"""白板可持久化图形项集合（canvas/items.py）。

每个图形项都实现 to_dict()/重建逻辑，并由 item_from_dict() 统一注册表重建，
因此 scene / 页面内容可以无损序列化为 JSON（.wbd 文件）。

图形族：

* :class:`StrokeItem` —— 手绘笔迹
* :class:`RectItem` —— 矩形 / 圆角矩形（``radius`` > 0 即圆角）
* :class:`EllipseItem` —— 椭圆 / 圆
* :class:`PolygonShapeItem` —— 三角形 / 菱形 / 五角星（按外接矩形生成路径）
* :class:`LineItem` —— 直线 / 单箭头 / 双向箭头

所有描边类图形都带 ``line_style``（solid / dash / dot / dash_dot），
虚线可以直接落在任意形状上，而不只是直线。
"""
from __future__ import annotations

import base64
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsTextItem,
)

from core.stroke import build_path, qcolor_from_rgba, qcolor_to_rgba

# 预览态标记：绘制过程中的临时图形项会带上它，序列化时自动跳过。
# 注意 QGraphicsItem.setData 的 key 必须是 int（不能是字符串）。
PREVIEW_KEY = 1000

# ------------------------------------------------------------------ 线型
# 名字 -> Qt 画笔样式；序列化只存名字，换 Qt 版本也稳。
LINE_STYLES = {
    "solid": Qt.PenStyle.SolidLine,
    "dash": Qt.PenStyle.DashLine,
    "dot": Qt.PenStyle.DotLine,
    "dash_dot": Qt.PenStyle.DashDotLine,
}
LINE_STYLE_LABELS = {
    "solid": "实线",
    "dash": "虚线",
    "dot": "点线",
    "dash_dot": "点划线",
}
DEFAULT_LINE_STYLE = "solid"

# Qt 画笔样式 -> 名字（反查用，只认标准样式）
_PEN_STYLE_NAMES = {
    Qt.PenStyle.SolidLine: "solid",
    Qt.PenStyle.DashLine: "dash",
    Qt.PenStyle.DotLine: "dot",
    Qt.PenStyle.DashDotLine: "dash_dot",
    Qt.PenStyle.DashDotDotLine: "dash_dot",
    Qt.PenStyle.CustomDashLine: "dash",
}


def line_style_name(style) -> str:
    """把 Qt 画笔样式或线型名字统一成线型名字（未知值退回实线）。"""
    if isinstance(style, str):
        return style if style in LINE_STYLES else DEFAULT_LINE_STYLE
    try:
        return _PEN_STYLE_NAMES.get(Qt.PenStyle(style), DEFAULT_LINE_STYLE)
    except (TypeError, ValueError):
        return DEFAULT_LINE_STYLE


def pen_style(style) -> Qt.PenStyle:
    """线型名字或 Qt 枚举 -> Qt.PenStyle。"""
    if isinstance(style, str):
        return LINE_STYLES.get(style, Qt.PenStyle.SolidLine)
    return Qt.PenStyle(style)


def mark_preview(item, preview: bool = True) -> None:
    item.setData(PREVIEW_KEY, bool(preview))


def is_preview(item) -> bool:
    return bool(item.data(PREVIEW_KEY))

# ------------------------------------------------------------------ 工具函数


def make_pen(color: QColor, width: float, style=DEFAULT_LINE_STYLE) -> QPen:
    """构造画笔；``style`` 可以是线型名字（"dash"）或 Qt.PenStyle。"""
    pen = QPen(QColor(color), float(width), pen_style(style))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def stroked_shape(path: QPainterPath, width: float, margin: float = 2.0) -> QPainterPath:
    """把路径按线宽“加粗”成可命中区域，便于点击/擦除细线。"""
    stroker = QPainterPathStroker()
    stroker.setWidth(max(1.0, width) + margin * 2)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path)


