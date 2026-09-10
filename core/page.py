"""页面模型（core/page.py）。

一个 :class:`BoardPage` 就是一页白板：一个独立的 :class:`WhiteboardScene`
加上页面名称。多页面即多场景，切换页面 = 切换视图的 scene，
这样每页的图形项、选中状态互不干扰，撤销栈也可以按页独立维护。
"""
from __future__ import annotations

from typing import List

from PySide6.QtGui import QUndoStack

from canvas.items import StrokeItem
from canvas.scene import WhiteboardScene


class BoardPage:
    """白板中的一页。

    每页自带一个 :class:`QUndoStack`：在第 2 页按 Ctrl+Z 只会撤销第 2 页的
    操作，不会把第 1 页的内容改掉。
    """

    def __init__(self, name: str = "页面", parent=None) -> None:
        self.name = str(name)
        self.scene = WhiteboardScene(parent)
        self.undo_stack = QUndoStack()
        self.undo_stack.setUndoLimit(200)

    # ------------------------------------------------------------- 查询
    def item_count(self) -> int:
        """顶层图形项数量（用于状态栏与删除页面提示）。"""
        return sum(1 for it in self.scene.items() if it.parentItem() is None)

    def is_empty(self) -> bool:
        return self.item_count() == 0

    @property
    def strokes(self) -> List[StrokeItem]:
        """本页所有手绘笔迹（自底向上），保留给需要按笔画处理的场景。"""
        return [it for it in reversed(self.scene.items())
                if isinstance(it, StrokeItem)]

    # ------------------------------------------------------------- 编辑
    def clear(self) -> None:
        """移除全部图形项（调用方负责压入撤销命令）。"""
        for item in list(self.scene.items()):
            if item.parentItem() is None:
                self.scene.removeItem(item)

    # ------------------------------------------------------------- 外观
    def apply_theme(self, background, grid) -> None:
        self.scene.set_background(background)
        self.scene.set_grid_color(grid)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<BoardPage {self.name!r} items={self.item_count()}>"
