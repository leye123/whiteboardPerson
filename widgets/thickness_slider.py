"""粗细滑块：滑动调整当前画笔/图形的线宽。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QWidget,
)

MIN_VALUE = 1
MAX_VALUE = 40


class ThicknessSlider(QWidget):
    thicknessChanged = Signal(float)

    def __init__(self, parent=None, label: str = "粗细") -> None:
        super().__init__(parent)
        # 工具栏会把最后加入的控件拉伸填满剩余空间（默认 sizePolicy 允许变宽），
        # 结果就是「粗细」两个字留在左边、滑块被推到窗口最右边。
        # 这里锁死水平尺寸，让它保持自然宽度。
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        self.caption = QLabel(label, self)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(MIN_VALUE, MAX_VALUE)
        self.slider.setValue(2)
        self.slider.setFixedWidth(110)
        self.value_label = QLabel("2", self)
        self.value_label.setFixedWidth(14)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self.caption)
        layout.addWidget(self.slider)
        layout.addWidget(self.value_label)

        self.slider.valueChanged.connect(self._on_value_changed)

    # ------------------------------------------------------------- API
    def value(self) -> float:
        return float(self.slider.value())

    def set_value(self, value: float, emit: bool = True) -> None:
        self.slider.blockSignals(not emit)
        self.slider.setValue(int(round(max(MIN_VALUE, min(MAX_VALUE, value)))))
        self.slider.blockSignals(False)
        self.value_label.setText(str(self.slider.value()))

    # ------------------------------------------------------------- 内部
    def _on_value_changed(self, value: int) -> None:
        self.value_label.setText(str(value))
        self.thicknessChanged.emit(float(value))
