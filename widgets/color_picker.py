"""颜色选择按钮：显示当前颜色块，点击弹出 QColorDialog。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton


class ColorPickerButton(QPushButton):
    colorChanged = Signal(QColor)

    def __init__(self, color: QColor = None, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.setFixedSize(56, 28)
        self.setToolTip("画笔颜色（点击选择）")
        self._refresh_style()
        self.clicked.connect(self._pick_color)

    # ------------------------------------------------------------- API
    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor, emit: bool = True) -> None:
        self._color = QColor(color)
        self._refresh_style()
        if emit:
            self.colorChanged.emit(QColor(self._color))

    def _refresh_style(self) -> None:
        hex_name = self._color.name(QColor.NameFormat.HexRgb)
        border = "#888" if self._color.lightness() > 160 else "#bbb"
        self.setStyleSheet(
            f"QPushButton {{ background-color: {hex_name};"
            f" border: 1px solid {border}; border-radius: 4px; }}"
        )
        self.update()

    # ------------------------------------------------------------- 内部
    def _pick_color(self) -> None:
        picked = QColorDialog.getColor(self._color, self.window(), "选择颜色")
        if picked.isValid() and picked != self._color:
            self.set_color(picked)
