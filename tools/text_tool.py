"""文字工具：先在画布上拖出**字体框**，再双击进去输入文字（v1.3.0 起）。

流程：

1. 选中文字工具后在画布上按住左键拖出框的大小（松开即创建）；
   直接单击会创建一个默认大小的框，免得必须拖一下才能用；
2. 双击这个框（选择工具下）即可在**右侧「参数」侧边栏**里输入文字、调字体字号等；
3. 框的缩放默认**不等比**：拖手柄改的是框本身（宽度决定折行），字号不动。

建好框之后不自动进入输入状态 —— 用户可能只是想先摆好版面。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor

from canvas.items import RectItem, TextItem, mark_preview
from core.history import AddItemCommand
from core.text_format import TextFormat
from tools.base_tool import BaseTool

# 直接单击（没拖动）时用的默认框尺寸
DEFAULT_BOX = (240.0, 80.0)
# 小于这个拖拽距离就当成「单击」
CLICK_THRESHOLD = 8.0
MIN_BOX = 24.0


class TextTool(BaseTool):
    name = "文字"
    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self, color: QColor = None, pixel_size: float = 16.0,
                 text_format: TextFormat = None, settings=None) -> None:
        super().__init__()
        if text_format is not None:
            self.format = text_format.copy()
        else:
            self.format = TextFormat(
                color=QColor(color) if color is not None else QColor(Qt.GlobalColor.black),
                pixel_size=float(pixel_size))
        if color is not None:
            self.format.color = QColor(color)
        self.settings = settings          # 记住上次用过的排版
        self._start = None
        self._preview = None
        self._drawing = False

    # ------------------------------------------------------------- 兼容属性
    @property
    def color(self) -> QColor:
        return QColor(self.format.color)

    @color.setter
    def color(self, value: QColor) -> None:
        self.format.color = QColor(value)

    @property
    def pixel_size(self) -> float:
        return float(self.format.pixel_size)

    @pixel_size.setter
    def pixel_size(self, value: float) -> None:
        self.format.pixel_size = float(value)

    # ------------------------------------------------------------- 事件
    def mousePressEvent(self, event, view) -> None:
        self._start = self.scene_pos(event, view)
        self._drawing = True
        self._update_preview(self._start, view)
        event.accept()

    def mouseMoveEvent(self, event, view) -> None:
        if not self._drawing or self._start is None:
            return
        self._update_preview(self.scene_pos(event, view), view)
        event.accept()

    def mouseReleaseEvent(self, event, view) -> None:
        if not self._drawing or self._start is None:
            event.accept()
            return
        self._drawing = False
        end = self.scene_pos(event, view)
        start = self._start
        self._start = None
        self._clear_preview(view)

        rect = self._box_rect(start, end)
        item = TextItem(
            "", self.format.color, self.format.pixel_size,
            family=self.format.family, bold=self.format.bold,
            italic=self.format.italic,
            text_width=rect.width(), align=self.format.align,
            font_file=self.format.font_file,
            box_height=rect.height())
        item.setPos(rect.topLeft())

        scene = view.scene()
        stack = self.undo_stack(view)
        if stack is not None:
            stack.push(AddItemCommand(scene, item, "新建字体框"))
        else:
            scene.addItem(item)
        scene.clearSelection()
        item.setSelected(True)
        window = view.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(
                "字体框已创建：双击它即可输入文字（右侧「参数」侧边栏）", 5000)
        event.accept()

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    # ------------------------------------------------------------- 生命周期
    def deactivate(self, view) -> None:
        self._drawing = False
        self._start = None
        self._clear_preview(view)
        super().deactivate(view)

    # ------------------------------------------------------------- 内部
    def _box_rect(self, start: QPointF, end: QPointF) -> QRectF:
        """拖出来的框；单击（几乎没拖动）时用默认尺寸。"""
        if (abs(end.x() - start.x()) < CLICK_THRESHOLD
                and abs(end.y() - start.y()) < CLICK_THRESHOLD):
            width, height = DEFAULT_BOX
            return QRectF(start.x(), start.y(), width, height)
        rect = QRectF(start, end).normalized()
        return QRectF(rect.x(), rect.y(),
                      max(MIN_BOX, rect.width()), max(MIN_BOX, rect.height()))

    def _update_preview(self, end: QPointF, view) -> None:
        scene = view.scene()
        rect = self._box_rect(self._start, end)
        if self._preview is None:
            preview = RectItem(rect, QColor(38, 132, 255), 1.0, line_style="dash")
            mark_preview(preview)
            preview.setZValue(1e9)
            scene.addItem(preview)
            self._preview = preview
            return
        self._preview.set_rect(rect)

    def _clear_preview(self, view=None) -> None:
        preview = self._preview
        self._preview = None
        if preview is None:
            return
        scene = preview.scene() or (view.scene() if view is not None else None)
        if scene is not None:
            scene.removeItem(preview)
