"""把图标以 ASCII 点阵打印出来（scripts/icon_ascii.py）。

没有图形界面／无法看图时，用它在终端里逐像素"看"图标长什么样，
排查「线条断开」「糊成一团」「贴边被裁」这类问题非常有效。

用法：
    python scripts/icon_ascii.py                 # 全部图标，30 像素
    python scripts/icon_ascii.py 16              # 指定尺寸（16 像素最考验细节）
    python scripts/icon_ascii.py 40 pen eraser   # 只看某几个图标
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from widgets import icons  # noqa: E402

SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 30
NAMES = sys.argv[2:] or list(icons.ICON_NAMES)

for name in NAMES:
    pixmap = icons.tool_icon(name, "light").pixmap(QSize(SIZE, SIZE), 1.0)
    image = pixmap.toImage()
    print(f"--- {name} ({image.width()}x{image.height()}) ---")
    for y in range(image.height()):
        row = "".join(
            "#" if image.pixelColor(x, y).alpha() > 110 else
            ("+" if image.pixelColor(x, y).alpha() > 30 else ".")
            for x in range(image.width()))
        print("   " + row)
