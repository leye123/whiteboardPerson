"""页面切换器：上一页 / 下一页 / 新建页 / 删除页 + 页码显示。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)


class PageNavigator(QWidget):
    prevRequested = Signal()
    nextRequested = Signal()
    newRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._prev_btn = QToolButton(self)
        self._prev_btn.setText("◀ 上一页")
        self._prev_btn.setToolTip("上一页 (Ctrl+PageUp)")
        self._prev_btn.clicked.connect(self.prevRequested)

        self._next_btn = QToolButton(self)
        self._next_btn.setText("下一页 ▶")
        self._next_btn.setToolTip("下一页 (Ctrl+PageDown)")
        self._next_btn.clicked.connect(self.nextRequested)

        self.page_label = QLabel("第 1 / 1 页", self)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(90)

        self._new_btn = QPushButton("＋ 新建", self)
        self._new_btn.setToolTip("新建页面 (Ctrl+N)")
        self._new_btn.clicked.connect(self.newRequested)

        self._del_btn = QPushButton("删除", self)
        self._del_btn.setToolTip("删除当前页 (Ctrl+W)")
        self._del_btn.clicked.connect(self.deleteRequested)

        layout.addWidget(self._prev_btn)
        layout.addWidget(self.page_label)
        layout.addWidget(self._next_btn)
        layout.addWidget(self._new_btn)
        layout.addWidget(self._del_btn)

    # ------------------------------------------------------------- API
    def set_page(self, index: int, total: int) -> None:
        """index 从 0 开始。"""
        self.page_label.setText(f"第 {index + 1} / {total} 页")
        self._prev_btn.setEnabled(index > 0)
        self._next_btn.setEnabled(index < total - 1)
        self._del_btn.setEnabled(total > 1)