def pen_bounds(path: QPainterPath, width: float, slack: float = 1.0) -> QRectF:
    """按线宽给出**紧凑**的外接矩形（路径 + 半个线宽 + 1 像素余量）。

    为什么要自己算：Qt 6.11 里 ``QGraphicsPathItem`` / ``QGraphicsEllipseItem``
    的 ``boundingRect()`` 会在路径外再留约 ``1.5 × 线宽``（比真实墨迹宽），
    于是 ``scene.itemsBoundingRect()`` 偏大，导出 PNG 时四周留白会比设定的
    边距多出几像素（实测 24px 边距变成 26px）。

    画笔统一用 RoundJoin/RoundCap（见 :func:`make_pen`），不会出现斜接尖角
    戳出这个范围，所以「半个线宽 + 1px」足够安全。
    """
    margin = max(0.0, float(width)) / 2.0 + max(0.0, float(slack))
    return path.boundingRect().adjusted(-margin, -margin, margin, margin)


def rgba_list(color: QColor) -> list:
    return qcolor_to_rgba(color)


# ------------------------------------------------------------------ 笔画


class StrokeItem(QGraphicsPathItem):
    """一条手绘笔迹。

    内部保存原始采样点（``_points``），渲染路径由 :func:`core.stroke.build_path`
    用中点二次贝塞尔拟合生成：
    - 曲线平滑，不会出现折线感；
    - 序列化时直接写出原始点，不会因为路径含曲线段而丢点。

    线型跟随工具栏的「线型」选择，所以画笔也能画虚线/点线
    （线型存在画笔对象上，序列化写 ``line_style``）。

    ``dash_offset`` 记录「这条笔迹从原笔迹的哪个位置开始」：
    橡皮擦擦断虚线后，碎片带上这个偏移，虚线图案才不会在断口处重新起头
    （否则看起来像整段虚线往后退了一截）。
    """

    TYPE = "stroke"

    def __init__(self, points=None, color: QColor = None, thickness: float = 2.0,
                 pos: QPointF = None, line_style=DEFAULT_LINE_STYLE,
                 dash_offset: float = 0.0):
        pts = [QPointF(p) for p in (points or [])]
        super().__init__(build_path(pts))
        self._points = pts
        # 线型单独记一份：setDashOffset() 会把画笔样式改写成 CustomDashLine，
        # 之后就不能再靠 pen().style() 反推用户选的是点线还是虚线了。
        self._line_style = line_style_name(line_style)
        self._dash_offset = float(dash_offset or 0.0)
        if color is None:
            color = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(color, thickness, line_style))
        self._apply_dash_offset()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        if pos is not None:
            self.setPos(pos)

    # ---------------------------------------------------------- 数据接口
    def points(self) -> list:
        return list(self._points)

    def point_count(self) -> int:
        return len(self._points)

    def set_points(self, points) -> None:
        self._points = [QPointF(p) for p in points]
        self._rebuild()

    def add_point(self, point: QPointF) -> None:
        """追加一个采样点并重建路径（画笔绘制过程中调用）。"""
        self._points.append(QPointF(point))
        self._rebuild()

    def _rebuild(self) -> None:
        self.setPath(build_path(self._points))

    # ---------------------------------------------------------- 虚线相位
    def line_style(self) -> str:
        """用户选的线型名字（solid/dash/dot/dash_dot）。

        不能看 ``pen().style()``：设过 dashOffset 之后它统一变成 CustomDashLine。
        """
        return self._line_style

    def dash_offset(self) -> float:
        """虚线相位（场景单位 = 从原笔迹起点算起的弧长）。"""
        return self._dash_offset

    def set_dash_offset(self, offset: float) -> None:
        """设置虚线相位偏移（场景单位，等于「到原笔迹起点的弧长」）。"""
        self._dash_offset = float(offset or 0.0)
        self._apply_dash_offset()
        self.update()

    def _apply_dash_offset(self) -> None:
        """把相位写进画笔。

        两个坑：

        * ``QPen.setDashOffset()`` 的单位是**线宽**（和 ``setDashPattern`` 一致），
          不是像素 —— 直接传弧长会得到完全错误的相位（实测差几像素到整段虚线）。
          所以这里除以线宽换算成 Qt 的单位。
        * 设过 offset 之后画笔样式会变成 ``CustomDashLine``
          （内置虚线的图案被展开成 ``dashPattern()``），可以利用这一点
          拿到虚线周期，把偏移归一到 ``[0, 一个周期)``，免得长笔迹把值堆到几万。
        """
        pen = self.pen()
        width = pen.widthF() or 1.0
        if self._dash_offset:
            pen.setDashOffset(self._dash_offset / width)
            pattern = pen.dashPattern()
            period = sum(pattern) * width if pattern else 0.0
            if period > 0:
                normalized = self._dash_offset % period
                if abs(normalized - self._dash_offset) > 1e-9:
                    self._dash_offset = normalized
                    pen.setDashOffset(normalized / width)
        super().setPen(pen)

    # ---------------------------------------------------------- 绘制接口
    def shape(self) -> QPainterPath:  # 加粗命中区
        return stroked_shape(self.path(), self.pen().widthF())

    def to_dict(self) -> dict:
        return {
            "type": self.TYPE,
            "color": rgba_list(self.pen().color()),
            "thickness": round(self.pen().widthF(), 3),
            "line_style": self._line_style,
            "dash_offset": round(self._dash_offset, 3),
            "points": [[round(p.x(), 3), round(p.y(), 3)] for p in self._points],
            "pos": [self.pos().x(), self.pos().y()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StrokeItem":
        pts = [QPointF(x, y) for x, y in data.get("points", [])]
        item = cls(
            pts,
            qcolor_from_rgba(data.get("color", [0, 0, 0, 255])),
            float(data.get("thickness", 2.0)),
            line_style=data.get("line_style", DEFAULT_LINE_STYLE),
            dash_offset=float(data.get("dash_offset", 0.0)),
        )
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 描边形状公共部分


def _common_geometry_dict(item, kind: str) -> dict:
    """描边形状（矩形/椭圆/多边形）共有的序列化字段。"""
    r = item.rect()
    data = {
        "type": item.TYPE,
        "kind": kind,
        "x": round(r.x(), 3),
        "y": round(r.y(), 3),
        "w": round(r.width(), 3),
        "h": round(r.height(), 3),
        "pos": [item.pos().x(), item.pos().y()],
        "outline": rgba_list(item.pen().color()),
        "outline_width": round(item.pen().widthF(), 3),
        "line_style": line_style_name(item.pen().style()),
        "fill": rgba_list(item.brush().color())
        if item.brush().style() != Qt.BrushStyle.NoBrush else None,
    }
    radius = getattr(item, "radius_value", None)
    if callable(radius):        # 兼容写成方法的情况
        radius = radius()
    if radius is not None:
        data["radius"] = round(float(radius), 3)
    return data


def _apply_common_geometry(item, data: dict):
    """把序列化数据里的描边/填充/位置应用到已建好的图形项上。"""
    item.setPen(make_pen(qcolor_from_rgba(data.get("outline", [0, 0, 0, 255])),
                         float(data.get("outline_width", 2.0)),
                         data.get("line_style", DEFAULT_LINE_STYLE)))
    fill = data.get("fill")
    if fill:
        item.setBrush(QColor(*fill[:4]))
    else:
        item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
    item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    pos = data.get("pos")
    if pos:
        item.setPos(QPointF(pos[0], pos[1]))
    return item


def _data_rect(data: dict) -> QRectF:
    return QRectF(float(data.get("x", 0.0)), float(data.get("y", 0.0)),
                  float(data.get("w", 0.0)), float(data.get("h", 0.0)))


def rounded_rect_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    limit = min(rect.width(), rect.height()) / 2.0
    r = max(0.0, min(float(radius), limit))
    if r <= 0.01:
        path.addRect(rect)
    else:
        path.addRoundedRect(rect, r, r)
    return path


def default_radius(rect: QRectF) -> float:
    """圆角矩形的默认圆角半径：随尺寸缩放，小图形不会变成胶囊。"""
    return max(4.0, min(rect.width(), rect.height()) * 0.18)


class RectItem(QGraphicsPathItem):
    """矩形；``radius`` > 0 时就是圆角矩形。

    用路径实现（而不是 QGraphicsRectItem）是因为 QGraphicsRectItem 画不出圆角。
    ``TYPE`` 仍保持 "rect"，旧文件里的直角矩形照样能读进来。
    """

    TYPE = "rect"

    def __init__(self, rect: QRectF, outline: QColor = None, width: float = 2.0,
                 radius: float = 0.0, line_style=DEFAULT_LINE_STYLE):
        rect = QRectF(rect)
        super().__init__(rounded_rect_path(rect, radius))
        self._rect = rect
        self._radius = max(0.0, float(radius))
        if outline is None:
            outline = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(outline, width, line_style))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    # -- 几何 --
    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def radius_value(self) -> float:
        return self._radius

    def set_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(rect)
        self.setPath(rounded_rect_path(self._rect, self._radius))
        self.update()

    # QGraphicsItem 体系里习惯叫 setRect，这里保持同名别名
    setRect = set_rect

    def set_radius(self, radius: float) -> None:
        self.prepareGeometryChange()
        self._radius = max(0.0, float(radius))
        self.setPath(rounded_rect_path(self._rect, self._radius))
        self.update()

    def shape(self) -> QPainterPath:
        return stroked_shape(self.path(), self.pen().widthF())

    def boundingRect(self) -> QRectF:
        return pen_bounds(self.path(), self.pen().widthF())

    # -- 序列化 --
    def to_dict(self) -> dict:
        return _common_geometry_dict(self, "round_rect" if self._radius > 0 else "rect")

    @classmethod
    def from_dict(cls, data: dict) -> "RectItem":
        radius = float(data.get("radius", 0.0))
        if data.get("kind") == "round_rect" and radius <= 0:
            radius = default_radius(_data_rect(data))
        item = cls(_data_rect(data), radius=radius)
        return _apply_common_geometry(item, data)


class EllipseItem(QGraphicsEllipseItem):
    TYPE = "ellipse"

    def __init__(self, rect: QRectF, outline: QColor = None, width: float = 2.0,
                 line_style=DEFAULT_LINE_STYLE):
        super().__init__(QRectF(rect))
        if outline is None:
            outline = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(outline, width, line_style))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def set_rect(self, rect: QRectF) -> None:
        self.setRect(QRectF(rect))

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self.rect())
        return stroked_shape(path, self.pen().widthF())

    def boundingRect(self) -> QRectF:
        margin = self.pen().widthF() / 2.0 + 1.0
        return QRectF(self.rect()).adjusted(-margin, -margin, margin, margin)

    def to_dict(self) -> dict:
        return _common_geometry_dict(self, "ellipse")

    @classmethod
    def from_dict(cls, data: dict) -> "EllipseItem":
        item = cls(_data_rect(data))
        return _apply_common_geometry(item, data)


