"""画笔工具：自由手绘平滑笔迹（tools/pen_tool.py）。

绘制过程分两步：
1. 落笔时创建一个“预览” StrokeItem 并直接放进场景，边移动边追加点，
   因此笔迹是实时可见的；
2. 抬笔时把预览项从场景移除，改为压入一条 :class:`AddItemCommand`，
   由撤销栈统一管理（预览项不进入历史，也不会被自动保存）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from canvas.items import StrokeItem, mark_preview
from core.history import AddItemCommand
from tools.base_tool import BaseTool

# 采样点之间的最小屏幕距离（像素）：太小会记录大量抖动点，太大会让曲线失真
MIN_SAMPLE_PX = 1.6
# 单条笔迹的点数上限，避免长时间涂抹导致内存/重绘压力
MAX_POINTS = 4000


class PenTool(BaseTool):
    name = "画笔"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, color: QColor = None, thickness: float = 2.0) -> None:
        super().__init__()
        self.color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.thickness = float(thickness)
        # 绘制中间态
        self._points = []
        self._preview_item = None
        self._drawing = False

    # ------------------------------------------------------------- 事件
    def mousePressEvent(self, event, view) -> None:
        scene = view.scene()
        pos = self.scene_pos(event, view)
        self._points = [pos]
        self._drawing = True

        item = StrokeItem([], self.color, self.thickness)
        mark_preview(item)
        scene.addItem(item)
        self._preview_item = item
        event.accept()

    def mouseMoveEvent(self, event, view) -> None:
        if not self._drawing or self._preview_item is None:
            return
        pos = self.scene_pos(event, view)
        if len(self._points) >= MAX_POINTS:
            event.accept()
            return
        # 按“屏幕像素”而不是“场景坐标”过滤抖动，缩放后手感一致
        min_scene = MIN_SAMPLE_PX / max(view.current_zoom(), 1e-6)
        if self._points and (pos - self._points[-1]).manhattanLength() < min_scene:
            event.accept()
            return
        self._points.append(pos)
        self._preview_item.add_point(pos)
        event.accept()

    def mouseReleaseEvent(self, event, view) -> None:
        if not self._drawing:
            return
        self._drawing = False
        scene = view.scene()
        preview = self._preview_item
        self._preview_item = None

        # 抬笔位置也纳入采样，避免最后一段被截断
        end = self.scene_pos(event, view)
        if self._points:
            min_scene = MIN_SAMPLE_PX / max(view.current_zoom(), 1e-6)
            if (end - self._points[-1]).manhattanLength() >= min_scene:
                self._points.append(end)
        points = self._points
        self._points = []

        if preview is not None and preview.scene() is scene:
            scene.removeItem(preview)

        # 只有一个点：视为“点一下”，也提交为一个圆点，避免落笔无痕
        if not points:
            event.accept()
            return

        item = StrokeItem(points, self.color, self.thickness)
        stack = self.undo_stack(view)
        if stack is not None:
            stack.push(AddItemCommand(scene, item, "画笔"))
        else:  # 无撤销栈（如单测直接使用）时直接添加
            scene.addItem(item)
        event.accept()

    def mouseDoubleClickEvent(self, event, view) -> None:
        # 双击不会中断连续笔画，直接忽略
        pass

    # ------------------------------------------------------------- 生命周期
    def deactivate(self, view) -> None:
        # 切换工具时若还在绘制，先把预览项清掉，避免留下“半条”笔迹
        if self._preview_item is not None:
            scene = self._preview_item.scene()
            if scene is not None:
                scene.removeItem(self._preview_item)
            self._preview_item = None
        self._points = []
        self._drawing = False
        super().deactivate(view)
