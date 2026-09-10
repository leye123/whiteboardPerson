"""形状工具：矩形/圆角矩形/椭圆/三角形/菱形/五角星/直线/箭头（拖拽预览 + 释放提交）。

``kind`` 决定几何形状，``line_style`` 决定线型（实线/虚线/点线/点划线）。
其中「虚线」「虚线箭头」是**自带虚线**的快捷种类（图标一眼能认出来），
其余形状的虚线由工具栏的「线型」下拉框控制 —— 于是任何形状都能画成虚线。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor

from canvas.items import (
    ARROW_BOTH,
    ARROW_END,
    ARROW_NONE,
    DEFAULT_LINE_STYLE,
    EllipseItem,
    LineItem,
    PolygonShapeItem,
    RectItem,
    default_radius,
    make_pen,
    mark_preview,
)
from core.history import AddItemCommand
from tools.base_tool import BaseTool

# 种类 -> (显示名, 几何类型, 强制线型, 箭头形态)
SHAPE_SPECS = {
    "rect": ("矩形", "rect", None, ARROW_NONE),
    "round_rect": ("圆角矩形", "round_rect", None, ARROW_NONE),
    "ellipse": ("椭圆", "ellipse", None, ARROW_NONE),
    "triangle": ("三角形", "triangle", None, ARROW_NONE),
    "diamond": ("菱形", "diamond", None, ARROW_NONE),
    "star": ("五角星", "star", None, ARROW_NONE),
    "line": ("直线", "line", None, ARROW_NONE),
    "dashed_line": ("虚线", "line", "dash", ARROW_NONE),
    "arrow": ("箭头", "line", None, ARROW_END),
    "dashed_arrow": ("虚线箭头", "line", "dash", ARROW_END),
    "double_arrow": ("双向箭头", "line", None, ARROW_BOTH),
}

KINDS = tuple(SHAPE_SPECS.keys())
SHAPE_KINDS = tuple(k for k, spec in SHAPE_SPECS.items() if spec[1] != "line")
LINE_KINDS = tuple(k for k, spec in SHAPE_SPECS.items() if spec[1] == "line")


def shape_label(kind: str) -> str:
    return SHAPE_SPECS.get(kind, SHAPE_SPECS["rect"])[0]


def is_line_kind(kind: str) -> bool:
    return SHAPE_SPECS.get(kind, SHAPE_SPECS["rect"])[1] == "line"


class ShapeTool(BaseTool):
    name = "形状"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, kind: str = "rect", color: QColor = None,
                 thickness: float = 2.0, line_style: str = DEFAULT_LINE_STYLE) -> None:
        super().__init__()
        if kind not in SHAPE_SPECS:
            raise ValueError(f"不支持的形状类型: {kind}")
        self.kind = kind
        self.color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.thickness = float(thickness)
        self.line_style = line_style
        self._start = None          # 场景坐标起点
        self._preview = None
        self._drawing = False

    # ------------------------------------------------------------- 属性
    @property
    def label(self) -> str:
        return shape_label(self.kind)

    def effective_line_style(self) -> str:
        """实际使用的线型：种类自带的虚线优先，否则跟随工具栏的线型选择。"""
        forced = SHAPE_SPECS[self.kind][2]
        return forced or self.line_style or DEFAULT_LINE_STYLE

    # ------------------------------------------------------------- 事件
    def mousePressEvent(self, event, view) -> None:
        pos = self.scene_pos(event, view)
        self._start = pos
        self._drawing = True
        self._update_preview(pos, view)
        event.accept()

    def mouseMoveEvent(self, event, view) -> None:
        if not self._drawing or self._start is None:
            return
        self._update_preview(self.scene_pos(event, view), view)
        event.accept()

    def mouseReleaseEvent(self, event, view) -> None:
        if not self._drawing:
            return
        self._drawing = False
        end = self.scene_pos(event, view)
        start = self._start
        self._start = None

        scene = view.scene()
        preview = self._preview
        self._preview = None
        if preview is not None and preview.scene() is scene:
            scene.removeItem(preview)

        # 过小(误点)则放弃
        if start is None or (abs(end.x() - start.x()) < 2 and abs(end.y() - start.y()) < 2):
            event.accept()
            return

        item = self._build_item(start, end)
        stack = self.undo_stack(view)
        if stack is not None:
            stack.push(AddItemCommand(scene, item, self._label()))
        else:
            scene.addItem(item)
        event.accept()

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    # ------------------------------------------------------------- 构建图形
    def _rect_between(self, a: QPointF, b: QPointF) -> QRectF:
        return QRectF(a, b).normalized()

    def _build_item(self, start: QPointF, end: QPointF):
        rect = self._rect_between(start, end)
        geometry = SHAPE_SPECS[self.kind][1]
        style = self.effective_line_style()
        arrow = SHAPE_SPECS[self.kind][3]
        if geometry == "rect":
            return RectItem(rect, self.color, self.thickness, line_style=style)
        if geometry == "round_rect":
            return RectItem(rect, self.color, self.thickness,
                            radius=default_radius(rect), line_style=style)
        if geometry == "ellipse":
            return EllipseItem(rect, self.color, self.thickness, line_style=style)
        if geometry in ("triangle", "diamond", "star"):
            return PolygonShapeItem(geometry, rect, self.color, self.thickness,
                                    line_style=style)
        return LineItem(start, end, self.color, self.thickness, arrow=arrow,
                        line_style=style)

    def _build_preview(self, start: QPointF, end: QPointF):
        """预览用半透明的同款画笔：所见即所得（虚线预览也是虚线）。"""
        item = self._build_item(start, end)
        color = QColor(self.color)
        color.setAlpha(150)
        pen = make_pen(color, self.thickness, self.effective_line_style())
        if isinstance(item, LineItem):
            item.set_pen(pen)
        else:
            item.setPen(pen)
        return item

    def _update_preview(self, end: QPointF, view) -> None:
        scene = view.scene()
        if self._preview is None:
            self._preview = self._build_preview(self._start, end)
            mark_preview(self._preview)
            scene.addItem(self._preview)
            return
        if isinstance(self._preview, LineItem):
            self._preview.set_endpoints(self._start, end)
        else:
            self._preview.set_rect(self._rect_between(self._start, end))

    def _label(self) -> str:
        return self.label
