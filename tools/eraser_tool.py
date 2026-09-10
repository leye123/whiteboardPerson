"""橡皮擦工具（tools/eraser_tool.py）。

默认是**部分擦除（擦断）**：以橡皮圆心为准，把笔迹上落在圆内的采样点删掉，
剩余的点重新拼成若干条新笔迹。于是：

* 擦中间 → 一条笔画变成两条；
* 擦两端 → 笔画被缩短；
* 全都擦到 → 整条消失。

矩形/椭圆/直线/箭头/文字/图片无法「擦断」，被橡皮碰到时整体删除。

一次「按下 → 拖拽 → 抬起」只产生**一个**撤销步骤
（通过 :class:`core.history.ReplaceItemsCommand` 把整段手势的增删合并起来），
不会像以前那样一次拖拽生成一堆撤销命令。

把 ``partial=False`` 传进构造函数即可退回「整笔擦除」的老行为。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt

from canvas.items import (
    EllipseItem,
    ImageItem,
    LineItem,
    PolygonShapeItem,
    RectItem,
    StrokeItem,
    TextItem,
    is_preview,
    line_style_name,
)
from core.history import ReplaceItemsCommand
from core.stroke import cumulative_lengths, split_by_eraser
from tools.base_tool import BaseTool

# 无法擦断、只能整体删除的图形（形状/文字/图片都属此类）
_WHOLE_ERASE = (LineItem, RectItem, EllipseItem, PolygonShapeItem, TextItem, ImageItem)
# 擦断后少于这个点数的碎片直接丢弃（1 个点只剩个圆点，没有保留价值）
MIN_SEGMENT_POINTS = 2


class EraserTool(BaseTool):
    name = "橡皮擦"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, size: float = 16.0, partial: bool = True) -> None:
        super().__init__()
        self.size = float(size)
        self.partial = bool(partial)
        self._removed = []      # 本次手势删掉的原有对象
        self._added = []        # 本次手势新生成的碎片
        self._erasing = False

    @property
    def radius(self) -> float:
        return max(2.0, self.size / 2.0)

    # ------------------------------------------------------------- 事件
    def mousePressEvent(self, event, view) -> None:
        self._erasing = True
        self._removed = []
        self._added = []
        pos = self.scene_pos(event, view)
        self._erase_at(pos, view)
        self._show_cursor(pos, view)
        event.accept()

    def mouseMoveEvent(self, event, view) -> None:
        pos = self.scene_pos(event, view)
        if self._erasing:
            self._erase_at(pos, view)
        self._show_cursor(pos, view)
        event.accept()

    def mouseReleaseEvent(self, event, view) -> None:
        if self._erasing:
            # 抬手位置也擦一次，避免最后一段漏掉
            self._erase_at(self.scene_pos(event, view), view)
        self._erasing = False
        self._commit(view)
        self._hide_cursor(view)
        event.accept()

    def mouseHoverEvent(self, event, view) -> None:
        self._show_cursor(self.scene_pos(event, view), view)

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    # ------------------------------------------------------------- 生命周期
    def activate(self, view) -> None:
        super().activate(view)
        self._reset()

    def deactivate(self, view) -> None:
        self._hide_cursor(view)
        self._reset()
        super().deactivate(view)

    def _reset(self) -> None:
        self._erasing = False
        self._removed = []
        self._added = []

    # ------------------------------------------------------------- 核心逻辑
    def _erase_at(self, pos: QPointF, view) -> None:
        scene = view.scene()
        radius = self.radius
        for item in scene.items_at(pos, radius):
            if item.parentItem() is not None or is_preview(item):
                continue
            if isinstance(item, StrokeItem) and self.partial:
                self._split_stroke(item, pos, radius, scene)
            elif isinstance(item, (StrokeItem,) + _WHOLE_ERASE):
                self._remove(item, scene)

    def _split_stroke(self, item: StrokeItem, center: QPointF, radius: float,
                      scene) -> None:
        """按橡皮圆把一条笔画擦断，必要时替换成多条碎笔迹。"""
        points = item.points()
        if not points:
            return
        runs = split_by_eraser(points, center, radius, item.pos())
        if len(runs) == 1 and len(runs[0]) == len(points):
            return                                   # 圆没碰到这条笔画
        segments = [run for run in runs if len(run) >= MIN_SEGMENT_POINTS]

        pen = item.pen()
        style = line_style_name(pen.style())
        origin = QPointF(item.pos())
        # 虚线相位：碎片要知道自己「从原笔迹多长的地方开始」，
        # 否则虚线图案会在断口处重新起头，断口之后的虚线整段移位。
        lengths = cumulative_lengths(points)
        base_offset = item.dash_offset()
        self._remove(item, scene)
        cursor = 0
        for run in segments:
            try:
                start_index = points.index(run[0], cursor)
            except ValueError:                       # 理论上不会发生
                start_index = cursor
            cursor = start_index
            # 碎片要继承原笔迹的颜色/线宽/线型，否则擦一下虚线会变成实线
            segment = StrokeItem(run, pen.color(), pen.widthF(), pos=origin,
                                 line_style=style,
                                 dash_offset=base_offset + lengths[start_index])
            scene.addItem(segment)
            self._added.append(segment)

    def _remove(self, item, scene) -> None:
        """把对象从场景移除，并登记到本次手势的增删记录里。

        如果这个对象本来就是本次手势生成的碎片，就把它从 ``_added`` 里撤销登记
        （它已经不存在了，撤销时不该再被“恢复”）。
        """
        for index, added in enumerate(self._added):
            if added is item:
                del self._added[index]
                break
        else:
            self._removed.append(item)
        item.setSelected(False)
        if item.scene() is scene:
            scene.removeItem(item)

    def _commit(self, view) -> None:
        """把整段手势的增删压成一个撤销步骤。"""
        removed, added = self._removed, self._added
        self._removed, self._added = [], []
        if not removed and not added:
            return
        stack = self.undo_stack(view)
        if stack is None:
            return
        stack.push(ReplaceItemsCommand(view.scene(), removed, added, "橡皮擦"))

    # ------------------------------------------------------------- 光标提示
    def _show_cursor(self, scene_pos: QPointF, view) -> None:
        """在视口上叠加一个圆圈，直观显示橡皮的作用范围。"""
        center = view.mapFromScene(scene_pos)
        radius = self.radius * max(view.current_zoom(), 1e-6)
        view.set_overlay_circle(QPointF(center), radius)

    def _hide_cursor(self, view) -> None:
        view.set_overlay_circle(None)
