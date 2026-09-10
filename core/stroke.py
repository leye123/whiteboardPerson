"""笔画数据模型与颜色工具（core/stroke.py）。

这一层只依赖 QtCore/QtGui，不依赖任何窗口部件，因此可以独立测试：

- :class:`Stroke`  —— 一条手绘笔迹的纯数据表示（点列表 + 颜色 + 线宽），
  是持久化格式里最核心的数据结构，也是 `canvas.items.StrokeItem` 的数据来源；
- :func:`build_path` —— 把离散采样点拟合成平滑曲线（中点二次贝塞尔），
  避免快速拖动时出现明显的折线感；
- :func:`qcolor_to_rgba` / :func:`qcolor_from_rgba` —— 颜色在 JSON 中的
  统一表示：``[r, g, b, a]``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Sequence

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainterPath

# ------------------------------------------------------------------ 颜色工具


def qcolor_to_rgba(color: QColor) -> List[int]:
    """QColor -> [r, g, b, a]，用于 JSON 序列化。"""
    if color is None:
        color = QColor(0, 0, 0, 255)
    return [int(color.red()), int(color.green()), int(color.blue()), int(color.alpha())]


def qcolor_from_rgba(value) -> QColor:
    """[r, g, b, a] / "#rrggbb" / QColor -> QColor（容错处理）。"""
    if isinstance(value, QColor):
        return QColor(value)
    if isinstance(value, str):
        color = QColor(value)
        return color if color.isValid() else QColor(0, 0, 0, 255)
    try:
        parts = list(value)
    except TypeError:
        return QColor(0, 0, 0, 255)

    if len(parts) >= 4:
        return QColor(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    if len(parts) == 3:
        return QColor(int(parts[0]), int(parts[1]), int(parts[2]))
    return QColor(0, 0, 0, 255)


# ------------------------------------------------------------------ 平滑曲线


def build_path(points: Sequence[QPointF]) -> QPainterPath:
    """用“中点二次贝塞尔”把采样点连成平滑路径。

    算法：从 p0 出发，对每个中间点 pi 用 ``quadTo(pi, mid(pi, pi+1))``，
    最后连到末点。这样曲线自然穿过相邻点的中点，笔画既平滑又贴合手绘轨迹。
    """
    path = QPainterPath()
    if not points:
        return path

    first = QPointF(points[0])
    if len(points) == 1:
        # 单点：给一个极短线段，保证落笔即成可见圆点
        path.moveTo(first)
        path.lineTo(first.x() + 0.01, first.y() + 0.01)
        return path

    path.moveTo(first)
    if len(points) == 2:
        path.lineTo(points[1])
        return path

    for i in range(1, len(points) - 1):
        current = points[i]
        nxt = points[i + 1]
        mid = QPointF((current.x() + nxt.x()) / 2.0,
                      (current.y() + nxt.y()) / 2.0)
        path.quadTo(current, mid)
    path.lineTo(points[-1])
    return path


# ------------------------------------------------------------------ 擦除切分


def split_by_eraser(points: Sequence[QPointF], center: QPointF, radius: float,
                    origin: QPointF = None) -> List[List[QPointF]]:
    """按橡皮圆把一条笔迹的采样点切成若干段（橡皮擦的核心算法）。

    落在圆内的点被丢掉，连续保留下来的点各自成为一段，于是「擦中间」会把
    一条笔画变成两条，「擦两端」会把它缩短，全擦掉则返回空列表。

    :param points: 笔迹的采样点（图形项局部坐标）
    :param center: 橡皮圆心（场景坐标）
    :param radius: 橡皮半径（场景坐标）
    :param origin: 图形项的 ``pos()``，用来把局部坐标换算到场景坐标
    :return: 若干段点列表；没有碰到时返回 ``[list(points)]``
    """
    origin = QPointF(0.0, 0.0) if origin is None else origin
    radius_sq = float(radius) * float(radius)
    runs: List[List[QPointF]] = []
    current: List[QPointF] = []
    for point in points:
        dx = point.x() + origin.x() - center.x()
        dy = point.y() + origin.y() - center.y()
        if dx * dx + dy * dy <= radius_sq:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(point)
    if current:
        runs.append(current)
    if not runs:
        return []
    return runs


# ------------------------------------------------------------------ 笔画模型


@dataclass
class Stroke:
    """一条手绘笔迹。

    仅承载数据，不负责渲染；`canvas.items.StrokeItem` 会把它变成可绘制的图形项。
    """

    color: QColor = field(default_factory=lambda: QColor(0, 0, 0, 255))
    thickness: float = 2.0
    points: List[QPointF] = field(default_factory=list)

    # ---------------------------------------------------------- 基本操作
    def add_point(self, point: QPointF) -> None:
        self.points.append(QPointF(point))

    def is_empty(self) -> bool:
        return not self.points

    def length(self) -> float:
        """折线总长度（用于过滤“点一下”产生的空笔画）。"""
        total = 0.0
        for a, b in zip(self.points, self.points[1:]):
            total += ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5
        return total

    def bounding_rect(self):
        """外接矩形。

        注意不能用 ``QRectF(p, p)`` 逐点 union：单点矩形是“空矩形”，
        ``united()`` 会把它忽略掉，结果永远是空。这里直接取 min/max。
        """
        from PySide6.QtCore import QRectF

        if not self.points:
            return QRectF()
        xs = [p.x() for p in self.points]
        ys = [p.y() for p in self.points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return QRectF(min_x, min_y, max_x - min_x, max_y - min_y)

    def to_path(self) -> QPainterPath:
        return build_path(self.points)

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "color": qcolor_to_rgba(self.color),
            "thickness": round(float(self.thickness), 3),
            "points": [[round(p.x(), 3), round(p.y(), 3)] for p in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Stroke":
        return cls(
            color=qcolor_from_rgba(data.get("color", [0, 0, 0, 255])),
            thickness=float(data.get("thickness", 2.0)),
            points=[QPointF(x, y) for x, y in data.get("points", [])],
        )

    @classmethod
    def from_points(cls, points: Iterable[QPointF],
                    color: QColor = None, thickness: float = 2.0) -> "Stroke":
        return cls(color=QColor(color) if color is not None else QColor(0, 0, 0, 255),
                   thickness=float(thickness),
                   points=[QPointF(p) for p in points])