# 多边形形状：按外接矩形生成路径
POLYGON_KINDS = ("triangle", "diamond", "star")
POLYGON_LABELS = {"triangle": "三角形", "diamond": "菱形", "star": "五角星"}


def polygon_shape_path(kind: str, rect: QRectF) -> QPainterPath:
    """按外接矩形生成三角形/菱形/五角星的路径。"""
    cx, cy = rect.center().x(), rect.center().y()
    rx, ry = rect.width() / 2.0, rect.height() / 2.0
    path = QPainterPath()
    if kind == "triangle":
        vertices = [(cx, rect.top()), (rect.right(), rect.bottom()),
                    (rect.left(), rect.bottom())]
    elif kind == "diamond":
        vertices = [(cx, rect.top()), (rect.right(), cy),
                    (cx, rect.bottom()), (rect.left(), cy)]
    elif kind == "star":
        vertices = []
        for i in range(10):
            factor = 1.0 if i % 2 == 0 else 0.42
            angle = -math.pi / 2 + i * math.pi / 5
            vertices.append((cx + rx * factor * math.cos(angle),
                             cy + ry * factor * math.sin(angle)))
    else:
        raise ValueError(f"不支持的多边形类型: {kind}")
    path.moveTo(*vertices[0])
    for point in vertices[1:]:
        path.lineTo(*point)
    path.closeSubpath()
    return path


