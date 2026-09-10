"""把主窗口渲染成 PNG（scripts/screenshot.py）。

用途：给 README/文档配图，或在没有人工点击的情况下做一次“视觉回归”检查。

用法：
    python scripts/screenshot.py                     # 浅色，输出 docs/screenshot_light.png
    python scripts/screenshot.py --theme dark
    python scripts/screenshot.py --out shot.png --no-content

脚本会真的创建窗口、填充示例内容、截屏后退出；加 ``--offscreen`` 可在无显示器
的环境（CI）里运行。
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

parser = argparse.ArgumentParser()
parser.add_argument("--theme", default="light", choices=("light", "dark"))
parser.add_argument("--out", default=None)
parser.add_argument("--size", default="1280x820")
parser.add_argument("--no-content", action="store_true")
parser.add_argument("--offscreen", action="store_true")
args, _rest = parser.parse_known_args()

if args.offscreen:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from canvas.items import EllipseItem, LineItem, RectItem, StrokeItem, TextItem  # noqa: E402
from main_window import MainWindow  # noqa: E402


def _add_sample_content(win: MainWindow) -> None:
    """往当前页塞一点示例内容，让截图有代表性。"""
    scene = win.view.scene()
    ink = QColor("#1a4fb4")
    warm = QColor("#e2574c")

    # 手写笔迹：一段正弦曲线
    points = []
    for i in range(90):
        x = 120.0 + i * 7.0
        y = 180.0 + 70.0 * __import__("math").sin(i / 9.0)
        points.append(QPointF(x, y))
    scene.addItem(StrokeItem(points, ink, 3.0))

    rect = RectItem(QRectF(140.0, 330.0, 240.0, 140.0), warm, 2.5)
    scene.addItem(rect)
    ellipse = EllipseItem(QRectF(430.0, 330.0, 220.0, 140.0), QColor("#2f9e6f"), 2.5)
    scene.addItem(ellipse)
    scene.addItem(LineItem(QPointF(700.0, 330.0), QPointF(880.0, 470.0), ink, 2.0))
    scene.addItem(LineItem(QPointF(700.0, 470.0), QPointF(880.0, 330.0), warm, 2.0,
                           arrow=True))

    title = TextItem("我的白板 · PySide6", QColor("#1f2328"), 34)
    title.setPos(QPointF(140.0, 90.0))
    scene.addItem(title)
    note = TextItem("画笔 · 橡皮 · 形状 · 文字 · 选择 · 多页面 · 撤销重做",
                    QColor("#5b6169"), 18)
    note.setPos(QPointF(140.0, 545.0))
    scene.addItem(note)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("我的白板")
    app.setOrganizationName("WhiteboardPyside")

    win = MainWindow()
    width, height = (int(v) for v in args.size.lower().split("x"))
    win.resize(width, height)
    win._apply_theme(args.theme)
    if not args.no_content:
        _add_sample_content(win)
    win.show()

    out = args.out or os.path.join(ROOT, "docs", f"screenshot_{args.theme}.png")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    def grab() -> None:
        pixmap = win.grab()
        ok = pixmap.save(out, "PNG")
        print(f"{'已保存' if ok else '保存失败'}: {out} ({pixmap.width()}x{pixmap.height()})")
        app.quit()

    QTimer.singleShot(800, grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
