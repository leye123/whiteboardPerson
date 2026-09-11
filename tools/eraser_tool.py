"""橡皮擦工具（tools/eraser_tool.py）。

默认是**部分擦除（擦断）**：以橡皮圆心为准，把笔迹上落在圆内的采样点删掉，
剩余的点重新拼成若干条新笔迹。于是：

* 擦中间 → 一条笔画变成两条；
* 擦两端 → 笔画被缩短；
* 全都擦到 → 整条消失。

**橡皮擦只处理手绘笔迹（``StrokeItem``）**：矩形/椭圆/多边形/直线箭头/字体框/
图片/组合它一概不碰（v1.3.0 起）。原因是「橡皮」的语义就是擦掉画上去的线条，
以前碰到图形/文字/图片会**整块删掉** —— 用户只想擦掉一点手绘线，结果旁边的
图形整个消失，非常容易误删。要删图形请用选择工具选中后按 ``Delete``
（或在参数侧边栏里改参数）；组合里的笔迹不单独编辑，先 ``Ctrl+Shift+G`` 拆开。

一次「按下 → 拖拽 → 抬起」只产生**一个**撤销步骤
（通过 :class:`core.history.ReplaceItemsCommand` 把整段手势的增删合并起来），
不会像以前那样一次拖拽生成一堆撤销命令。

把 ``partial=False`` 传进构造函数即可退回「整笔擦除」的老行为。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt

from canvas.items import StrokeItem, is_preview
from core.history import ReplaceItemsCommand
from core.stroke import cumulative_lengths, split_by_eraser
from tools.base_tool import BaseTool

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
        """只擦笔迹：图形 / 线段 / 字体框 / 图片 / 组合一律不碰。

        ``scene.items_at()`` 只返回**顶层**对象，所以组合里的笔迹不会在这里
        被单独擦到（组合的成员不能被单独编辑，要改先 Ctrl+Shift+G 拆开）。
        """
        scene = view.scene()
        radius = self.radius
        for item in scene.items_at(pos, radius):
            if not isinstance(item, StrokeItem) or is_preview(item):
                continue        # 不是手绘笔迹（图形/线段/文字框/图片/组合）→ 橡皮不管
            if item.parentItem() is not None:
                continue        # 组合里的笔迹：不单独编辑
            if self.partial:
                self._split_stroke(item, pos, radius, scene)
            else:
                self._remove(item, scene)

    def _split_stroke(self, item: StrokeItem, center: QPointF, radius: float,
                      scene) -> None:
        """按橡皮圆把一条笔画擦断，必要时替换成多条碎笔迹。"""
        points = item.points()
        if not points:
            return
        # 笔迹可能被缩放过（自由伸缩会给它套 transform），所以先把橡皮圆心
        # 换算到笔迹的**局部坐标**、半径按缩放倍数折算，再按局部坐标切分。
        # 否则缩放过的笔迹只能被擦到一半（局部坐标与场景坐标不再重合）。
        inverse, invertible = item.sceneTransform().inverted()
        local_center = inverse.map(center) if invertible else QPointF(center)
        scale = item.scene_scale()
        runs = split_by_eraser(points, local_center, radius / max(scale, 1e-6))
        if len(runs) == 1 and len(runs[0]) == len(points):
            return                                   # 圆没碰到这条笔画
        segments = [run for run in runs if len(run) >= MIN_SEGMENT_POINTS]

        pen = item.pen()
        # 线型必须问图形项要，**不能**从 pen.style() 反推：
        # 设过虚线相位的笔迹，画笔样式已经变成 CustomDashLine，
        # 反推会一律得到 "dash" —— 于是点线/点划线被擦过之后变成虚线。
        # 线型必须问图形项要，**不能**从 pen.style() 反推：
        # 设过虚线相位的笔迹，画笔样式已经变成 CustomDashLine，
        # 反推会一律得到 "dash" —— 于是点线/点划线被擦过之后变成虚线
        # （碎片起点的相位不为周期整数倍时才触发，所以是「有时候会变成虚线」）。
        style = item.line_style()
        origin = QPointF(item.pos())
        transform = item.transform()
        # 虚线相位：碎片要知道自己「从原笔迹多长的地方开始」，
        # 否则虚线图案会在断口处重新起头，断口之后的虚线整段移位。
        # **实线完全不需要**：给实线笔迹设相位会让 Qt 把它变成「空白虚线」
        # （见 StrokeItem._apply_dash_offset），碎片会整段隐形、拖橡皮时闪烁。
        lengths = cumulative_lengths(points) if style != "solid" else None
        base_offset = item.dash_offset() if style != "solid" else 0.0
        self._remove(item, scene)
        cursor = 0
        for run in segments:
            try:
                start_index = points.index(run[0], cursor)
            except ValueError:                       # 理论上不会发生
                start_index = cursor
            cursor = start_index
            dash_offset = base_offset + lengths[start_index] if lengths else 0.0
            # 碎片要继承原笔迹的颜色/线宽/线型/缩放，否则擦一下虚线会变成实线、
            # 缩放过的笔迹会突然跳回原始大小
            segment = StrokeItem(run, pen.color(), pen.widthF(), pos=origin,
                                 line_style=style, dash_offset=dash_offset)
            segment.setTransform(transform)
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