class PolygonShapeItem(QGraphicsPathItem):
    """三角形 / 菱形 / 五角星（统一按外接矩形定义，方便拖拽绘制）。"""

    TYPE = "polygon"

    def __init__(self, kind: str, rect: QRectF, outline: QColor = None,
                 width: float = 2.0, line_style=DEFAULT_LINE_STYLE):
        if kind not in POLYGON_KINDS:
            raise ValueError(f"不支持的多边形类型: {kind}")
        rect = QRectF(rect)
        super().__init__(polygon_shape_path(kind, rect))
        self._kind = kind
        self._rect = rect
        if outline is None:
            outline = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(outline, width, line_style))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def kind(self) -> str:
        return self._kind

    def set_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(rect)
        self.setPath(polygon_shape_path(self._kind, self._rect))
        self.update()

    setRect = set_rect

    def shape(self) -> QPainterPath:
        return stroked_shape(self.path(), self.pen().widthF())

    def boundingRect(self) -> QRectF:
        return pen_bounds(self.path(), self.pen().widthF())

    def to_dict(self) -> dict:
        return _common_geometry_dict(self, self._kind)

    @classmethod
    def from_dict(cls, data: dict) -> "PolygonShapeItem":
        kind = data.get("kind", "triangle")
        if kind not in POLYGON_KINDS:
            kind = "triangle"
        item = cls(kind, _data_rect(data))
        return _apply_common_geometry(item, data)


