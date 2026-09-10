"""工具基类（策略模式）：所有工具统一接口。"""
from __future__ import annotations

from PySide6.QtCore import Qt


class BaseTool:
    """所有白板工具的基类。

    工具不持有场景引用，而是通过事件回调中传入的 view.scene() 动态取当前页面，
    这样在多页面之间切换时工具无需重建。
    """

    name = "工具"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self) -> None:
        self.is_active = False

    # ------------------------------------------------------------- 生命周期
    def activate(self, view) -> None:
        self.is_active = True
        view.viewport().setCursor(self.cursor)

    def deactivate(self, view) -> None:
        self.is_active = False
        if getattr(view, "current_tool", None) is self:
            view._apply_tool_cursor()

    # ------------------------------------------------------------- 事件接口
    def mousePressEvent(self, event, view) -> None:
        pass

    def mouseMoveEvent(self, event, view) -> None:
        pass

    def mouseReleaseEvent(self, event, view) -> None:
        pass

    def mouseDoubleClickEvent(self, event, view) -> None:
        pass

    def mouseHoverEvent(self, event, view) -> None:
        pass

    # ------------------------------------------------------------- 小工具
    def scene_pos(self, event, view):
        return view.mapToScene(event.position().toPoint())

    @staticmethod
    def undo_stack(view):
        return getattr(view, "undo_stack", None)
