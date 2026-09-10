"""文字工具：点击画布后在弹出对话框中输入文本，确定后插入。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout

from canvas.items import TextItem
from core.history import AddItemCommand
from tools.base_tool import BaseTool


class TextEditDialog(QDialog):
    """一个简单的多行文本输入对话框，用于新建/编辑文字。"""

    def __init__(self, parent=None, title: str = "输入文字",
                 initial: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(320, 180)
        layout = QVBoxLayout(self)
        self.editor = QTextEdit(self)
        self.editor.setPlainText(initial)
        self.editor.setFocus()
        layout.addWidget(self.editor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def ask(cls, parent=None, title: str = "输入文字",
            initial: str = "") -> str | None:
        dlg = cls(parent, title, initial)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            text = dlg.editor.toPlainText().strip()
            return text or None
        return None


class TextTool(BaseTool):
    name = "文字"
    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self, color: QColor = None, pixel_size: float = 16.0) -> None:
        super().__init__()
        self.color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.pixel_size = float(pixel_size)

    def mousePressEvent(self, event, view) -> None:
        pos = self.scene_pos(event, view)
        text = TextEditDialog.ask(view.window(), "输入文字")
        if not text:
            event.accept()
            return
        item = TextItem(text, self.color, self.pixel_size)
        item.setPos(pos)
        scene = view.scene()
        stack = self.undo_stack(view)
        if stack is not None:
            stack.push(AddItemCommand(scene, item, "添加文字"))
        else:
            scene.addItem(item)
        event.accept()

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    def mouseMoveEvent(self, event, view) -> None:
        pass

    def mouseReleaseEvent(self, event, view) -> None:
        pass