# ------------------------------------------------------------------ 直线 / 箭头

# 箭头形态：无 / 终点箭头 / 双向箭头
ARROW_NONE, ARROW_END, ARROW_BOTH = "none", "end", "both"


class LineItem(QGraphicsItem):
    """带可选箭头的直线（支持实线/虚线等线型与双向箭头）。

    几何存储在场景坐标中（item 自身 pos 保持 (0,0)），
    移动通过 setPos 完成，因此序列化记录 pos 即可。
    """

    TYPE = "line"

    def __init__(self, start: QPointF, end: QPointF, outline: QColor,
                 width: float, arrow=ARROW_NONE, line_style=DEFAULT_LINE_STYLE):
        super().__init__()
        self._p1 = QPointF(start)
        self._p2 = QPointF(end)
        self._arrow = _normalize_arrow(arrow)
        self._pen = make_pen(outline, width, line_style)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(0)

    # -- 几何 --
    def start(self) -> QPointF:
        return self._p1

    def end(self) -> QPointF:
        return self._p2

    def arrow(self) -> str:
        return self._arrow

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self.prepareGeometryChange()
        self._p1 = QPointF(start)
        self._p2 = QPointF(end)
        self.update()

    def set_pen(self, pen: QPen) -> None:
        """替换画笔（用于预览态的虚线笔，以及切换线宽/线型/颜色）。"""
        self.prepareGeometryChange()
        self._pen = QPen(pen)
        self.update()

    def _head_at(self, tip_point: QPointF, direction: QPointF) -> QPolygonF:
        """按线宽缩放箭头头大小，返回以 ``tip_point`` 为顶点、指向 ``direction`` 的三角形。"""
        width = self._pen.widthF()
        size = max(10.0, width * 3.2)
        dx, dy = direction.x(), direction.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return QPolygonF()
        ux, uy = dx / length, dy / length          # 单位方向
        px, py = -uy, ux                           # 垂直单位
        tip = QPointF(tip_point.x() - ux * width * 0.6,
                      tip_point.y() - uy * width * 0.6)
        base = QPointF(tip.x() - ux * size, tip.y() - uy * size)
        return QPolygonF([
            tip,
            QPointF(base.x() + px * size * 0.45, base.y() + py * size * 0.45),
            QPointF(base.x() - px * size * 0.45, base.y() - py * size * 0.45),
        ])

    def arrow_heads(self) -> list:
        """返回所有箭头三角形（终点 / 起点）。"""
        heads = []
        delta = self._p2 - self._p1
        if self._arrow in (ARROW_END, ARROW_BOTH):
            heads.append(self._head_at(self._p2, delta))
        if self._arrow == ARROW_BOTH:
            heads.append(self._head_at(self._p1, -delta))
        return [h for h in heads if not h.isEmpty()]

    # -- QGraphicsItem 接口 --
    def boundingRect(self) -> QRectF:
        margin = self._pen.widthF() / 2 + 12
        x1, y1 = self._p1.x(), self._p1.y()
        x2, y2 = self._p2.x(), self._p2.y()
        return QRectF(min(x1, x2) - margin, min(y1, y2) - margin,
                      abs(x2 - x1) + margin * 2, abs(y2 - y1) + margin * 2)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(self._p1)
        path.lineTo(self._p2)
        for head in self.arrow_heads():
            path.addPolygon(head)
        return stroked_shape(path, self._pen.widthF())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen)
        painter.drawLine(self._p1, self._p2)
        heads = self.arrow_heads()
        if heads:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._pen.color())
            for head in heads:
                painter.drawPolygon(head)

    # -- 序列化 --
    def to_dict(self) -> dict:
        return {
            "type": self.TYPE,
            "x1": round(self._p1.x(), 3), "y1": round(self._p1.y(), 3),
            "x2": round(self._p2.x(), 3), "y2": round(self._p2.y(), 3),
            "pos": [self.pos().x(), self.pos().y()],
            "outline": rgba_list(self._pen.color()),
            "outline_width": round(self._pen.widthF(), 3),
            "line_style": line_style_name(self._pen.style()),
            "arrow": self._arrow,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LineItem":
        item = cls(
            QPointF(data.get("x1", 0.0), data.get("y1", 0.0)),
            QPointF(data.get("x2", 0.0), data.get("y2", 0.0)),
            qcolor_from_rgba(data.get("outline", [0, 0, 0, 255])),
            float(data.get("outline_width", 2.0)),
            data.get("arrow", ARROW_NONE),
            data.get("line_style", DEFAULT_LINE_STYLE),
        )
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


def _normalize_arrow(value) -> str:
    """兼容旧文件的布尔箭头标记（True -> 终点箭头）。"""
    if value is True:
        return ARROW_END
    if value in (False, None):
        return ARROW_NONE
    return value if value in (ARROW_NONE, ARROW_END, ARROW_BOTH) else ARROW_NONE


# ------------------------------------------------------------------ 文字

# 文本对齐：左 / 居中 / 右
TEXT_ALIGNS = ("left", "center", "right")


class TextItem(QGraphicsTextItem):
    """文字块：字体、字号、粗斜体、颜色、对齐、自动换行（固定文本宽度）都可自定义。

    自动换行的实现是 ``setTextWidth(w)``：Qt 会在 ``w`` 宽度内折行，
    因此换行宽度本身也要序列化，重新打开才能保持同样的排版。
    """

    TYPE = "text"

    def __init__(self, text: str, color: QColor, pixel_size: float = 16.0,
                 family: str = "", bold: bool = False, italic: bool = False,
                 wrap: bool = False, text_width: float = 300.0, align: str = "left",
                 font_file: str = ""):
        super().__init__(text)
        self._font_file = font_file or ""
        self.setDefaultTextColor(QColor(color))
        self._wrap = bool(wrap)
        self._text_width = float(text_width)
        font = QFont(family) if family else QFont()
        font.setPixelSize(int(max(6, pixel_size)))
        font.setBold(bool(bold))
        font.setItalic(bool(italic))
        self.setFont(font)
        self.set_text_align(align)
        self._apply_wrap()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    # -- 排版 --
    def _apply_wrap(self) -> None:
        self.setTextWidth(self._text_width if self._wrap else -1.0)

    def is_wrapped(self) -> bool:
        return self._wrap

    def text_width(self) -> float:
        return self._text_width

    def set_wrap(self, wrap: bool, text_width: float = None) -> None:
        self._wrap = bool(wrap)
        if text_width is not None:
            self._text_width = max(20.0, float(text_width))
        self._apply_wrap()

    def text_align(self) -> str:
        return getattr(self, "_align", "left")

    def set_text_align(self, align: str) -> None:
        align = align if align in TEXT_ALIGNS else "left"
        self._align = align
        option = self.document().defaultTextOption()
        option.setAlignment({
            "left": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
        }[align] | Qt.AlignmentFlag.AlignTop)
        self.document().setDefaultTextOption(option)

    def set_text_format(self, fmt) -> None:
        """用 :class:`core.text_format.TextFormat` 更新排版（编辑已有文字时用）。"""
        font = QFont(fmt.family) if fmt.family else QFont()
        font.setPixelSize(int(max(6, fmt.pixel_size)))
        font.setBold(bool(fmt.bold))
        font.setItalic(bool(fmt.italic))
        self.setFont(font)
        self.setDefaultTextColor(QColor(fmt.color))
        self._font_file = getattr(fmt, "font_file", "") or ""
        self.set_text_align(fmt.align)
        self.set_wrap(fmt.wrap, fmt.text_width)

    def font_family(self) -> str:
        return self.font().family()

    def font_file(self) -> str:
        return self._font_file

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        """文字块的显示区域（场景坐标）。

        用 ``boundingRect`` 而不是 ``document().size()``：前者已经把字体、
        换行宽度与对齐都算进去了。
        """
        return self.mapRectToScene(self.boundingRect())

    def resize_state(self) -> dict:
        font = self.font()
        return {
            "pixel_size": int(font.pixelSize()),
            "text_width": round(self._text_width, 3),
            "pos": [self.pos().x(), self.pos().y()],
        }

    def restore_resize_state(self, state: dict) -> None:
        font = self.font()
        font.setPixelSize(int(max(6, state.get("pixel_size", font.pixelSize()))))
        self.setFont(font)
        if state.get("text_width") is not None:
            self._text_width = max(20.0, float(state["text_width"]))
            self._apply_wrap()
        pos = state.get("pos")
        if pos:
            self.setPos(QPointF(pos[0], pos[1]))
        self.update()

    def resize_with(self, index: int, point: QPointF, keep_aspect: bool = False) -> None:
        """拖手柄缩放文字：改字号（等比），勾了自动换行时折行宽度一起缩放。

        文字不能像图片那样横向拉扁（字形会变形），所以这里始终等比：
        按手柄所在方向算出倍数，取字号能落到的整数值，再反算出实际倍数，
        把锚点（对角 / 对边）固定在原位。
        """
        from canvas.resize import BOTTOM_HANDLES, LEFT_HANDLES, RIGHT_HANDLES, TOP_HANDLES
        from canvas.resize import anchor_point, resized_rect

        rect = self.resize_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        target = resized_rect(rect, index, point, keep_aspect)

        factors = []
        if index in LEFT_HANDLES or index in RIGHT_HANDLES:
            factors.append(target.width() / rect.width())
        if index in TOP_HANDLES or index in BOTTOM_HANDLES:
            factors.append(target.height() / rect.height())
        factor = max(factors) if factors else 1.0

        font = self.font()
        old_size = max(6, font.pixelSize())
        new_size = int(max(6, min(400, round(old_size * factor))))
        if new_size == old_size:
            return
        # 用取整后的字号反算真实倍数，保证锚点算得准
        actual = new_size / float(old_size)

        anchor = anchor_point(rect, index)
        origin = self.pos()
        self.setPos(QPointF(anchor.x() - (anchor.x() - origin.x()) * actual,
                            anchor.y() - (anchor.y() - origin.y()) * actual))
        font.setPixelSize(new_size)
        self.setFont(font)
        if self._wrap:
            self._text_width = max(20.0, self._text_width * actual)
            self._apply_wrap()
        self.update()

    def to_dict(self) -> dict:
        font = self.font()
        return {
            "type": self.TYPE,
            "pos": [self.pos().x(), self.pos().y()],
            "text": self.toPlainText(),
            "color": rgba_list(self.defaultTextColor()),
            "font_family": font.family(),
            "pixel_size": font.pixelSize(),
            "bold": font.bold(),
            "italic": font.italic(),
            "wrap": self._wrap,
            "text_width": round(self._text_width, 3),
            "align": getattr(self, "_align", "left"),
            # 导入字体记录来源文件：换台机器打开时能提示字体来自哪里
            "font_file": self._font_file,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TextItem":
        item = cls(
            data.get("text", ""),
            qcolor_from_rgba(data.get("color", [0, 0, 0, 255])),
            float(data.get("pixel_size", 16.0)),
            family=data.get("font_family", ""),
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            wrap=bool(data.get("wrap", False)),
            text_width=float(data.get("text_width", 300.0)),
            align=data.get("align", "left"),
            font_file=data.get("font_file", ""),
        )
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 图片


class ImageItem(QGraphicsPixmapItem):
    """导入的图片：可自由伸缩（四边拉伸 / 四角等比）。

    原始位图保持不变，显示尺寸靠 ``QTransform`` 的缩放（``sx`` / ``sy``）实现，
    所以放大不会丢像素、缩小也不破坏原图；缩放比例会写进 ``.wbd``。
    """

    TYPE = "image"
    MIN_SCALE = 0.02
    MAX_SCALE = 50.0

    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        """当前显示区域（场景坐标）。"""
        return self.mapRectToScene(QRectF(self.pixmap().rect()))

    def resize_state(self) -> dict:
        transform = self.transform()
        return {
            "sx": round(transform.m11(), 6),
            "sy": round(transform.m22(), 6),
            "pos": [self.pos().x(), self.pos().y()],
        }

    def restore_resize_state(self, state: dict) -> None:
        self.setTransform(QTransform.fromScale(float(state.get("sx", 1.0)),
                                               float(state.get("sy", 1.0))))
        pos = state.get("pos")
        if pos:
            self.setPos(QPointF(pos[0], pos[1]))

    def resize_with(self, index: int, point: QPointF, keep_aspect: bool = False) -> None:
        from canvas.resize import resized_rect

        rect = self.resize_rect()
        target = resized_rect(rect, index, point, keep_aspect)
        pixmap_rect = self.pixmap().rect()
        if pixmap_rect.width() <= 0 or pixmap_rect.height() <= 0:
            return
        sx = target.width() / pixmap_rect.width()
        sy = target.height() / pixmap_rect.height()
        sx = max(self.MIN_SCALE, min(self.MAX_SCALE, sx))
        sy = max(self.MIN_SCALE, min(self.MAX_SCALE, sy))
        self.prepareGeometryChange()
        self.setTransform(QTransform.fromScale(sx, sy))
        self.setPos(target.topLeft())
        self.update()

    def to_dict(self) -> dict:
        image = self.pixmap().toImage()
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        transform = self.transform()
        return {
            "type": self.TYPE,
            "pos": [self.pos().x(), self.pos().y()],
            "data": bytes(ba.toBase64()).decode("ascii"),
            "w": self.pixmap().width(),
            "h": self.pixmap().height(),
            "sx": round(transform.m11(), 6),
            "sy": round(transform.m22(), 6),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageItem":
        raw = base64.b64decode(data.get("data", ""))
        pix = QPixmap()
        pix.loadFromData(raw, "PNG")
        item = cls(pix)
        item.setTransform(QTransform.fromScale(float(data.get("sx", 1.0)),
                                               float(data.get("sy", 1.0))))
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 工厂


_TYPE_MAP = {
    StrokeItem.TYPE: StrokeItem,
    RectItem.TYPE: RectItem,
    EllipseItem.TYPE: EllipseItem,
    PolygonShapeItem.TYPE: PolygonShapeItem,
    LineItem.TYPE: LineItem,
    TextItem.TYPE: TextItem,
    ImageItem.TYPE: ImageItem,
}

ITEM_TYPES = tuple(_TYPE_MAP.keys())


def item_from_dict(data: dict):
    """按序列化 dict 重建图形项；未知类型或坏数据返回 None。"""
    cls = _TYPE_MAP.get(data.get("type"))
    if cls is None:
        return None
    try:
        return cls.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None
