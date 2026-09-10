"""拖动画布工具（tools/pan_tool.py）。

把「按住空格 + 左键拖拽」「中键拖拽」这两个临时平移手势做成常驻工具：
选中它以后直接用左键拖拽即可自由移动画布，不需要按住键盘。

实际的手势逻辑只有一份 —— 都在 :class:`~canvas.view.WhiteboardView` 里
（``begin_pan`` / ``do_pan`` / ``end_pan``）：这里的按下事件调用
``begin_pan``，之后的移动与抬起会由视图自己接管（它看到 ``_panning``
就优先处理并直接返回），所以这个工具不需要重复实现平移与收尾。
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from tools.base_tool import BaseTool


class PanTool(BaseTool):
    name = "拖动画布"
    cursor = Qt.CursorShape.OpenHandCursor

    def mousePressEvent(self, event, view) -> None:
        # 之后的 mouseMove / mouseRelease 由 WhiteboardView 的平移分支处理
        view.begin_pan(event.position())
        event.accept()

    def mouseMoveEvent(self, event, view) -> None:
        view.do_pan(event.position())
        event.accept()

    def mouseReleaseEvent(self, event, view) -> None:
        view.end_pan()
        event.accept()

    def mouseHoverEvent(self, event, view) -> None:
        if not view.is_panning:
            view.viewport().setCursor(self.cursor)

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    def deactivate(self, view) -> None:
        if view.is_panning:          # 拖动中被切走工具时收尾
            view.end_pan()
        super().deactivate(view)
