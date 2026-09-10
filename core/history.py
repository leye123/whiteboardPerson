"""撤销 / 重做命令（core/history.py）。

所有会改变画布内容的操作都封装成 :class:`QUndoCommand` 子类，统一压入
:class:`QUndoStack`，由菜单栏的“撤销/重做”和快捷键触发。

约定：
- 命令构造时**不**修改场景；所有副作用都发生在 :meth:`redo` 中，
  这样 ``stack.push(cmd)`` 会自动执行一次，逻辑保持单一来源。
- 对于“图形项已经在场景里”的情况（例如画笔在拖拽过程中已经把预览项
  放进场景），命令用 ``_skip_first_redo`` 跳过第一次 redo，避免重复添加
  导致的闪烁。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from PySide6.QtCore import QPointF
from PySide6.QtGui import QUndoCommand, QUndoStack

from core.text_format import TextFormat


def top_level_items(items: Iterable) -> List:
    """过滤掉子项与 None，只保留顶层图形项。"""
    return [it for it in items if it is not None and it.parentItem() is None]


# ------------------------------------------------------------------ 新增


class AddItemCommand(QUndoCommand):
    """向场景添加一个图形项。"""

    def __init__(self, scene, item, text: str = "添加对象", parent=None) -> None:
        super().__init__(text, parent)
        self._scene = scene
        self._item = item
        # 若图形项已在场景中（预览项），第一次 redo 不做任何事
        self._skip_first_redo = item.scene() is scene

    def redo(self) -> None:
        if self._skip_first_redo:
            self._skip_first_redo = False
            return
        if self._item.scene() is not self._scene:
            self._scene.addItem(self._item)

    def undo(self) -> None:
        if self._item.scene() is self._scene:
            self._scene.removeItem(self._item)


# ------------------------------------------------------------------ 删除


class RemoveItemCommand(QUndoCommand):
    """从场景移除单个图形项。"""

    def __init__(self, scene, item, text: str = "删除对象", parent=None) -> None:
        super().__init__(text, parent)
        self._scene = scene
        self._item = item
        self._z = item.zValue()

    def redo(self) -> None:
        self._item.setSelected(False)
        if self._item.scene() is self._scene:
            self._scene.removeItem(self._item)

    def undo(self) -> None:
        if self._item.scene() is not self._scene:
            self._scene.addItem(self._item)
            self._item.setZValue(self._z)


class RemoveItemsCommand(QUndoCommand):
    """一次删除多个图形项（框选删除、整页清空都用它）。"""

    def __init__(self, scene, items: Sequence, text: str = "删除对象",
                 parent=None) -> None:
        super().__init__(text, parent)
        self._scene = scene
        # 记录原始层级，撤销时尽量还原
        self._items = [(it, it.zValue()) for it in top_level_items(items)]

    def redo(self) -> None:
        for item, _z in self._items:
            item.setSelected(False)
            if item.scene() is self._scene:
                self._scene.removeItem(item)

    def undo(self) -> None:
        # 逆序添加，保证原来的层叠关系（先添加的在下面）
        for item, z in reversed(self._items):
            if item.scene() is not self._scene:
                self._scene.addItem(item)
                item.setZValue(z)


class ClearPageCommand(RemoveItemsCommand):
    """清空当前页（等价于删除本页所有顶层图形项）。"""

    def __init__(self, scene, text: str = "清空页面", parent=None) -> None:
        super().__init__(scene, scene.items(), text, parent)


class ReplaceItemsCommand(QUndoCommand):
    """用一批新图形项替换另一批（橡皮擦「擦断」笔迹时用）。

    约定：命令被 push 时场景里**已经是替换后的状态**（拖拽过程中已经实时改过
    场景了），所以 :meth:`redo` 写成幂等的，重复执行结果一致。
    """

    def __init__(self, scene, removed: Sequence, added: Sequence,
                 text: str = "擦除", parent=None) -> None:
        super().__init__(text, parent)
        self._scene = scene
        self._removed = [it for it in removed if it is not None]
        self._added = [it for it in added if it is not None]

    def redo(self) -> None:
        for item in self._removed:
            if item.scene() is self._scene:
                item.setSelected(False)
                self._scene.removeItem(item)
        for item in self._added:
            if item.scene() is not self._scene:
                self._scene.addItem(item)

    def undo(self) -> None:
        for item in self._added:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        for item in self._removed:
            if item.scene() is not self._scene:
                self._scene.addItem(item)


# ------------------------------------------------------------------ 移动


class MoveItemsCommand(QUndoCommand):
    """移动一个或多个图形项（记录移动前后的场景坐标）。"""

    def __init__(self, items: Sequence,
                 old_positions: Dict,
                 new_positions: Dict,
                 text: str = "移动对象", parent=None) -> None:
        super().__init__(text, parent)
        self._old = {it: QPointF(p) for it, p in old_positions.items()}
        self._new = {it: QPointF(p) for it, p in new_positions.items()}

    def _apply(self, positions: Dict) -> None:
        for item, pos in positions.items():
            if item.scene() is not None:
                item.setPos(pos)

    def redo(self) -> None:
        self._apply(self._new)

    def undo(self) -> None:
        self._apply(self._old)


# ------------------------------------------------------------------ 原地修改


class TextFormatCommand(QUndoCommand):
    """修改已有文字的文本内容与排版（双击文字编辑时用，可撤销）。

    命令构造时**不**改动画布；新旧状态都在构造时快照下来，
    ``redo`` 应用新状态、``undo`` 回到旧状态，因此重复撤销/重做是安全的。
    """

    def __init__(self, item, new_text: str, new_format: TextFormat,
                 text: str = "编辑文字", parent=None) -> None:
        super().__init__(text, parent)
        self._item = item
        self._old_text = item.toPlainText()
        self._old_format = TextFormat.from_item(item)
        self._new_text = new_text
        self._new_format = new_format.copy()

    def _apply(self, content: str, fmt: TextFormat) -> None:
        if self._item is None:
            return
        self._item.setPlainText(content)
        self._item.set_text_format(fmt)
        self._item.update()

    def redo(self) -> None:
        self._apply(self._new_text, self._new_format)

    def undo(self) -> None:
        self._apply(self._old_text, self._old_format)


# ------------------------------------------------------------------ 工具函数


def push_commands(stack: Optional[QUndoStack], text: str,
                  commands: Sequence[QUndoCommand]) -> None:
    """把若干命令合并成一次撤销步骤。

    典型用途：一次橡皮擦拖拽擦掉了十几个对象，撤销时应当一次全部恢复。

    注意不能无条件使用 ``beginMacro``/``endMacro``：Qt 6 即使宏里没有压入
    任何命令，也会在栈里留下一个空步骤（用户按 Ctrl+Z 会“什么都没发生”）。
    这里先过滤空命令，只有一个命令时直接压入，避免产生空宏。
    """
    commands = [cmd for cmd in commands if cmd is not None]
    if not commands:
        return
    if stack is None or len(commands) == 1:
        for cmd in commands:
            if stack is None:
                cmd.redo()
            else:
                stack.push(cmd)
        return
    stack.beginMacro(text)
    try:
        for cmd in commands:
            stack.push(cmd)
    finally:
        stack.endMacro()
