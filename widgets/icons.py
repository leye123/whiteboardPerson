"""运行时绘制的矢量图标（widgets/icons.py）。

为什么不用 png/svg 资源文件：

* 打包（PyInstaller/Nuitka）时不必额外处理资源路径；
* 图标可以随浅色/深色主题重新着色，选中态自动用强调色高亮；
* 不引入任何第三方依赖。

两个必须注意的实现细节（都踩过坑）：

1. **一个 QIcon 里不要混入设置了 devicePixelRatio 的位图。**
   QIcon 只按“位图的设备尺寸”挑选最接近的一张，且永远不会向上放大。
   如果同时放入 20x20(DPR=1) 和 40x40(DPR=2)，在 125% 缩放的屏幕上
   （需要 30 设备像素）Qt 可能挑中 40x40(DPR=2)，其逻辑尺寸变成 32，
   在 24 像素的按钮里就被画得又小又偏。这里的做法是：用**多个离散尺寸**
   （16/20/24/30/32/40/48/64）且 DPR 一律为 1，让 Qt 总能拿到接近的尺寸。
2. **图形必须闭合、坐标必须留在安全区内。**
   画「笔」「橡皮」这类轮廓时要用 ``close=True`` 把多边形闭合，
   否则线条会有缺口（看起来“显示不全”）；同时所有坐标限制在
   2.2~17.8 之间，给线宽和圆帽留出余量，避免贴边被裁。

对外接口：:func:`tool_icon` / :func:`app_icon`。
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

# 选中态强调色（与 QSS 中的主色保持一致）
ACCENT = {"light": QColor("#1a4fb4"), "dark": QColor("#8ab4ff")}
FOREGROUND = {"light": QColor("#2f3439"), "dark": QColor("#d7dbe0")}

# 逻辑画布尺寸：所有图形按 20x20 的坐标系绘制
BOX = 20.0
# 线宽（逻辑单位）。1.7/20 ≈ 8.5%，在 16~64 像素下都清晰
STROKE = 1.7
# 内容安全区：坐标不超过这个范围，避免线宽+圆帽顶到画布边缘被裁
SAFE_LO, SAFE_HI = 2.2, 17.8
# QIcon 里准备的离散像素尺寸（不设置 DPR）
ICON_SIZES: Tuple[int, ...] = (16, 20, 24, 30, 32, 40, 48, 64)


# ------------------------------------------------------------------ 绘图小工具


def _line(p: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _poly(p: QPainter, points: Iterable[Tuple[float, float]],
          close: bool = False, fill: bool = False) -> None:
    """画折线/多边形。

    ``close=True`` 时把首点补到末尾：QPainter.drawPolygon 并不会自动闭合，
    不闭合的轮廓看上去就像“线条少了一段”。
    """
    pts = [QPointF(x, y) for x, y in points]
    if close and pts:
        pts.append(QPointF(pts[0]))
    poly = QPolygonF(pts)
    if fill:
        p.save()
        p.setBrush(p.pen().color())
        p.drawPolygon(poly)
        p.restore()
    else:
        p.drawPolygon(poly)


def _rect(p: QPainter, x: float, y: float, w: float, h: float,
          radius: float = 0.0) -> None:
    if radius > 0:
        p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
    else:
        p.drawRect(QRectF(x, y, w, h))


def _ellipse(p: QPainter, x: float, y: float, w: float, h: float) -> None:
    p.drawEllipse(QRectF(x, y, w, h))


def _path(p: QPainter, builder: Callable[[QPainterPath], None]) -> None:
    path = QPainterPath()
    builder(path)
    p.drawPath(path)


def _arc(p: QPainter, rect: QRectF, start_deg: float, sweep_deg: float) -> None:
    """画一段椭圆弧。

    先显式 ``moveTo`` 到弧的起点再 ``arcTo``：在空路径上直接调用 ``arcTo``
    时，起点是没有定义的，Qt 会补出多余的连线（表现为从画布左上角拉出一条杂线）。
    """
    def build(path: QPainterPath) -> None:
        rad = math.radians(start_deg)
        cx, cy = rect.center().x(), rect.center().y()
        path.moveTo(cx + rect.width() / 2 * math.cos(rad),
                    cy - rect.height() / 2 * math.sin(rad))
        path.arcTo(rect, start_deg, sweep_deg)

    _path(p, build)


def _head(p: QPainter, tip: Tuple[float, float], direction: Tuple[float, float],
          length: float = 4.2, width: float = 3.0) -> None:
    """在 ``tip`` 处画一个指向 ``direction`` 的实心箭头。"""
    dx, dy = direction
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    px, py = -dy, dx
    base_x, base_y = tip[0] - dx * length, tip[1] - dy * length
    _poly(p, [
        tip,
        (base_x + px * width / 2, base_y + py * width / 2),
        (base_x - px * width / 2, base_y - py * width / 2),
    ], close=True, fill=True)


def _point_on_circle(rect: QRectF, angle_deg: float) -> QPointF:
    rad = math.radians(angle_deg)
    return QPointF(rect.center().x() + rect.width() / 2 * math.cos(rad),
                   rect.center().y() - rect.height() / 2 * math.sin(rad))


def _tangent(angle_deg: float, ccw: bool = True) -> Tuple[float, float]:
    """圆上某点沿逆/顺时针方向的切线（用于摆箭头）。"""
    rad = math.radians(angle_deg)
    tx, ty = -math.sin(rad), -math.cos(rad)      # θ 增大方向
    return (tx, ty) if ccw else (-tx, -ty)


# ------------------------------------------------------------------ 绘图工具类图标


def _pen(p: QPainter) -> None:
    """钢笔：笔尖 + 笔杆（闭合五边形）+ 中间的分界线。"""
    tip = (4.0, 15.4)
    _poly(p, [tip, (9.0, 13.4), (16.4, 6.0), (13.0, 2.6), (5.6, 10.0)], close=True)
    _line(p, 5.6, 10.0, 9.0, 13.4)


def _eraser(p: QPainter) -> None:
    """橡皮：倾斜的方块 + 分界线 + 底边线。"""
    # 以 (10,10) 为中心、方向 45° 的矩形，长 11、宽 6
    ux, uy = 0.7071, -0.7071
    vx, vy = 0.7071, 0.7071
    cx, cy = 10.0, 9.6
    half_l, half_w = 5.4, 3.0

    def corner(su: float, sv: float) -> Tuple[float, float]:
        return (cx + ux * half_l * su + vx * half_w * sv,
                cy + uy * half_l * su + vy * half_w * sv)

    a, b = corner(-1, -1), corner(1, -1)
    c, d = corner(1, 1), corner(-1, 1)
    _poly(p, [a, b, c, d], close=True)
    # 分界线（把橡皮头与握把分开）
    seam_u = 0.15
    _line(p, cx + ux * half_l * seam_u + vx * half_w,
          cy + uy * half_l * seam_u + vy * half_w,
          cx + ux * half_l * seam_u - vx * half_w,
          cy + uy * half_l * seam_u - vy * half_w)
    # 底边线（橡皮放在纸面上的感觉）
    _line(p, 5.0, 17.4, 15.0, 17.4)


def _rect_shape(p: QPainter) -> None:
    _rect(p, 3.6, 5.0, 12.8, 10.0, 1.4)


def _ellipse_shape(p: QPainter) -> None:
    _ellipse(p, 3.6, 5.0, 12.8, 10.0)


def _line_shape(p: QPainter) -> None:
    _line(p, 4.2, 15.8, 15.8, 4.2)


def _arrow(p: QPainter) -> None:
    _line(p, 4.2, 15.8, 13.6, 6.4)
    _head(p, (16.2, 3.8), (1.0, -1.0), length=4.6, width=3.4)


def _pan(p: QPainter) -> None:
    """「拖动画布」图标：四向箭头（十字 + 四个箭头）。

    这是最直观的「拖动」符号，且各尺寸下都清晰。想要 Photoshop 那种张开的
    手型，把 :data:`_DRAW` 里 ``"pan"`` 的值换成 :func:`_hand` 即可。
    """
    _line(p, 10.0, 4.2, 10.0, 15.8)
    _line(p, 4.2, 10.0, 15.8, 10.0)
    _head(p, (10.0, 3.2), (0.0, -1.0), length=3.6, width=2.4)
    _head(p, (10.0, 16.8), (0.0, 1.0), length=3.6, width=2.4)
    _head(p, (3.2, 10.0), (-1.0, 0.0), length=3.6, width=2.4)
    _head(p, (16.8, 10.0), (1.0, 0.0), length=3.6, width=2.4)


def _hand(p: QPainter) -> None:
    """备选图标：张开的手（手掌 + 三根手指 + 拇指）。

    手指用圆角竖条而不是逐根描边。坐标是按「描边后仍能看见缝隙」倒推的：
    线宽 1.7 会让每根手指向外扩 0.85，所以「中心距 − 条宽」要留出 3.0
    （净缝隙约 1.3 单位）；手指下端停在掌心上沿，否则会在掌心留下小凸起。
    """
    for x in (4.0, 9.0, 14.0):
        _rect(p, x, 3.8, 2.0, 5.6, 1.0)     # 三根手指，中心距 5.0，止于掌心
    _rect(p, 3.6, 9.4, 12.8, 7.2, 2.6)      # 手掌
    _rect(p, 2.6, 10.8, 2.2, 4.6, 1.1)      # 拇指


def _text(p: QPainter) -> None:
    _line(p, 4.8, 5.2, 15.2, 5.2)
    _line(p, 10.0, 5.2, 10.0, 15.0)
    _line(p, 7.4, 15.0, 12.6, 15.0)


def _select(p: QPainter) -> None:
    """鼠标指针箭头（闭合轮廓）。"""
    _poly(p, [
        (5.6, 3.2), (5.6, 15.4), (8.7, 12.4), (10.8, 16.6),
        (12.7, 15.7), (10.6, 11.5), (14.8, 11.2),
    ], close=True)


# ------------------------------------------------------------------ 编辑类图标


def _undo(p: QPainter) -> None:
    """逆时针圆弧箭头：开口朝左上方，箭头落在正上方并指向左（经典撤销造型）。"""
    rect = QRectF(4.4, 4.4, 11.2, 11.2)
    start, sweep = 200.0, 250.0          # 200° → 450°(=90°，正上方)
    _arc(p, rect, start, sweep)
    end_angle = start + sweep
    tip = _point_on_circle(rect, end_angle)
    _head(p, (tip.x(), tip.y()), _tangent(end_angle, ccw=True),
          length=4.2, width=3.2)


def _redo(p: QPainter) -> None:
    """顺时针圆弧箭头（undo 的水平镜像）。"""
    rect = QRectF(4.4, 4.4, 11.2, 11.2)
    start, sweep = 340.0, -250.0         # 340° → 90°，与 undo 对称
    _arc(p, rect, start, sweep)
    end_angle = start + sweep
    tip = _point_on_circle(rect, end_angle)
    _head(p, (tip.x(), tip.y()), _tangent(end_angle, ccw=False),
          length=4.2, width=3.2)


def _new(p: QPainter) -> None:
    """带折角的新文档。"""
    _poly(p, [(5.2, 3.0), (11.6, 3.0), (15.0, 6.4), (15.0, 17.0),
              (5.2, 17.0)], close=True)
    _poly(p, [(11.6, 3.0), (11.6, 6.4), (15.0, 6.4)])


def _open(p: QPainter) -> None:
    _poly(p, [(3.2, 15.8), (3.2, 5.0), (8.2, 5.0), (9.6, 7.2), (16.8, 7.2),
              (16.8, 15.8)], close=True)
    _line(p, 3.2, 10.2, 16.8, 10.2)


def _save(p: QPainter) -> None:
    """软盘。"""
    _rect(p, 4.0, 3.4, 12.0, 13.2, 1.4)
    _rect(p, 7.0, 3.4, 6.0, 4.4)
    _rect(p, 6.6, 11.0, 6.8, 5.6)


def _export(p: QPainter) -> None:
    """导出：托盘 + 向下的箭头。"""
    _poly(p, [(3.6, 11.6), (3.6, 16.6), (16.4, 16.6), (16.4, 11.6)])
    _line(p, 10.0, 3.2, 10.0, 11.4)
    _head(p, (10.0, 13.2), (0.0, 1.0), length=3.8, width=2.8)


def _trash(p: QPainter) -> None:
    """垃圾桶：桶盖 + 提手 + 桶身（线条不宜过多，小尺寸会糊成一团）。"""
    _line(p, 3.6, 5.8, 16.4, 5.8)
    _line(p, 7.8, 3.2, 12.2, 3.2)
    _poly(p, [(5.6, 5.8), (6.6, 16.8), (13.4, 16.8), (14.4, 5.8)], close=True)
    _line(p, 10.0, 8.6, 10.0, 14.4)


def _image(p: QPainter) -> None:
    """图片：画框 + 一个山形 + 一个小太阳（太阳要与山错开，否则小尺寸会糊）。"""
    _rect(p, 3.4, 4.8, 13.2, 10.4, 1.4)
    _poly(p, [(5.0, 13.6), (10.2, 8.8), (13.4, 12.6)])
    _ellipse(p, 5.6, 6.6, 2.0, 2.0)


def _fit(p: QPainter) -> None:
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        cx = 5.0 if sx > 0 else 15.0
        cy = 5.0 if sy > 0 else 15.0
        _line(p, cx, cy, cx + sx * 3.4, cy)
        _line(p, cx, cy, cx, cy + sy * 3.4)
    _rect(p, 7.6, 7.6, 4.8, 4.8, 0.6)


def _zoom(p: QPainter, plus: bool) -> None:
    _ellipse(p, 3.6, 3.6, 9.6, 9.6)
    _line(p, 12.0, 12.0, 16.4, 16.4)
    _line(p, 5.8, 8.4, 11.0, 8.4)
    if plus:
        _line(p, 8.4, 5.8, 8.4, 11.0)


def _page(p: QPainter) -> None:
    """新建页面：文档 + 加号。"""
    _poly(p, [(4.6, 3.0), (11.0, 3.0), (14.4, 6.4), (14.4, 17.0),
              (4.6, 17.0)], close=True)
    _poly(p, [(11.0, 3.0), (11.0, 6.4), (14.4, 6.4)])
    _line(p, 9.5, 10.6, 9.5, 14.6)
    _line(p, 7.5, 12.6, 11.5, 12.6)


def _theme(p: QPainter) -> None:
    """主题切换：圆形 + 右半实心（对比度图标，比画月牙更清晰）。"""
    rect = QRectF(3.8, 3.8, 12.4, 12.4)
    _ellipse(p, rect.x(), rect.y(), rect.width(), rect.height())

    def build(path: QPainterPath) -> None:
        path.moveTo(_point_on_circle(rect, 90.0))
        path.arcTo(rect, 90.0, -180.0)
        path.closeSubpath()

    p.save()
    p.setBrush(p.pen().color())
    _path(p, build)
    p.restore()


_DRAW: Dict[str, Callable[[QPainter], None]] = {
    "pan": _pan,          # 工具栏「拖动画布」用这个
    "hand": _hand,        # 备选：手型平移图标
    "pen": _pen,
    "eraser": _eraser,
    "rect": _rect_shape,
    "ellipse": _ellipse_shape,
    "line": _line_shape,
    "arrow": _arrow,
    "text": _text,
    "select": _select,
    "undo": _undo,
    "redo": _redo,
    "new": _new,
    "open": _open,
    "save": _save,
    "export": _export,
    "image": _image,
    "trash": _trash,
    "fit": _fit,
    "zoom_in": lambda p: _zoom(p, True),
    "zoom_out": lambda p: _zoom(p, False),
    "page": _page,
    "moon": _theme,
    "theme": _theme,
}

# 全部可用图标名（供文档与自检脚本使用）
ICON_NAMES = tuple(_DRAW.keys())


# ------------------------------------------------------------------ 对外接口


def _render(name: str, color: QColor, size: int) -> QPixmap:
    """把图标绘制到 size x size 的位图上（DPR 保持 1，由 QIcon 负责挑选）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = size / BOX
    painter.scale(scale, scale)
    pen = QPen(color, STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _DRAW.get(name, _rect_shape)(painter)
    painter.end()
    return pixmap


def tool_icon(name: str, theme: str = "light", sizes: Tuple[int, ...] = None) -> QIcon:
    """按主题生成图标；未知名称回退为矩形图标。

    同一个 QIcon 内放入多个**离散尺寸**（DPR 全为 1）：
    这样在 100%/125%/150% 缩放下 Qt 都能选到接近的位图，
    不会出现“把 20px 的图放大后用”或者“挑错尺寸被裁掉”的问题。
    """
    fg = FOREGROUND.get(theme, FOREGROUND["light"])
    on = ACCENT.get(theme, ACCENT["light"])
    icon = QIcon()
    for size in (sizes or ICON_SIZES):
        normal = _render(name, fg, size)
        highlight = _render(name, on, size)
        icon.addPixmap(normal, QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(highlight, QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(highlight, QIcon.Mode.Active, QIcon.State.Off)
        icon.addPixmap(highlight, QIcon.Mode.Selected, QIcon.State.Off)
        icon.addPixmap(highlight, QIcon.Mode.Selected, QIcon.State.On)
    return icon


def app_icon(theme: str = "light", sizes: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)) -> QIcon:
    """应用窗口图标：一块带笔迹的白板。"""
    fg = FOREGROUND.get(theme, FOREGROUND["light"])
    accent = ACCENT.get(theme, ACCENT["light"])
    icon = QIcon()
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(size / BOX, size / BOX)
        painter.setPen(QPen(fg, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _rect(painter, 1.8, 3.2, 16.4, 13.6, 2.0)
        painter.setPen(QPen(accent, 1.9))
        path = QPainterPath()
        path.moveTo(4.6, 13.4)
        path.cubicTo(7.4, 7.6, 10.6, 14.8, 15.4, 7.0)
        painter.drawPath(path)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
