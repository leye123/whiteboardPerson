"""白板可持久化图形项集合（canvas/items.py）。

每个图形项都实现 to_dict()/重建逻辑，并由 item_from_dict() 统一注册表重建，
因此 scene / 页面内容可以无损序列化为 JSON（.wbd 文件）。
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
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsTextItem,
)

from core.stroke import build_path, qcolor_from_rgba, qcolor_to_rgba

# 预览态标记：绘制过程中的临时图形项会带上它，序列化时自动跳过。
# 注意 QGraphicsItem.setData 的 key 必须是 int（不能是字符串）。
PREVIEW_KEY = 1000


def mark_preview(item, preview: bool = True) -> None:
    item.setData(PREVIEW_KEY, bool(preview))


def is_preview(item) -> bool:
    return bool(item.data(PREVIEW_KEY))

# ------------------------------------------------------------------ 工具函数


def make_pen(
    color: QColor, width: float, style: Qt.PenStyle = Qt.PenStyle.SolidLine
) -> QPen:
    pen = QPen(QColor(color), float(width), style)
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


def rgba_list(color: QColor) -> list:
    return qcolor_to_rgba(color)


# ------------------------------------------------------------------ 笔画


class StrokeItem(QGraphicsPathItem):
    """一条手绘笔迹。

    内部保存原始采样点（``_points``），渲染路径由 :func:`core.stroke.build_path`
    用中点二次贝塞尔拟合生成：
    - 曲线平滑，不会出现折线感；
    - 序列化时直接写出原始点，不会因为路径含曲线段而丢点。
    """

    TYPE = "stroke"

    def __init__(self, points=None, color: QColor = None, thickness: float = 2.0,
                 pos: QPointF = None):
        pts = [QPointF(p) for p in (points or [])]
        super().__init__(build_path(pts))
        self._points = pts
        if color is None:
            color = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(color, thickness))
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

    # ---------------------------------------------------------- 绘制接口
    def shape(self) -> QPainterPath:  # 加粗命中区
        return stroked_shape(self.path(), self.pen().widthF())

    def to_dict(self) -> dict:
        return {
            "type": self.TYPE,
            "color": rgba_list(self.pen().color()),
            "thickness": round(self.pen().widthF(), 3),
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
        )
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 矩形 / 椭圆


class _RectLikeMixin:
    """QGraphicsRectItem / QGraphicsEllipseItem 共享的序列化逻辑。"""

    def _common_dict(self, kind: str) -> dict:
        r = self.rect()
        return {
            "type": kind,
            "x": round(r.x(), 3),
            "y": round(r.y(), 3),
            "w": round(r.width(), 3),
            "h": round(r.height(), 3),
            "pos": [self.pos().x(), self.pos().y()],
            "outline": rgba_list(self.pen().color()),
            "outline_width": round(self.pen().widthF(), 3),
            "fill": rgba_list(self.brush().color()) if self.brush().style() != Qt.BrushStyle.NoBrush else None,
        }

    @classmethod
    def _common_from_dict(cls, data: dict, kind: str):
        rect = QRectF(data["x"], data["y"], data["w"], data["h"])
        item = cls(rect)
        item.setPen(make_pen(qcolor_from_rgba(data.get("outline", [0, 0, 0, 255])),
                             float(data.get("outline_width", 2.0))))
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


class RectItem(_RectLikeMixin, QGraphicsRectItem):
    TYPE = "rect"

    def __init__(self, rect: QRectF, outline: QColor = None, width: float = 2.0):
        super().__init__(rect)
        if outline is None:
            outline = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(outline, width))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect())
        return stroked_shape(path, self.pen().widthF())

    def to_dict(self) -> dict:
        return self._common_dict(self.TYPE)

    @classmethod
    def from_dict(cls, data: dict) -> "RectItem":
        return cls._common_from_dict(data, "rect")


class EllipseItem(_RectLikeMixin, QGraphicsEllipseItem):
    TYPE = "ellipse"

    def __init__(self, rect: QRectF, outline: QColor = None, width: float = 2.0):
        super().__init__(rect)
        if outline is None:
            outline = QColor(Qt.GlobalColor.black)
        self.setPen(make_pen(outline, width))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(self.rect())
        return stroked_shape(path, self.pen().widthF())

    def to_dict(self) -> dict:
        return self._common_dict(self.TYPE)

    @classmethod
    def from_dict(cls, data: dict) -> "EllipseItem":
        return cls._common_from_dict(data, "ellipse")


# ------------------------------------------------------------------ 直线 / 箭头


class LineItem(QGraphicsItem):
    """带可选箭头端点的直线。

    几何存储在场景坐标中（item 自身 pos 保持 (0,0)），
    移动通过 setPos 完成，因此序列化记录 pos 即可。
    """

    TYPE = "line"

    def __init__(self, start: QPointF, end: QPointF, outline: QColor,
                 width: float, arrow: bool = False):
        super().__init__()
        self._p1 = QPointF(start)
        self._p2 = QPointF(end)
        self._arrow = bool(arrow)
        self._pen = make_pen(outline, width)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(0)

    # -- 几何 --
    def start(self) -> QPointF:
        return self._p1

    def end(self) -> QPointF:
        return self._p2

    def set_endpoints(self, start: QPointF, end: QPointF) -> None:
        self.prepareGeometryChange()
        self._p1 = QPointF(start)
        self._p2 = QPointF(end)
        self.update()

    def set_pen(self, pen: QPen) -> None:
        """替换画笔（用于预览态的虚线笔）。"""
        self.prepareGeometryChange()
        self._pen = QPen(pen)
        self.update()

    def _arrow_head(self) -> QPolygonF:
        """按线宽缩放箭头头大小，返回以 self._p2 为顶点的三角形。"""
        width = self._pen.widthF()
        size = max(10.0, width * 3.2)
        dx, dy = self._p2.x() - self._p1.x(), self._p2.y() - self._p1.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return QPolygonF()
        ux, uy = dx / length, dy / length          # 单位方向
        px, py = -uy, ux                           # 垂直单位
        tip = QPointF(self._p2.x() - ux * width * 0.6, self._p2.y() - uy * width * 0.6)
        base = QPointF(tip.x() - ux * size, tip.y() - uy * size)
        return QPolygonF([
            tip,
            QPointF(base.x() + px * size * 0.45, base.y() + py * size * 0.45),
            QPointF(base.x() - px * size * 0.45, base.y() - py * size * 0.45),
        ])

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
        head = self._arrow_head()
        if not head.isEmpty():
            path.addPolygon(head)
        return stroked_shape(path, self._pen.widthF())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self._pen)
        painter.drawLine(self._p1, self._p2)
        if self._arrow:
            head = self._arrow_head()
            if not head.isEmpty():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._pen.color())
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
            "arrow": self._arrow,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LineItem":
        item = cls(
            QPointF(data.get("x1", 0.0), data.get("y1", 0.0)),
            QPointF(data.get("x2", 0.0), data.get("y2", 0.0)),
            qcolor_from_rgba(data.get("outline", [0, 0, 0, 255])),
            float(data.get("outline_width", 2.0)),
            bool(data.get("arrow", False)),
        )
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 文字


class TextItem(QGraphicsTextItem):
    TYPE = "text"

    def __init__(self, text: str, color: QColor, pixel_size: float = 16.0):
        super().__init__(text)
        self.setDefaultTextColor(QColor(color))
        font = QFont()
        font.setPixelSize(int(max(8, pixel_size)))
        self.setFont(font)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def to_dict(self) -> dict:
        font = self.font()
        return {
            "type": self.TYPE,
            "pos": [self.pos().x(), self.pos().y()],
            "text": self.toPlainText(),
            "color": rgba_list(self.defaultTextColor()),
            "font_family": font.family(),
            "pixel_size": font.pixelSize(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TextItem":
        item = cls(
            data.get("text", ""),
            qcolor_from_rgba(data.get("color", [0, 0, 0, 255])),
            float(data.get("pixel_size", 16.0)),
        )
        family = data.get("font_family")
        if family:
            font = item.font()
            font.setFamily(family)
            item.setFont(font)
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 图片


class ImageItem(QGraphicsPixmapItem):
    TYPE = "image"

    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

    def to_dict(self) -> dict:
        image = self.pixmap().toImage()
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        buf.close()
        return {
            "type": self.TYPE,
            "pos": [self.pos().x(), self.pos().y()],
            "data": bytes(ba.toBase64()).decode("ascii"),
            "w": self.pixmap().width(),
            "h": self.pixmap().height(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageItem":
        raw = base64.b64decode(data.get("data", ""))
        pix = QPixmap()
        pix.loadFromData(raw, "PNG")
        item = cls(pix)
        pos = data.get("pos")
        if pos:
            item.setPos(QPointF(pos[0], pos[1]))
        return item


# ------------------------------------------------------------------ 工厂


_TYPE_MAP = {
    StrokeItem.TYPE: StrokeItem,
    RectItem.TYPE: RectItem,
    EllipseItem.TYPE: EllipseItem,
    LineItem.TYPE: LineItem,
    TextItem.TYPE: TextItem,
    ImageItem.TYPE: ImageItem,
}

ITEM_TYPES = tuple(_TYPE_MAP.keys())


def item_from_dict(data: dict):
    """按序列化 dict 重建图形项；未知类型返回 None。"""
    cls = _TYPE_MAP.get(data.get("type"))
    if cls is None:
        return None
    return cls.from_dict(data)
