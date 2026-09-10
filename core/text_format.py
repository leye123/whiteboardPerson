"""文字排版参数（core/text_format.py）。

把「字体、字号、颜色、粗斜体、对齐、自动换行」打包成一个可序列化的对象，
这样：

* 输入文字对话框 → :class:`canvas.items.TextItem` → 设置文件，用的是同一份参数；
* 上次用过的排版会被记住，下次输入文字默认沿用；
* 单元测试可以直接构造 ``TextFormat`` 断言往返结果，不用去戳界面控件。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from PySide6.QtGui import QColor, QFont

DEFAULT_FAMILY = ""              # 空 = 跟随系统默认字体
DEFAULT_PIXEL_SIZE = 16.0
DEFAULT_COLOR = "#000000"
DEFAULT_WRAP = False
DEFAULT_TEXT_WIDTH = 300.0       # 自动换行的折行宽度（场景单位 ≈ 像素）
DEFAULT_ALIGN = "left"
ALIGNS = ("left", "center", "right")
ALIGN_LABELS = {"left": "左对齐", "center": "居中", "right": "右对齐"}


@dataclass
class TextFormat:
    """一条文字的排版参数。"""

    family: str = DEFAULT_FAMILY
    pixel_size: float = DEFAULT_PIXEL_SIZE
    color: QColor = field(default_factory=lambda: QColor(DEFAULT_COLOR))
    bold: bool = False
    italic: bool = False
    wrap: bool = DEFAULT_WRAP
    text_width: float = DEFAULT_TEXT_WIDTH
    align: str = DEFAULT_ALIGN
    font_file: str = ""          # 导入字体的来源文件（可选，便于换机时提示）

    # ------------------------------------------------------------- 基础
    def copy(self, **changes) -> "TextFormat":
        return replace(self, **changes)

    def qfont(self) -> QFont:
        font = QFont(self.family) if self.family else QFont()
        font.setPixelSize(int(max(6, self.pixel_size)))
        font.setBold(bool(self.bold))
        font.setItalic(bool(self.italic))
        return font

    # ------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "pixel_size": float(self.pixel_size),
            "color": QColor(self.color).name(QColor.NameFormat.HexArgb),
            "bold": bool(self.bold),
            "italic": bool(self.italic),
            "wrap": bool(self.wrap),
            "text_width": float(self.text_width),
            "align": self.align if self.align in ALIGNS else DEFAULT_ALIGN,
            "font_file": self.font_file,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TextFormat":
        if not data:
            return cls()
        try:
            pixel_size = float(data.get("pixel_size", DEFAULT_PIXEL_SIZE))
        except (TypeError, ValueError):
            pixel_size = DEFAULT_PIXEL_SIZE
        try:
            text_width = float(data.get("text_width", DEFAULT_TEXT_WIDTH))
        except (TypeError, ValueError):
            text_width = DEFAULT_TEXT_WIDTH
        align = str(data.get("align", DEFAULT_ALIGN))
        return cls(
            family=str(data.get("family", DEFAULT_FAMILY) or ""),
            pixel_size=max(6.0, pixel_size),
            color=QColor(str(data.get("color", DEFAULT_COLOR))),
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            wrap=bool(data.get("wrap", DEFAULT_WRAP)),
            text_width=max(20.0, text_width),
            align=align if align in ALIGNS else DEFAULT_ALIGN,
            font_file=str(data.get("font_file", "") or ""),
        )

    # ------------------------------------------------------------- 与图形项互转
    @classmethod
    def from_item(cls, item) -> "TextFormat":
        font = item.font()
        return cls(
            family=font.family(),
            pixel_size=float(font.pixelSize()) if font.pixelSize() > 0 else DEFAULT_PIXEL_SIZE,
            color=QColor(item.defaultTextColor()),
            bold=font.bold(),
            italic=font.italic(),
            wrap=bool(item.is_wrapped()),
            text_width=float(item.text_width()),
            align=item.text_align(),
            font_file=item.font_file(),
        )
