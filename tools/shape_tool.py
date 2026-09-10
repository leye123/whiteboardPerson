"""形状工具：矩形、椭圆、直线、箭头（拖拽预览 + 释放提交）。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor

from canvas.items import EllipseItem, LineItem, RectItem, make_pen, mark_preview
from core.history import AddItemCommand
from tools.base_tool import BaseTool

KINDS = ("rect", "ellipse", "line", "arrow")


class ShapeTool(BaseTool):
    name = "形状"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, kind: str = "rect",
                 color: QColor = None, thickness: float = 2.0) -> None:
        super().__init__()
        if kind not in KINDS:
            raise ValueError(f"不支持的形状类型: {kind}")
        self.kind = kind
        self.color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.thickness = float(thickness)
        self._start = None          # 场景坐标起点
        self._preview = None
        self._drawing = False

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
        if self.kind == "rect":
            item = RectItem(rect, self.color, self.thickness)
        elif self.kind == "ellipse":
            item = EllipseItem(rect, self.color, self.thickness)
        else:
            item = LineItem(start, end, self.color, self.thickness,
                            arrow=(self.kind == "arrow"))
        return item

    def _build_preview(self, start: QPointF, end: QPointF):
        item = self._build_item(start, end)
        pen = make_pen(self.color, self.thickness, Qt.PenStyle.DashLine)
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
            self._preview.setRect(self._rect_between(self._start, end))

    def _label(self) -> str:
        return {
            "rect": "矩形", "ellipse": "椭圆",
            "line": "直线", "arrow": "箭头",
        }[self.kind]
