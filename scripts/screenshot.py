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

from canvas.items import (  # noqa: E402
    ARROW_BOTH,
    ARROW_END,
    EllipseItem,
    LineItem,
    PolygonShapeItem,
    RectItem,
    StrokeItem,
    TextItem,
    default_radius,
    make_label,
)
from main_window import MainWindow  # noqa: E402


def _add_sample_content(win: MainWindow) -> None:
    """往当前页塞一点示例内容，让截图有代表性。

    坐标按**当前可见区域**的相对比例算，而不是写死场景坐标 ——
    视图启动时以场景原点为中心，写死的坐标很容易落到窗口外
    （之前五角星就整个跑到可视区右边去了，截图上根本看不到）。
    """
    import math

    scene = win.view.scene()
    view = win.view
    area = view.mapToScene(view.viewport().rect()).boundingRect()
    left, top = area.left(), area.top()
    width, height = area.width(), area.height()

    def box(fx: float, fy: float, fw: float, fh: float) -> QRectF:
        return QRectF(left + width * fx, top + height * fy,
                      width * fw, height * fh)

    def point(fx: float, fy: float) -> QPointF:
        return QPointF(left + width * fx, top + height * fy)

    ink = QColor("#1a4fb4")
    warm = QColor("#e2574c")
    green = QColor("#2f9e6f")
    orange = QColor("#e8912a")
    dark = win._theme == "dark"
    title_color = QColor("#e6e9ee") if dark else QColor("#1f2328")
    note_color = QColor("#a9b1bb") if dark else QColor("#5b6169")

    # 手写笔迹：一段正弦曲线
    points = []
    for index in range(90):
        points.append(QPointF(left + width * (0.06 + 0.44 * index / 89.0),
                              top + height * (0.34 + 0.09 * math.sin(index / 9.0))))
    scene.addItem(StrokeItem(points, ink, 3.0))

    # 圆角矩形（圆角半径随尺寸自动算）/ 椭圆 / 五角星
    rounded = box(0.06, 0.50, 0.20, 0.24)
    rounded_item = RectItem(rounded, warm, 2.5, radius=default_radius(rounded))
    # 图形内部也能写字，字体数据跟着图形一起保存（v1.3.0）
    rounded_item.set_raw_label(make_label("图形内的文字", pixel_size=20,
                                          color=warm, align="center"))
    scene.addItem(rounded_item)
    scene.addItem(EllipseItem(box(0.30, 0.50, 0.18, 0.24), green, 2.5))
    scene.addItem(PolygonShapeItem("star", box(0.52, 0.47, 0.16, 0.28), orange, 2.5))

    # 虚线箭头 / 双向点线箭头
    scene.addItem(LineItem(point(0.72, 0.53), point(0.94, 0.68), ink, 2.0,
                           arrow=ARROW_END, line_style="dash"))
    scene.addItem(LineItem(point(0.72, 0.72), point(0.94, 0.56), warm, 2.0,
                           arrow=ARROW_BOTH, line_style="dot"))

    title = TextItem("我的白板 · PySide6", title_color, 34)
    title.setPos(point(0.06, 0.10))
    scene.addItem(title)

    # 自动换行的说明文字（折行宽度是排版参数之一）
    note = TextItem("画笔 · 橡皮（擦断）· 11 种形状 · 字体框（双击输入）· 图形内文字\n"
                    "选择/框选 · 参数侧边栏 · 拖动画布 · 多页面 · 撤销重做 · PNG 导出",
                    note_color, 18, wrap=True, text_width=width * 0.5)
    note.setPos(point(0.06, 0.80))
    scene.addItem(note)

    # 字体框：拖出框 → 双击后在右侧参数侧边栏输入（v1.3.0 的输入方式）
    boxed = TextItem("字体框：拖出大小，双击输入", note_color, 18, wrap=True,
                     text_width=width * 0.26, box_height=height * 0.12)
    boxed.setPos(point(0.62, 0.12))
    scene.addItem(boxed)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("我的白板")
    app.setOrganizationName("WhiteboardPyside")

    win = MainWindow()
    width, height = (int(v) for v in args.size.lower().split("x"))
    win.resize(width, height)
    win._apply_theme(args.theme)
    win.show()
    # 必须先 show + 处理事件：show 之前 viewport 还没有真实尺寸，
    # 按“可见区域”放置的示例内容会挤在一个 100×30 的角落里（截图上一小团）。
    app.processEvents()
    if not args.no_content:
        _add_sample_content(win)
        app.processEvents()

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
