"""文字工具：点击画布后弹出对话框输入文本与排版，确定后插入。

对话框支持选择字体、字号、颜色、粗斜体、对齐与自动换行，也可以导入字体文件
（见 :mod:`widgets.text_dialog` 与 :mod:`core.fonts`）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from canvas.items import TextItem
from core.history import AddItemCommand
from core.text_format import TextFormat
from tools.base_tool import BaseTool
from widgets.text_dialog import TextDialog, TextEditDialog  # noqa: F401 —— 旧名字兼容


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
        self.settings = settings          # 用于记住导入的字体与上次的排版
        self.format_changed = None        # 可选回调：MainWindow 用它同步颜色按钮

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
        pos = self.scene_pos(event, view)
        result = TextDialog.ask(view.window(), "输入文字", "", self.format,
                                self.settings)
        if not result:
            event.accept()
            return
        # 兼容旧接口：若被替换成只返回字符串的实现（测试里会这么做）
        if isinstance(result, tuple):
            text, fmt = result
        else:
            text, fmt = result, self.format
        self._remember_format(fmt)

        item = TextItem(
            text, fmt.color, fmt.pixel_size,
            family=fmt.family, bold=fmt.bold, italic=fmt.italic,
            wrap=fmt.wrap, text_width=fmt.text_width, align=fmt.align,
            font_file=fmt.font_file)
        item.setPos(pos)
        scene = view.scene()
        stack = self.undo_stack(view)
        if stack is not None:
            stack.push(AddItemCommand(scene, item, "添加文字"))
        else:
            scene.addItem(item)
        event.accept()

    def _remember_format(self, fmt: TextFormat) -> None:
        """记住这次用的排版（下次默认沿用），并同步设置与界面颜色。"""
        self.format = fmt.copy()
        if self.settings is not None:
            try:
                self.settings.set_text_format(self.format)
                self.settings.sync()
            except Exception:  # noqa: BLE001 —— 设置写不进去不该影响画布
                pass
        if callable(self.format_changed):
            self.format_changed(self.format)

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    def mouseMoveEvent(self, event, view) -> None:
        pass

    def mouseReleaseEvent(self, event, view) -> None:
        pass
