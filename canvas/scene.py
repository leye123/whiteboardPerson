"""白板场景（canvas/scene.py）。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QGraphicsScene

# 网格在屏幕上的最小/最大间距（像素）。缩放时自动切换网格密度，
# 既不会密到糊成一片，也不会疏到看不见。
GRID_MIN_PX = 14.0
GRID_MAX_PX = 56.0
# 单次重绘最多画多少个网格点（防止极端缩放下的卡顿）
GRID_MAX_POINTS = 6000


class WhiteboardScene(QGraphicsScene):
    """管理全部图形项的自定义场景。

    - 场景范围较大（近似无限画布）；
    - drawBackground 绘制浅色点阵网格；
    - 背景/网格颜色可由主题（浅色/深色）动态调整。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-10000.0, -10000.0, 20000.0, 20000.0)
        self._bg_color = QColor("#fbfbfb")
        self._grid_color = QColor(0, 0, 0, 28)
        self._grid_step = 25.0
        # 保持顶层图形项的 Python 引用。
        # 原因：PySide 中图形项的所有权跟随 Python 引用，若只由撤销命令持有，
        # 一旦 QUndoStack.clear() 删掉命令，图形项就会被回收并从场景消失
        # （表现为“删除页面后其它页内容也没了”）。这里由场景兜底持有。
        self._owned_items = []

    # ------------------------------------------------------------- 图形项生命周期
    def addItem(self, item) -> None:
        super().addItem(item)
        if item.parentItem() is None and not any(it is item for it in self._owned_items):
            self._owned_items.append(item)

    def removeItem(self, item) -> None:
        super().removeItem(item)
        for index, owned in enumerate(self._owned_items):
            if owned is item:
                del self._owned_items[index]
                break

    # ------------------------------------------------------------- 主题外观
    def set_background(self, color: QColor) -> None:
        self._bg_color = QColor(color)
        self.invalidate()

    def set_grid_color(self, color: QColor) -> None:
        self._grid_color = QColor(color)
        self.invalidate()

    def grid_color(self) -> QColor:
        return QColor(self._grid_color)

    def background_color(self) -> QColor:
        return QColor(self._bg_color)

    def set_grid_step(self, step: float) -> None:
        self._grid_step = max(4.0, float(step))
        self.invalidate()

    # ------------------------------------------------------------- 背景绘制
    def _effective_grid_step(self, scale: float) -> float:
        """按当前缩放把网格步长调整到“屏幕间距合理”的档位。"""
        step = self._grid_step
        if step <= 0 or scale <= 0:
            return 0.0
        # 缩得太小时放大步长（4 倍一档），缩得太大时缩小步长（2 倍一档）
        while step * scale < GRID_MIN_PX:
            step *= 4.0
        while step * scale > GRID_MAX_PX and step > 1.0:
            step /= 2.0
        return step

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, self._bg_color)

        # 点阵网格：根据缩放自动调整密度，并限制单次绘制点数
        scale = abs(painter.transform().m11()) or 1.0
        step = self._effective_grid_step(scale)
        if step <= 0:
            return

        cols = int(rect.width() / step) + 2
        rows = int(rect.height() / step) + 2
        if cols <= 0 or rows <= 0 or cols * rows > GRID_MAX_POINTS:
            return

        left = rect.left() - (rect.left() % step)
        top = rect.top() - (rect.top() % step)
        points = QPolygonF()
        y = top
        for _ in range(rows):
            x = left
            for _ in range(cols):
                points.append(QPointF(x, y))
                x += step
            y += step

        painter.save()
        painter.setPen(self._grid_color)
        painter.drawPoints(points)
        painter.restore()

    # ------------------------------------------------------------- 便捷查询
    def items_at(self, scene_point, radius: float = 4.0) -> list:
        """返回场景坐标点附近（半径内）的所有顶层图形项。"""
        if radius <= 0:
            return [it for it in self.items(scene_point) if not it.parentItem()]
        rect = QRectF(scene_point.x() - radius, scene_point.y() - radius,
                      radius * 2, radius * 2)
        return [it for it in self.items(rect, Qt.ItemSelectionMode.IntersectsItemShape)
                if not it.parentItem()]

    def topmost_item_at(self, scene_point, radius: float = 4.0):
        found = self.items_at(scene_point, radius)
        return found[0] if found else None
