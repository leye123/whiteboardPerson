"""图标自检 + 生成图标总览图（scripts/check_icons.py）。

做两件事：

1. **逐图标体检**：把每个图标渲染到 ``docs/icons.png``（带名称标注的总览图），
   并检查「墨迹」是否贴到画布边缘（贴边 = 被裁切）、是否为空、覆盖率是否合理；
2. **工具栏实测**：真的建一个主窗口，逐个 QToolButton 抓图，测量图标实际
   占用的区域，确认在按钮里没有被裁掉。

在 HiDPI（125%/150% 缩放）下最容易出问题，可以这样模拟：

    set QT_SCALE_FACTOR=1.25 && python scripts/check_icons.py

退出码 0 = 全部正常，1 = 有图标异常（会打印具体是哪个）。
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

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QToolBar,
    QToolButton,
    QWidget,
)

from widgets import icons  # noqa: E402

# 安全区：图标内容不应越过这个范围，否则按钮/画布边缘会把笔画切掉
SAFE_MIN = 1
PROBE_SIZES = (16, 20, 24, 32, 48, 64)
EDGE_TOLERANCE = 0        # 允许贴边的像素数（0 = 一点都不许贴）


def _ink_bbox(image: QImage, alpha_threshold: int = 40):
    """返回非透明像素的包围盒 (x1, y1, x2, y2) 与像素数。"""
    xs, ys, count = [], [], 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > alpha_threshold:
                xs.append(x)
                ys.append(y)
                count += 1
    if not xs:
        return None, 0
    return (min(xs), min(ys), max(xs), max(ys)), count


def _foreground_bbox(image: QImage, tolerance: int = 26):
    """按“与背景色不同”找前景（按钮抓图里按钮本身有背景填充和边框）。

    需要忽略两种“非图标”的颜色：出现次数最多的颜色（背景）以及四个角上的
    颜色（QSS 描边，选中态按钮有一圈强调色边框）。否则整块背景/边框会被
    当成前景，误报“图标贴边”。
    """
    counts = {}
    for y in range(image.height()):
        for x in range(image.width()):
            counts[image.pixel(x, y)] = counts.get(image.pixel(x, y), 0) + 1

    ignored = []
    if counts:
        ignored.append(QColor.fromRgba(max(counts.items(), key=lambda kv: kv[1])[0]))
    for corner in ((0, 0), (image.width() - 1, 0),
                   (0, image.height() - 1),
                   (image.width() - 1, image.height() - 1)):
        ignored.append(image.pixelColor(*corner))

    def is_background(color: QColor) -> bool:
        return any(abs(color.red() - ref.red()) <= tolerance
                   and abs(color.green() - ref.green()) <= tolerance
                   and abs(color.blue() - ref.blue()) <= tolerance
                   for ref in ignored)

    xs, ys, count = [], [], 0
    for y in range(image.height()):
        for x in range(image.width()):
            if not is_background(image.pixelColor(x, y)):
                xs.append(x)
                ys.append(y)
                count += 1
    if not xs:
        return None, 0
    return (min(xs), min(ys), max(xs), max(ys)), count


def check_icon(rendered: QImage, name: str, size: int) -> list:
    """返回该图标在当前尺寸下的问题列表。"""
    problems = []
    bbox, count = _ink_bbox(rendered)
    if bbox is None:
        return [f"{name}@{size}: 空白图标"]
    x1, y1, x2, y2 = bbox
    if x1 <= SAFE_MIN - 1 - EDGE_TOLERANCE or y1 <= SAFE_MIN - 1 - EDGE_TOLERANCE \
            or x2 >= size - SAFE_MIN + EDGE_TOLERANCE or y2 >= size - SAFE_MIN + EDGE_TOLERANCE:
        problems.append(f"{name}@{size}: 墨迹贴边(被裁切) bbox={bbox} 画布={size}x{size}")
    coverage = count / float(size * size)
    if coverage > 0.55:
        problems.append(f"{name}@{size}: 墨迹过多({coverage:.0%})，可能糊成一团")
    return problems


def render_sheet(sizes, out_path: str) -> None:
    """把所有图标按名称排成一张总览图，便于人工核对。"""
    names = list(icons.ICON_NAMES)
    cell = max(sizes) + 28
    cols = 7
    rows = (len(names) + cols - 1) // cols
    width = cols * cell * len(sizes) // 2 + cell
    width = cols * (max(sizes) + 46) + 20
    height = rows * (max(sizes) + 34) + 20

    sheet = QImage(width, height, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#ffffff"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont()
    font.setPixelSize(12)
    painter.setFont(font)

    for index, name in enumerate(names):
        row, col = divmod(index, cols)
        x = 20 + col * (max(sizes) + 46)
        y = 20 + row * (max(sizes) + 34)
        for size in sizes:
            icon = icons.tool_icon(name, "light")
            pixmap = icon.pixmap(QSize(size, size), 1.0)
            painter.drawPixmap(x, y, pixmap)
            x += size + 10
        painter.setPen(QColor("#333333"))
        painter.drawText(20 + col * (max(sizes) + 46), y + max(sizes) + 18, name)

    painter.end()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sheet.save(out_path, "PNG")
    print(f"图标总览图已保存：{out_path}（{sheet.width()}x{sheet.height()}）")


def check_toolbar(size: int = 24) -> list:
    """真实建窗，抓每个工具按钮，检查图标在按钮内是否被裁切、工具栏是否溢出。"""
    from main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 820)
    win.show()
    app = QApplication.instance()
    app.processEvents()

    problems = []
    toolbar = win.findChild(QToolBar, "tools_bar")
    if toolbar is None:
        return ["找不到工具栏 tools_bar"]

    # 工具栏溢出：Qt 会用一个 “>>” 扩展按钮把放不下的项藏起来
    extension = toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
    if extension is not None and extension.isVisible():
        problems.append("工具栏溢出：有按钮被收进 “>>” 扩展菜单里（窗口太窄或按钮太宽）")
    print(f"  工具栏: 可见项 {len(toolbar.actions())}，"
          f"宽度={toolbar.width()}，sizeHint={toolbar.sizeHint().width()}，"
          f"iconSize={toolbar.iconSize().width()}x{toolbar.iconSize().height()}，"
          f"溢出按钮={'有' if extension is not None and extension.isVisible() else '无'}")

    # 工具栏会用剩余空间拉伸“最后一个可扩展控件”（默认 sizePolicy 允许变宽），
    # 结果就是「粗细」标签留在左边、滑块被推到窗口最右边。这里逐一核对。
    for widget in toolbar.findChildren(QWidget):
        if widget.parent() is not toolbar or not widget.isVisible():
            continue
        hint = widget.sizeHint().width()
        actual = widget.width()
        stretched = hint > 0 and actual > hint + 40
        print(f"  控件 {type(widget).__name__:20} 宽={actual:4d} sizeHint={hint:4d}"
              f"{'  ← 被拉伸' if stretched else ''}")
        if stretched:
            problems.append(
                f"工具栏控件 {type(widget).__name__} 被拉伸：宽 {actual} >> sizeHint {hint}")

    buttons = [b for b in toolbar.findChildren(QToolButton)
               if b.defaultAction() is not None
               and b.defaultAction() in win.tool_actions.values()]
    if len(buttons) != len(win.tool_actions):
        problems.append(f"工具栏按钮数量异常：{len(buttons)} != {len(win.tool_actions)}")
    for button in buttons:
        key = str(button.defaultAction().data())
        # 暂时去掉按钮自身的 QSS（背景/边框），这样抓到的就是纯图标，
        # 否则选中态按钮的强调色边框会被误判成“图标贴边”。
        original_style = button.styleSheet()
        button.setStyleSheet("QToolButton { background: transparent; border: none; }")
        app.processEvents()
        image = button.grab().toImage()
        button.setStyleSheet(original_style)
        bbox, count = _foreground_bbox(image)
        w, h = image.width(), image.height()
        if bbox is None:
            problems.append(f"按钮 {key}: 抓图无内容")
            print(f"  按钮 {key:9} {w:3d}x{h:3d}  无前景内容!")
            continue
        x1, y1, x2, y2 = bbox
        clipped = x1 <= 0 or y1 <= 0 or x2 >= w - 1 or y2 >= h - 1
        print(f"  按钮 {key:9} {w:3d}x{h:3d}  图标 bbox={bbox} "
              f"尺寸={x2 - x1 + 1}x{y2 - y1 + 1} 像素={count}"
              f"{'  ← 贴边(可能被裁)' if clipped else ''}")
        if clipped:
            problems.append(f"按钮 {key}: 图标贴到按钮边缘(可能被裁) bbox={bbox} 按钮={w}x{h}")
    win.close()
    return problems


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    print(f"设备像素比 = {app.devicePixelRatio():.2f}，"
          f"平台 = {app.platformName()}")

    print("\n[1] 逐图标体检")
    problems = []
    for name in icons.ICON_NAMES:
        row = []
        for size in PROBE_SIZES:
            pixmap = icons.tool_icon(name, "light").pixmap(QSize(size, size), 1.0)
            row.append(f"{size}:{pixmap.width()}x{pixmap.height()}")
            problems.extend(check_icon(pixmap.toImage(), name, size))
        bbox, count = _ink_bbox(
            icons.tool_icon(name, "light").pixmap(QSize(64, 64), 1.0).toImage())
        print(f"  {name:9} 64px bbox={bbox} 像素={count:4d}  各尺寸请求→实际 {' '.join(row)}")

    print("\n[2] 工具栏实测")
    problems.extend(check_toolbar())

    render_sheet((16, 24, 32), os.path.join(ROOT, "docs", "icons.png"))

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for item in problems:
            print("  -", item)
        return 1
    print("图标检查全部通过（无空白、无贴边裁切、覆盖率合理）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
