"""线型选择器（widgets/line_style_picker.py）。

工具栏上的一个小下拉框：实线 / 虚线 / 点线 / 点划线。
选中的线型会立即作用到**画笔**与形状工具，因此：

* 手绘笔迹也能是虚线/点线（预览与最终笔迹一致）；
* 任何形状都能画成虚线（矩形、椭圆、三角形、箭头都行），
  而不只是「虚线」「虚线箭头」那两种固定图形。

下拉项自带一段该线型的预览线，选之前就能看出效果。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QSizePolicy

from canvas.items import DEFAULT_LINE_STYLE, LINE_STYLE_LABELS, LINE_STYLES
from widgets import icons

# 下拉框里的顺序（也是「由粗到细」的常用顺序）
STYLE_ORDER = ("solid", "dash", "dot", "dash_dot")


class LineStylePicker(QComboBox):
    """线型下拉框：值变化时发出 :attr:`lineStyleChanged`。"""

    lineStyleChanged = Signal(str)

    def __init__(self, current: str = DEFAULT_LINE_STYLE, parent=None) -> None:
        super().__init__(parent)
        # 工具栏会拉伸最后一个控件，这里锁死宽度（同 ThicknessSlider 的坑）
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("线型：画笔手绘与直线/箭头/矩形/椭圆等所有描边图形都用它")
        self.setMinimumWidth(96)
        for name in STYLE_ORDER:
            if name not in LINE_STYLES:
                continue
            self.addItem(LINE_STYLE_LABELS[name], name)
        self.set_current(current)
        self.currentIndexChanged.connect(self._on_index_changed)

    # ------------------------------------------------------------- API
    def current_style(self) -> str:
        return str(self.currentData() or DEFAULT_LINE_STYLE)

    def set_current(self, name: str, emit: bool = True) -> None:
        index = self.findData(name if name in LINE_STYLES else DEFAULT_LINE_STYLE)
        self.blockSignals(not emit)
        self.setCurrentIndex(max(0, index))
        self.blockSignals(False)
        self._refresh_icons()

    def set_theme(self, theme: str) -> None:
        """主题切换后重绘下拉项图标。"""
        self._theme = theme
        self._refresh_icons()

    # ------------------------------------------------------------- 内部
    def _on_index_changed(self, _index: int) -> None:
        self.lineStyleChanged.emit(self.current_style())

    def _refresh_icons(self) -> None:
        theme = getattr(self, "_theme", "light")
        for index in range(self.count()):
            name = str(self.itemData(index))
            self.setItemIcon(
                index, icons.tool_icon(f"line_style_{name}", theme,
                                       sizes=(16, 20, 24, 32)))
