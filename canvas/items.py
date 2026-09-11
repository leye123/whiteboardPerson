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
    QTextOption,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsItemGroup,
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
#
# 注意：``CustomDashLine`` 是**无法反推**的 —— 设过 ``setDashOffset()`` 之后
# 任何虚线线型都会变成它，所以它只能给一个保守的默认值。
# 需要"用户到底选了哪种线型"时，请用 :meth:`StrokeItem.line_style()`，
# 别用这个函数去猜（橡皮擦曾经因此把点线/点划线擦成虚线）。
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

        三个坑（第一个曾让实线笔迹在擦除后**整段隐形**）：

        * **实线不能设相位**：``QPen.setDashOffset()`` 会把样式切成
          ``CustomDashLine`` 并且**留下空图案**，而「空图案的自定义虚线」
          Qt 什么都不画 —— 实线碎片于是隐形；拖橡皮时碎片不断重建，
          有的有墨有的没墨，用户看到的就是「闪烁」。这里只对真正有虚线图案的
          线型（dash/dot/dash_dot）设置相位。
        * ``setDashOffset()`` 的单位是**线宽**（和 ``setDashPattern`` 一致），
          不是像素 —— 直接传弧长会得到完全错误的相位。
        * 设过 offset 之后 ``pen().style()`` 变成 ``CustomDashLine``，
          所以线型单独记在 ``_line_style`` 上；顺便用展开后的图案算出周期，
          把偏移归一到 ``[0, 一个周期)``。
        """
        pen = QPen(self.pen())
        width = pen.widthF() or 1.0
        offset = self._dash_offset if self._line_style != "solid" else 0.0
        if offset:
            candidate = QPen(pen)
            candidate.setDashOffset(offset / width)
            pattern = candidate.dashPattern()
            if not pattern:
                # 兜底：空图案的虚线画不出任何东西，宁可放弃相位（画成实线才对）
                offset = 0.0
            else:
                period = sum(pattern) * width
                if period > 0:
                    offset %= period
                    candidate = QPen(pen)
                    candidate.setDashOffset(offset / width)
                pen = candidate
        self._dash_offset = offset
        super().setPen(pen)

    # ---------------------------------------------------------- 绘制接口
    def shape(self) -> QPainterPath:  # 加粗命中区
        return stroked_shape(self.path(), self.pen().widthF())

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        return self.mapRectToScene(self.boundingRect())

    def resize_state(self) -> dict:
        return transform_state(self)

    def restore_resize_state(self, state: dict) -> None:
        apply_transform_state(self, state)

    def resize_with(self, index: int, point: QPointF,
                    keep_aspect: bool = False) -> None:
        # 笔迹用 transform 缩放：采样点不动，线宽跟着一起变粗/变细，符合手写的感觉
        resize_by_transform(self, index, point, keep_aspect)

    def scene_scale(self) -> float:
        """当前「局部 → 场景」的平均缩放倍数（橡皮擦换算半径用）。"""
        return item_scene_scale(self)

    # ---------------------------------------------------------- 样式快照 / 单项设置
    def outline_color(self) -> QColor:
        return QColor(self.pen().color())

    def outline_width(self) -> float:
        return float(self.pen().widthF())

    def current_line_style(self) -> str:
        return self._line_style

    def set_outline_color(self, color) -> None:
        pen = self.pen()
        pen.setColor(QColor(color))
        self.setPen(pen)
        self._apply_dash_offset()
        self.update()

    def set_outline_width(self, width: float) -> None:
        pen = self.pen()
        pen.setWidthF(max(0.1, float(width)))
        self.setPen(pen)
        self._apply_dash_offset()
        self.update()

    def set_line_style(self, style) -> None:
        """切换线型（参数侧边栏用）：相位保持不变，只换图案。"""
        self._line_style = line_style_name(style)
        pen = self.pen()
        pen.setStyle(pen_style(self._line_style))
        self.setPen(pen)
        self._apply_dash_offset()
        self.update()

    def style_state(self) -> dict:
        pen = self.pen()
        return {
            "color": rgba_list(pen.color()),
            "thickness": round(pen.widthF(), 3),
            "line_style": self._line_style,
            "dash_offset": round(self._dash_offset, 3),
        }

    def restore_style_state(self, state: dict) -> None:
        if not state:
            return
        pen = self.pen()
        if state.get("color") is not None:
            pen.setColor(qcolor_from_rgba(state["color"]))
        if state.get("thickness") is not None:
            pen.setWidthF(max(0.1, float(state["thickness"])))
        if state.get("line_style") is not None:
            self._line_style = line_style_name(state["line_style"])
            pen.setStyle(pen_style(self._line_style))
        self.setPen(pen)
        if state.get("dash_offset") is not None:
            self._dash_offset = float(state["dash_offset"] or 0.0)
        self._apply_dash_offset()
        self.update()

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
        # 图形内部的文字标签（含字体数据）：没有就写 null
        "label": dict(item.raw_label()) if getattr(item, "raw_label", None)
        and item.raw_label() else None,
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
    label = data.get("label")
    if hasattr(item, "set_raw_label"):
        item.set_raw_label(dict(label) if label else None)
    item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    pos = data.get("pos")
    if pos:
        item.setPos(QPointF(pos[0], pos[1]))
    return item


def _data_rect(data: dict) -> QRectF:
    return QRectF(float(data.get("x", 0.0)), float(data.get("y", 0.0)),
                  float(data.get("w", 0.0)), float(data.get("h", 0.0)))


# ------------------------------------------------------------------ 图形内文字标签

def make_label(text: str = "", family: str = "", pixel_size: float = 16.0,
               color=None, bold: bool = False, italic: bool = False,
               align: str = "center") -> dict:
    """图形内部的文字标签（也用 TextFormat 那套字段，参数侧边栏可直接复用）。"""
    return {
        "text": str(text or ""),
        "font_family": str(family or ""),
        "pixel_size": float(pixel_size),
        "color": rgba_list(QColor(color) if color is not None
                           else QColor(Qt.GlobalColor.black)),
        "bold": bool(bold),
        "italic": bool(italic),
        "align": align if align in ("left", "center", "right") else "center",
    }


def paint_label(painter: QPainter, rect: QRectF, label: dict) -> None:
    """在图形内部居中绘制标签文字（自动折行、垂直居中）。

    图形项自己的 ``paint()`` 里调用：画笔已经在图形项的局部坐标系里，
    所以直接用图形的 rect 即可；标签不会影响图形项的 boundingRect/命中测试。
    """
    if not label:
        return
    text = str(label.get("text", ""))
    if not text.strip():
        return
    font = QFont(str(label.get("font_family", "")) or QFont().family())
    font.setPixelSize(int(max(6, float(label.get("pixel_size", 16.0)))))
    font.setBold(bool(label.get("bold", False)))
    font.setItalic(bool(label.get("italic", False)))
    color = qcolor_from_rgba(label.get("color", [0, 0, 0, 255]))
    align = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }.get(str(label.get("align", "center")), Qt.AlignmentFlag.AlignHCenter)

    painter.save()
    painter.setPen(QPen(QColor(color)))
    painter.setFont(font)
    option = QTextOption(align | Qt.AlignmentFlag.AlignVCenter)
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    painter.drawText(QRectF(rect).adjusted(4.0, 4.0, -4.0, -4.0), text, option)
    painter.restore()


def label_text(label: dict) -> str:
    return str((label or {}).get("text", ""))


def shape_style_state(item) -> dict:
    """描边形状（矩形/椭圆/多边形）的样式快照：描边、线宽、线型、填充、标签。"""
    return {
        "outline": rgba_list(item.pen().color()),
        "outline_width": round(item.pen().widthF(), 3),
        "line_style": line_style_name(item.pen().style()),
        "fill": rgba_list(item.brush().color())
        if item.brush().style() != Qt.BrushStyle.NoBrush else None,
        "label": dict(item.raw_label()) if item.raw_label() else None,
    }


def apply_shape_style(item, state: dict) -> None:
    if not state:
        return
    if state.get("outline") is not None:
        pen = item.pen()
        pen.setColor(qcolor_from_rgba(state["outline"]))
        item.setPen(pen)
    if state.get("outline_width") is not None:
        pen = item.pen()
        pen.setWidthF(max(0.1, float(state["outline_width"])))
        item.setPen(pen)
    if state.get("line_style") is not None:
        pen = item.pen()
        pen.setStyle(pen_style(state["line_style"]))
        item.setPen(pen)
    if "fill" in state:
        fill = state.get("fill")
        item.setBrush(QColor(*fill[:4]) if fill else QBrush(Qt.BrushStyle.NoBrush))
    if "label" in state:
        item.set_raw_label(dict(state["label"]) if state["label"] else None)
    item.update()


def local_rect_for(item, target: QRectF) -> QRectF:
    """把「场景坐标下的目标矩形」换算成图形项的局部矩形。

    ``sceneTransform()`` 已经把父项（例如组合）与自身的变换都算进去了，
    所以组合被缩放之后，里面的图形项照样能算对。
    """
    inverse, invertible = item.sceneTransform().inverted()
    return inverse.mapRect(QRectF(target)) if invertible else QRectF(target)


def item_scene_scale(item) -> float:
    """图形项当前「局部 → 场景」的平均缩放倍数（橡皮擦换算半径用）。"""
    determinant = item.sceneTransform().determinant()
    return abs(determinant) ** 0.5 or 1.0


def rect_state(item) -> dict:
    """缩放快照：局部矩形 + 位置（撤销用）。"""
    r = item.rect()
    return {"rect": [round(r.x(), 3), round(r.y(), 3),
                     round(r.width(), 3), round(r.height(), 3)],
            "pos": [item.pos().x(), item.pos().y()]}


def apply_rect_state(item, state: dict) -> None:
    rect = state.get("rect")
    if rect:
        item.set_rect(QRectF(float(rect[0]), float(rect[1]),
                             float(rect[2]), float(rect[3])))
    pos = state.get("pos")
    if pos:
        item.setPos(QPointF(float(pos[0]), float(pos[1])))


def resize_geometry(item, index: int, point: QPointF, keep_aspect: bool = False,
                    on_radius=None) -> None:
    """按手柄拖动结果改图形项的局部矩形（线宽/线型保持不变）。

    形状（矩形/椭圆/多边形）都是「按几何缩放」：改 rect，而不是套 transform ——
    这样边框线宽不会被拉粗，圆角半径也能按比例跟着变。
    """
    from canvas.resize import resized_rect

    scene_rect = item.resize_rect()
    if scene_rect.width() <= 0 or scene_rect.height() <= 0:
        return
    target = resized_rect(scene_rect, index, point, keep_aspect)
    if on_radius is not None:
        # 圆角半径按「横纵缩放倍数的几何平均」变：等比缩放时刚好成正比，
        # 只往一个方向拉时缓慢变大（完全不变会让大图形看起来几乎没有圆角）
        ratio_x = target.width() / scene_rect.width()
        ratio_y = target.height() / scene_rect.height()
        on_radius((ratio_x * ratio_y) ** 0.5)
    item.set_rect(local_rect_for(item, target))


def transform_state(item) -> dict:
    """缩放快照（transform 版，用于笔迹/图片/组合）。"""
    transform = item.transform()
    return {"sx": round(transform.m11(), 6), "sy": round(transform.m22(), 6),
            "pos": [item.pos().x(), item.pos().y()]}


def apply_transform_state(item, state: dict) -> None:
    item.setTransform(QTransform.fromScale(float(state.get("sx", 1.0)),
                                           float(state.get("sy", 1.0))))
    pos = state.get("pos")
    if pos:
        item.setPos(QPointF(float(pos[0]), float(pos[1])))


def resize_by_transform(item, index: int, point: QPointF,
                        keep_aspect: bool = False) -> None:
    """按手柄拖动结果给图形项套一个缩放 transform（笔迹/图片/组合用）。

    用 transform 而不是改几何：笔迹的采样点、组合里的子项都保持原样，
    线宽与内容会跟着一起放大，看起来才自然。
    """
    from canvas.resize import resized_rect

    scene_rect = item.resize_rect()
    if scene_rect.width() <= 0 or scene_rect.height() <= 0:
        return
    target = resized_rect(scene_rect, index, point, keep_aspect)
    old = item.transform()
    sx = old.m11() * target.width() / scene_rect.width()
    sy = old.m22() * target.height() / scene_rect.height()
    local = QRectF(item.boundingRect())
    item.prepareGeometryChange()
    item.setTransform(QTransform.fromScale(sx, sy))
    # 局部左上角要跟着补回来：否则内容会整体偏移（局部原点通常不在左上角）
    item.setPos(QPointF(target.left() - local.left() * sx,
                        target.top() - local.top() * sy))
    item.update()


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


class _ShapeLabelMixin:
    """图形内文字标签 + 样式快照（矩形 / 椭圆 / 多边形共用）。

    标签只影响绘制，不进入 ``boundingRect()``/``shape()`` ——
    图形的尺寸仍由几何决定，标签按框内居中排版，放大缩小都跟着走。
    """

    _label = None                    # 类属性兜底，子类不用专门初始化

    # -- 标签 --
    def raw_label(self) -> dict:
        return dict(self._label) if self._label else {}

    def set_raw_label(self, label) -> None:
        self._label = dict(label) if label else None
        self.update()

    def label_text(self) -> str:
        return label_text(self._label)

    def set_label_text(self, text: str) -> None:
        if not self._label:
            self._label = make_label()
        self._label["text"] = str(text or "")
        self.update()

    # -- 绘制：先画图形本身，再把标签叠在上面 --
    def paint(self, painter: QPainter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        paint_label(painter, self.rect(), self._label)

    # -- 样式快照（参数侧边栏 / 撤销）--
    def style_state(self) -> dict:
        return shape_style_state(self)

    def restore_style_state(self, state: dict) -> None:
        apply_shape_style(self, state)

    # -- 单项设置 / 读取（参数侧边栏用）--
    def outline_color(self) -> QColor:
        return QColor(self.pen().color())

    def outline_width(self) -> float:
        return float(self.pen().widthF())

    def current_line_style(self) -> str:
        return line_style_name(self.pen().style())

    def fill_color(self):
        """填充色；没有填充时返回 None。"""
        if self.brush().style() == Qt.BrushStyle.NoBrush:
            return None
        return QColor(self.brush().color())

    # -- 单项设置（参数侧边栏实时改）--
    def set_outline_color(self, color) -> None:
        pen = self.pen()
        pen.setColor(QColor(color))
        self.setPen(pen)
        self.update()

    def set_outline_width(self, width: float) -> None:
        pen = self.pen()
        pen.setWidthF(max(0.1, float(width)))
        self.setPen(pen)
        self.update()

    def set_line_style(self, style) -> None:
        pen = self.pen()
        pen.setStyle(pen_style(style))
        self.setPen(pen)
        self.update()

    def set_fill_color(self, color) -> None:
        self.setBrush(QColor(color) if color is not None
                      else QBrush(Qt.BrushStyle.NoBrush))
        self.update()

    # -- 命中区 --
    def interior_path(self):
        """闭合图形"内部"的路径；``None`` 表示只有描边能点中。

        v1.3.0：图形默认**不填充**，``shape()`` 只包含描边那一圈，
        于是"点在矩形中间"什么都点不到 —— 用户想选中它、双击进去写文字
        全都没反应。选择工具因此额外用这个路径判定（见
        :meth:`canvas.scene.PageScene.items_at` 的 ``interior`` 参数），
        橡皮擦仍按 ``shape()`` 精确判定，避免点一下空白就把大框整块擦掉。
        """
        return None


class RectItem(_ShapeLabelMixin, QGraphicsPathItem):
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

    def interior_path(self):
        return self.path()

    def boundingRect(self) -> QRectF:
        return pen_bounds(self.path(), self.pen().widthF())

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        return self.mapRectToScene(self.rect())

    def resize_state(self) -> dict:
        state = rect_state(self)
        state["radius"] = round(self._radius, 3)
        return state

    def restore_resize_state(self, state: dict) -> None:
        self._radius = max(0.0, float(state.get("radius", self._radius)))
        apply_rect_state(self, state)

    def resize_with(self, index: int, point: QPointF,
                    keep_aspect: bool = False) -> None:
        # 圆角半径按比例跟着缩放，否则拉大之后圆角就看不见了
        resize_geometry(self, index, point, keep_aspect,
                        on_radius=self._scale_radius)

    def _scale_radius(self, ratio: float) -> None:
        if self._radius > 0:
            self._radius = max(1.0, self._radius * ratio)

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


class EllipseItem(_ShapeLabelMixin, QGraphicsEllipseItem):
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

    def interior_path(self):
        path = QPainterPath()
        path.addEllipse(self.rect())
        return path

    def boundingRect(self) -> QRectF:
        margin = self.pen().widthF() / 2.0 + 1.0
        return QRectF(self.rect()).adjusted(-margin, -margin, margin, margin)

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        return self.mapRectToScene(self.rect())

    def resize_state(self) -> dict:
        return rect_state(self)

    def restore_resize_state(self, state: dict) -> None:
        apply_rect_state(self, state)

    def resize_with(self, index: int, point: QPointF,
                    keep_aspect: bool = False) -> None:
        resize_geometry(self, index, point, keep_aspect)

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


class PolygonShapeItem(_ShapeLabelMixin, QGraphicsPathItem):
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

    def interior_path(self):
        return self.path()

    def boundingRect(self) -> QRectF:
        return pen_bounds(self.path(), self.pen().widthF())

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        return self.mapRectToScene(self.rect())

    def resize_state(self) -> dict:
        return rect_state(self)

    def restore_resize_state(self, state: dict) -> None:
        apply_rect_state(self, state)

    def resize_with(self, index: int, point: QPointF,
                    keep_aspect: bool = False) -> None:
        resize_geometry(self, index, point, keep_aspect)

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

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def resize_rect(self) -> QRectF:
        """线段的包围盒。

        水平/垂直线的包围盒某个方向为 0，8 个手柄会叠在一起没法抓，
        所以给一个最小可视厚度（缩放计算用同一个矩形，不影响结果）。
        """
        rect = QRectF(QPointF(self._p1), QPointF(self._p2)).normalized()
        margin = self._pen.widthF() / 2.0 + 1.0
        rect = rect.adjusted(-margin, -margin, margin, margin)
        minimum = 6.0
        if rect.height() < minimum:
            grow = (minimum - rect.height()) / 2.0
            rect.adjust(0.0, -grow, 0.0, grow)
        if rect.width() < minimum:
            grow = (minimum - rect.width()) / 2.0
            rect.adjust(-grow, 0.0, grow, 0.0)
        return self.mapRectToScene(rect)

    def resize_state(self) -> dict:
        return {
            "p1": [self._p1.x(), self._p1.y()],
            "p2": [self._p2.x(), self._p2.y()],
            "pos": [self.pos().x(), self.pos().y()],
        }

    def restore_resize_state(self, state: dict) -> None:
        p1 = state.get("p1")
        p2 = state.get("p2")
        if p1 and p2:
            self.set_endpoints(QPointF(float(p1[0]), float(p1[1])),
                               QPointF(float(p2[0]), float(p2[1])))
        pos = state.get("pos")
        if pos:
            self.setPos(QPointF(float(pos[0]), float(pos[1])))

    def resize_with(self, index: int, point: QPointF,
                    keep_aspect: bool = False) -> None:
        """缩放线段：两个端点按同一比例缩放（线宽不变）。

        几何换算基于**紧贴端点的矩形**，而不是带画笔余量的手柄框 ——
        否则那条固定宽度的余量会被一起缩放，线段两端会往里缩几像素。
        """
        from canvas.resize import (BOTTOM_HANDLES, LEFT_HANDLES, RIGHT_HANDLES,
                                   TOP_HANDLES, resized_rect)

        scene_p1 = self.mapToScene(self._p1)
        scene_p2 = self.mapToScene(self._p2)
        tight = QRectF(scene_p1, scene_p2).normalized()
        span_x = abs(scene_p2.x() - scene_p1.x())
        span_y = abs(scene_p2.y() - scene_p1.y())
        if span_x <= 1e-6 and span_y <= 1e-6:
            return
        # 退化方向给一个极小尺寸，避免除零（结果会被下面的方向判断覆盖）
        probe = QRectF(tight)
        if probe.width() <= 1e-6:
            probe.setWidth(1e-6)
        if probe.height() <= 1e-6:
            probe.setHeight(1e-6)
        target = resized_rect(probe, index, point, keep_aspect)

        left, right = tight.left(), tight.right()
        top, bottom = tight.top(), tight.bottom()
        if span_x > 1e-6 and index in LEFT_HANDLES + RIGHT_HANDLES:
            if index in LEFT_HANDLES:
                left = right - abs(target.width())
            else:
                right = left + abs(target.width())
        if span_y > 1e-6 and index in TOP_HANDLES + BOTTOM_HANDLES:
            if index in TOP_HANDLES:
                top = bottom - abs(target.height())
            else:
                bottom = top + abs(target.height())

        width = max(right - left, 1e-6)
        height = max(bottom - top, 1e-6)
        sx = width / tight.width() if span_x > 1e-6 else 1.0
        sy = height / tight.height() if span_y > 1e-6 else 1.0
        fixed_left = index in RIGHT_HANDLES      # 拖左边时右边不动
        fixed_top = index in BOTTOM_HANDLES

        def mapped(point_in_scene: QPointF) -> QPointF:
            if span_x <= 1e-6:
                x = point_in_scene.x()
            elif fixed_left:
                x = right - (tight.right() - point_in_scene.x()) * sx
            else:
                x = left + (point_in_scene.x() - tight.left()) * sx
            if span_y <= 1e-6:
                y = point_in_scene.y()
            elif fixed_top:
                y = bottom - (tight.bottom() - point_in_scene.y()) * sy
            else:
                y = top + (point_in_scene.y() - tight.top()) * sy
            return QPointF(x, y)

        self.set_endpoints(self.mapFromScene(mapped(scene_p1)),
                           self.mapFromScene(mapped(scene_p2)))

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

    # ---------------------------------------------------------- 样式快照 / 单项设置
    def outline_color(self) -> QColor:
        return QColor(self._pen.color())

    def outline_width(self) -> float:
        return float(self._pen.widthF())

    def current_line_style(self) -> str:
        return line_style_name(self._pen.style())

    def set_outline_color(self, color) -> None:
        pen = QPen(self._pen)
        pen.setColor(QColor(color))
        self.set_pen(pen)

    def set_outline_width(self, width: float) -> None:
        pen = QPen(self._pen)
        pen.setWidthF(max(0.1, float(width)))
        self.set_pen(pen)

    def set_line_style(self, style) -> None:
        pen = QPen(self._pen)
        pen.setStyle(pen_style(style))
        self.set_pen(pen)

    def style_state(self) -> dict:
        return {
            "outline": rgba_list(self._pen.color()),
            "outline_width": round(self._pen.widthF(), 3),
            "line_style": line_style_name(self._pen.style()),
            "arrow": self._arrow,
        }

    def restore_style_state(self, state: dict) -> None:
        if not state:
            return
        pen = QPen(self._pen)
        if state.get("outline") is not None:
            pen.setColor(qcolor_from_rgba(state["outline"]))
        if state.get("outline_width") is not None:
            pen.setWidthF(max(0.1, float(state["outline_width"])))
        if state.get("line_style") is not None:
            pen.setStyle(pen_style(state["line_style"]))
        if state.get("arrow") is not None:
            self._arrow = _normalize_arrow(state["arrow"])
        self.set_pen(pen)

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
    """文字块 / **字体框**。

    两种形态（用 ``box_h`` 区分，序列化兼容旧文件）：

    * **字体框**（``box_h > 0``，v1.3.0 起文字工具拖出来的默认形态）：
      框的尺寸是明确的，文字在框宽内自动折行；拖手柄**自由拉伸**（默认不等比），
      改的是**框**而不是字号 —— 字号在参数侧边栏里单独调。
    * **自适应**（``box_h <= 0``，旧文件里保存的文字）：框随文字大小走，
      拖手柄等比改字号（保留 v1.3.0 之前的手感，旧文件打开后不会变形）。

    字体、字号、粗斜体、颜色、对齐、折行宽度都可自定义；
    ``_text_width`` 在字体框形态下就是框宽。
    """

    TYPE = "text"
    MIN_BOX = 20.0

    def __init__(self, text: str, color: QColor, pixel_size: float = 16.0,
                 family: str = "", bold: bool = False, italic: bool = False,
                 wrap: bool = False, text_width: float = 300.0, align: str = "left",
                 font_file: str = "", box_height: float = 0.0):
        super().__init__(text)
        self._font_file = font_file or ""
        self.setDefaultTextColor(QColor(color))
        self._box_h = max(0.0, float(box_height or 0.0))
        # 字体框形态一定有固定宽度（否则谈不上"框"）
        self._wrap = True if self.is_box() else bool(wrap)
        self._text_width = max(self.MIN_BOX, float(text_width))
        if self.is_box():
            # 框的左上角就是文字起点：去掉文档默认边距，手柄才对得上文字
            self.document().setDocumentMargin(0.0)
        font = QFont(family) if family else QFont()
        font.setPixelSize(int(max(6, pixel_size)))
        font.setBold(bool(bold))
        font.setItalic(bool(italic))
        self.setFont(font)
        self.set_text_align(align)
        self._apply_wrap()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    # -- 形态 --
    def is_box(self) -> bool:
        """是否「字体框」形态（有明确高度）。"""
        return self._box_h > 0.0

    def box_height(self) -> float:
        return self._box_h

    def is_empty(self) -> bool:
        return not self.toPlainText().strip()

    def set_box_size(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._text_width = max(self.MIN_BOX, float(width))
        self._box_h = max(0.0, float(height))
        self._wrap = True if self.is_box() else self._wrap
        if self.is_box():
            self.document().setDocumentMargin(0.0)
        self._apply_wrap()
        self.update()

    # -- 内容 --
    def set_text(self, text: str) -> None:
        self.setPlainText(text or "")
        self.update()

    # -- 排版 --
    def _apply_wrap(self) -> None:
        if self.is_box():
            self.setTextWidth(self._text_width)
        else:
            self.setTextWidth(self._text_width if self._wrap else -1.0)

    def is_wrapped(self) -> bool:
        return self._wrap

    def text_width(self) -> float:
        return self._text_width

    def set_wrap(self, wrap: bool, text_width: float = None) -> None:
        self._wrap = bool(wrap)
        if text_width is not None:
            self._text_width = max(self.MIN_BOX, float(text_width))
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

    # -- 单项样式设置（参数侧边栏用）--
    def set_pixel_size(self, pixel_size: float) -> None:
        font = self.font()
        font.setPixelSize(int(max(6, min(400, pixel_size))))
        self.setFont(font)
        self.update()

    def set_font_family(self, family: str, font_file: str = None) -> None:
        font = self.font()
        font.setFamily(family or "")
        self.setFont(font)
        if font_file is not None:
            self._font_file = font_file
        self.update()

    def set_bold(self, on: bool) -> None:
        font = self.font()
        font.setBold(bool(on))
        self.setFont(font)
        self.update()

    def set_italic(self, on: bool) -> None:
        font = self.font()
        font.setItalic(bool(on))
        self.setFont(font)
        self.update()

    def set_text_color(self, color: QColor) -> None:
        self.setDefaultTextColor(QColor(color))
        self.update()

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

    # ---------------------------------------------------------- 样式快照
    def style_state(self) -> dict:
        """参数侧边栏用：内容 + 排版 + 框尺寸（可整体保存/恢复）。"""
        font = self.font()
        return {
            "text": self.toPlainText(),
            "font_family": font.family(),
            "pixel_size": int(font.pixelSize()),
            "bold": font.bold(),
            "italic": font.italic(),
            "color": rgba_list(self.defaultTextColor()),
            "align": self.text_align(),
            "wrap": self._wrap,
            "text_width": round(self._text_width, 3),
            "box_h": round(self._box_h, 3),
            "font_file": self._font_file,
            "pos": [self.pos().x(), self.pos().y()],
        }

    def restore_style_state(self, state: dict) -> None:
        self.prepareGeometryChange()
        if "text" in state:
            self.setPlainText(str(state["text"]))
        font = self.font()
        font.setFamily(str(state.get("font_family", font.family())))
        font.setPixelSize(int(max(6, state.get("pixel_size", font.pixelSize()))))
        font.setBold(bool(state.get("bold", font.bold())))
        font.setItalic(bool(state.get("italic", font.italic())))
        self.setFont(font)
        if state.get("color") is not None:
            self.setDefaultTextColor(qcolor_from_rgba(state["color"]))
        self._font_file = str(state.get("font_file", self._font_file) or "")
        self.set_text_align(str(state.get("align", self.text_align())))
        self._box_h = max(0.0, float(state.get("box_h", self._box_h)))
        self._text_width = max(self.MIN_BOX, float(state.get("text_width", self._text_width)))
        self._wrap = bool(state.get("wrap", self._wrap)) or self.is_box()
        self._apply_wrap()
        pos = state.get("pos")
        if pos:
            self.setPos(QPointF(float(pos[0]), float(pos[1])))
        self.update()

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        return True

    def default_keep_aspect(self, handle_index: int) -> bool:
        """字体框默认**自由拉伸**（拖四角也不等比），按住 Shift 才等比。

        自适应文字（旧文件）相反：它靠改字号缩放，横向拉扁会让字形变形，
        所以保持等比。
        """
        if self.is_box():
            return False
        from canvas.resize import CORNER_HANDLES

        return handle_index in CORNER_HANDLES

    def boundingRect(self) -> QRectF:
        """字体框形态下，包围盒就是框本身（文字溢出也不改框）。"""
        if self.is_box():
            return QRectF(0.0, 0.0, self._text_width, self._box_h)
        return super().boundingRect()

    def shape(self) -> QPainterPath:
        """字体框整块都可命中（空框也能点中、能双击进去输入）。"""
        if self.is_box():
            path = QPainterPath()
            path.addRect(QRectF(0.0, 0.0, self._text_width, self._box_h))
            return path
        return super().shape()

    def resize_rect(self) -> QRectF:
        return self.mapRectToScene(self.boundingRect())

    def resize_state(self) -> dict:
        state = {"box_h": round(self._box_h, 3)}
        state.update(self.style_state())
        return state

    def restore_resize_state(self, state: dict) -> None:
        self.restore_style_state(state)

    def resize_with(self, index: int, point: QPointF, keep_aspect: bool = False) -> None:
        """拖手柄缩放。

        * **字体框**：默认**不等比** —— 改的是框（宽 → 折行宽度，高 → 框高），
          字号不动；按住 Shift 时才保持宽高比。
        * **自适应文字**（旧文件）：等比改字号（横向拉扁会让字形变形）。
        """
        from canvas.resize import resized_rect

        if not self.is_box():
            self._resize_font_only(index, point, keep_aspect)
            return

        rect = self.resize_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        target = resized_rect(rect, index, point, keep_aspect)
        offset = self.pos()
        self.prepareGeometryChange()
        self._text_width = max(self.MIN_BOX, target.width())
        self._box_h = max(self.MIN_BOX, target.height())
        self._wrap = True
        self._apply_wrap()
        # 手柄的锚点（对角/对边）保持不动
        self.setPos(QPointF(target.left(), target.top()))
        if offset != self.pos():
            self.update()

    def _resize_font_only(self, index: int, point: QPointF,
                          keep_aspect: bool = False) -> None:
        """自适应文字：等比改字号（保留旧行为）。"""
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
            self._text_width = max(self.MIN_BOX, self._text_width * actual)
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
            # > 0 表示这是一个「字体框」（固定框尺寸）
            "box_h": round(self._box_h, 3),
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
            box_height=float(data.get("box_h", 0.0)),
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

    # ---------------------------------------------------------- 样式快照 / 单项设置
    def scale_x(self) -> float:
        return float(self.transform().m11())

    def scale_y(self) -> float:
        return float(self.transform().m22())

    def set_scale(self, sx: float, sy: float = None) -> None:
        """按比例设置显示尺寸（参数侧边栏用），传入 0.5 表示 50%。"""
        sy = sx if sy is None else sy
        sx = max(self.MIN_SCALE, min(self.MAX_SCALE, float(sx)))
        sy = max(self.MIN_SCALE, min(self.MAX_SCALE, float(sy)))
        self.prepareGeometryChange()
        self.setTransform(QTransform.fromScale(sx, sy))
        self.update()

    def style_state(self) -> dict:
        state = transform_state(self)
        state["opacity"] = round(self.opacity(), 3)
        return state

    def restore_style_state(self, state: dict) -> None:
        if not state:
            return
        apply_transform_state(self, state)
        if state.get("opacity") is not None:
            self.setOpacity(float(state["opacity"]))

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


# ------------------------------------------------------------------ 组合


class GroupItem(QGraphicsItemGroup):
    """把若干图形项组合成一个整体（Ctrl+G）。

    组合之后它就像「一个常规图形」：点击/框选选中的是整个组，拖动整体移动，
    拖手柄整体缩放（走 transform，里面的内容一起变大变小），按 Delete 整体删除
    （橡皮擦不碰它 —— 橡皮只擦手绘笔迹）；
    拆开（Ctrl+Shift+G）后各回原位。

    几个必须注意的点：

    * ``QGraphicsItemGroup`` 默认**不可选中**，要自己打开 ``ItemIsSelectable``；
    * 场景的命中测试会把「组」当成一个整体返回（组里的子项不会被单独点到），
      但 ``scene.items()`` 仍然会列出子项 —— 序列化那边靠
      ``parentItem() is not None`` 把它们跳过，由组自己负责保存子项；
    * 组要保持 ``pos=(0,0)``、无变换地加入子项，子项的场景位置才不会变；
    * 组的缩放记在 ``transform`` 上（``sx``/``sy``），子项自身保持不变。
    """

    TYPE = "group"

    def __init__(self, items=None):
        super().__init__()
        self._children: list = []
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        for item in (items or []):
            self.add_item(item)

    # ---------------------------------------------------------- 成员管理
    def add_item(self, item) -> None:
        """把图形项收进组里（保持它原来的场景位置）。

        必须用 Qt 的 :meth:`QGraphicsItemGroup.addToGroup`，不能自己
        ``setParentItem(self)`` —— 后者绕过了组内部的成员表，组会算不出
        ``boundingRect()``（选中框、手柄、命中测试全都跟着失效，
        表现为「组合后选不中也缩放不了」）。
        """
        if item is None or item is self:
            return
        # 组处于 (0,0) 且无变换时，addToGroup 保持子项的局部坐标
        # （也就是它原来的场景坐标），场景位置因此不变。
        item.setSelected(False)
        self.addToGroup(item)
        if item not in self._children:
            self._children.append(item)

    def children_items(self) -> list:
        return list(self._children)

    def remove_item(self, item) -> None:
        """把图形项移出组。

        Qt 的 ``removeFromGroup()`` **会自己保持子项的场景位置与变换**
        （它把组的变换补进子项的 pos/transform 里），所以这里不需要再做什么 ——
        曾经手动补偿过一次，结果缩放被叠加了两遍（拆组后图形突然变大一倍）。
        """
        self.removeFromGroup(item)
        for index, child in enumerate(self._children):
            if child is item:
                del self._children[index]
                break

    # ---------------------------------------------------------- 自由伸缩
    def is_resizable(self) -> bool:
        # 只有一个子项时没必要缩放「组」，直接选那个子项更好
        return bool(self._children)

    def resize_rect(self) -> QRectF:
        return self.mapRectToScene(self.boundingRect())

    def resize_state(self) -> dict:
        return transform_state(self)

    def restore_resize_state(self, state: dict) -> None:
        apply_transform_state(self, state)

    def resize_with(self, index: int, point: QPointF,
                    keep_aspect: bool = False) -> None:
        resize_by_transform(self, index, point, keep_aspect)

    def scene_scale(self) -> float:
        return item_scene_scale(self)

    # ---------------------------------------------------------- 样式快照（递归到成员）
    def style_state(self) -> dict:
        """组合的样式快照 = 各成员自己的快照（参数改动会作用到所有成员）。"""
        state = transform_state(self)
        state["children"] = [
            child.style_state() for child in self._children
            if getattr(child, "style_state", None) is not None]
        return state

    def restore_style_state(self, state: dict) -> None:
        if not state:
            return
        children = state.get("children") or []
        for child, child_state in zip(self._children, children):
            if getattr(child, "restore_style_state", None) is not None:
                child.restore_style_state(child_state)
        apply_transform_state(self, state)

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        transform = self.transform()
        return {
            "type": self.TYPE,
            "pos": [self.pos().x(), self.pos().y()],
            "sx": round(transform.m11(), 6),
            "sy": round(transform.m22(), 6),
            "items": [child.to_dict() for child in self._children
                      if getattr(child, "to_dict", None) is not None],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GroupItem":
        item = cls()
        # 先按「单位变换、原点」把子项收进来，再设置组的位移/缩放：
        # QGraphicsItemGroup.addToGroup() **会保持子项的场景位置**，
        # 所以如果先设好组的变换，子项的局部坐标会被反过来补偿掉，
        # 组一加载就会整体错位。
        for child_data in data.get("items", []):
            child = item_from_dict(child_data)
            if child is not None:
                item.add_item(child)
        item.setTransform(QTransform.fromScale(float(data.get("sx", 1.0)),
                                               float(data.get("sy", 1.0))))
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(float(pos[0]), float(pos[1])))
        return item


def apply_style_changes(item, changes: dict) -> None:
    """把参数侧边栏的一组改动应用到图形项上（只处理该对象支持的那些项）。

    ``changes`` 里只会出现用户真正动过的那几项，例如 ``{"color": QColor}``、
    ``{"text": "..."}``、``{"width": 4.0}``。同一把键在不同对象上含义不同：

    * 文字对象：``color`` / ``text_color`` 都是文字颜色，``text`` 是内容，可改框尺寸；
    * 图形（矩形/椭圆/多边形）：``color`` 是描边色，``text`` 是**图形内部的标签**；
    * 笔迹 / 线段：``color`` 是线条颜色、``width`` 是线宽、``line_style`` 是线型；
    * 图片：``scale_percent``（100% = 位图原始像素）与 ``opacity_percent``。
    """
    if item is None or not changes:
        return

    # ---- 文字对象 ----
    if isinstance(item, TextItem):
        if "text" in changes:
            item.set_text(str(changes["text"]))
        if changes.get("font_family") is not None:
            item.set_font_family(str(changes["font_family"]))
        if "pixel_size" in changes:
            item.set_pixel_size(float(changes["pixel_size"]))
        if "bold" in changes:
            item.set_bold(bool(changes["bold"]))
        if "italic" in changes:
            item.set_italic(bool(changes["italic"]))
        if changes.get("align"):
            item.set_text_align(str(changes["align"]))
        if changes.get("text_color") is not None:
            item.set_text_color(changes["text_color"])
        elif changes.get("color") is not None:
            item.set_text_color(changes["color"])
        if item.is_box() and ("box_width" in changes or "box_height" in changes):
            item.set_box_size(float(changes.get("box_width", item.text_width())),
                              float(changes.get("box_height", item.box_height())))
        return

    # ---- 图形内部的文字标签 ----
    label_keys = ("text", "font_family", "pixel_size", "bold", "italic",
                  "align", "text_color")
    if hasattr(item, "set_raw_label") and any(key in changes for key in label_keys):
        label = item.raw_label() or make_label()
        if "text" in changes:
            label["text"] = str(changes["text"])
        if changes.get("font_family") is not None:
            label["font_family"] = str(changes["font_family"])
        if "pixel_size" in changes:
            label["pixel_size"] = float(changes["pixel_size"])
        if "bold" in changes:
            label["bold"] = bool(changes["bold"])
        if "italic" in changes:
            label["italic"] = bool(changes["italic"])
        if changes.get("align"):
            label["align"] = str(changes["align"])
        if changes.get("text_color") is not None:
            label["color"] = rgba_list(changes["text_color"])
        item.set_raw_label(label)

    # ---- 描边 / 线型 / 填充 ----
    if changes.get("color") is not None and hasattr(item, "set_outline_color"):
        item.set_outline_color(changes["color"])
    if "width" in changes and hasattr(item, "set_outline_width"):
        item.set_outline_width(float(changes["width"]))
    if changes.get("line_style") and hasattr(item, "set_line_style"):
        item.set_line_style(str(changes["line_style"]))
    if "fill" in changes and hasattr(item, "setBrush"):
        fill = changes["fill"]
        item.setBrush(QColor(fill) if fill is not None else QBrush(Qt.BrushStyle.NoBrush))
        item.update()

    # ---- 图片 ----
    if isinstance(item, ImageItem):
        if "scale_percent" in changes:
            item.set_scale(float(changes["scale_percent"]) / 100.0)
        if "opacity_percent" in changes:
            item.setOpacity(max(0.05, min(1.0, float(changes["opacity_percent"]) / 100.0)))


def style_targets(items) -> list:
    """把选中的对象展开成"真正要改参数"的图形项列表。

    组合本身没有颜色/线宽，改参数时应该落到**组内每个成员**身上，
    所以这里把组合摊平成它的成员（普通对象原样返回）。
    """
    targets = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, GroupItem):
            for child in item.children_items():
                if hasattr(child, "style_state"):
                    targets.append(child)
            continue
        if hasattr(item, "style_state"):
            targets.append(item)
    return targets


# ------------------------------------------------------------------ 工厂


_TYPE_MAP = {
    StrokeItem.TYPE: StrokeItem,
    RectItem.TYPE: RectItem,
    EllipseItem.TYPE: EllipseItem,
    PolygonShapeItem.TYPE: PolygonShapeItem,
    LineItem.TYPE: LineItem,
    TextItem.TYPE: TextItem,
    ImageItem.TYPE: ImageItem,
    GroupItem.TYPE: GroupItem,
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
