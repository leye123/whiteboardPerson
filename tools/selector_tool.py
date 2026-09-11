"""选择/移动工具：点选、Ctrl 多选、框选（套索矩形）与整体拖动。

移动通过代码修改 setPos 完成并压入 MoveItemsCommand（可撤销），
而不是依赖 QGraphicsItem 的原生 ItemIsMovable 拖拽（原生拖拽不经过撤销栈）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsRectItem

from canvas.items import TextItem, mark_preview
from core.history import MoveItemsCommand, ResizeItemCommand
from tools.base_tool import BaseTool


class SelectorTool(BaseTool):
    name = "选择"
    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, settings=None) -> None:
        super().__init__()
        self._mode = None             # None | 'drag' | 'rubber' | 'resize'
        self._press_scene = QPointF()
        self._drag_origins = {}       # item -> QPointF(scenePos)
        self._rubber_item = None
        self.settings = settings      # 文字编辑对话框要用（记住导入的字体/排版）
        # 缩放（拖图片/文字的手柄）
        self._resize_item = None
        self._resize_handle = None
        self._resize_old_state = None

    # ------------------------------------------------------------- 事件
    def mousePressEvent(self, event, view) -> None:
        scene = view.scene()
        pos = self.scene_pos(event, view)
        # 兜底：上一次若因工具切换/手势中断留下框选矩形，先清掉，
        # 否则它既不会被绘制逻辑回收，又会一直叠在白板上。
        self._clear_rubber(scene)
        self._press_scene = pos
        self._drag_origins = {}
        self._mode = None

        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if not ctrl:
            # 先看是不是按在缩放手柄上：手柄在选中框上（可能压在图形项外面），
            # 所以必须比命中图形项更早判断。
            handle = view.handle_at(event.position().toPoint())
            if handle is not None:
                self._begin_resize(view, handle)
                event.accept()
                return

        item = scene.topmost_item_at(pos, interior=True)

        if ctrl:
            # Ctrl+点击：切换选中状态，不进入拖拽
            if item is not None:
                item.setSelected(not item.isSelected())
            self._mode = None
            event.accept()
            return

        if item is not None:
            # 点击已选中的任意一项 -> 拖动整组；否则单选后同样可直接拖动
            if not item.isSelected():
                scene.clearSelection()
                item.setSelected(True)
            self._begin_drag(scene)
        else:
            scene.clearSelection()
            self._begin_rubber(scene, pos)
        event.accept()

    def mouseMoveEvent(self, event, view) -> None:
        scene = view.scene()
        pos = self.scene_pos(event, view)

        if self._mode == "resize":
            self._do_resize(pos, event, view)
            event.accept()
            return

        if self._mode == "drag":
            delta = pos - self._press_scene
            for item, origin in self._drag_origins.items():
                if item.scene() is scene:
                    item.setPos(origin + delta)
            event.accept()
            return

        if self._mode == "rubber" and self._rubber_item is not None:
            rect = QRectF(self._press_scene, pos).normalized()
            self._rubber_item.setRect(rect)
            path = QPainterPath()
            path.addRect(rect)
            # 必须用关键字传 mode：位置参数传枚举会被 PySide6 当成
            # setSelectionArea(path, QTransform) 重载，直接段错误。
            scene.setSelectionArea(path, mode=Qt.ItemSelectionMode.IntersectsItemShape)
            event.accept()
            return

        # 悬停时给出提示：手柄上是缩放指针，对象上是可拖拽的手型
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            handle = view.handle_at(event.position().toPoint())
            if handle is not None:
                view.viewport().setCursor(view.handle_cursor(handle))
            elif scene.topmost_item_at(pos, interior=True) is not None:
                view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                view.viewport().setCursor(self.cursor)
        event.accept()

    def mouseReleaseEvent(self, event, view) -> None:
        scene = view.scene()

        if self._mode == "resize":
            self._finish_resize(view)

        if self._mode == "drag":
            self._finish_drag(scene, view)

        self._clear_rubber(scene)
        self._mode = None
        self._drag_origins = {}
        event.accept()

    def deactivate(self, view) -> None:
        # 拖拽/框选/缩放过程中被切走（例如用快捷键换工具）时收尾
        self._clear_rubber()
        self._mode = None
        self._drag_origins = {}
        self._resize_item = None
        self._resize_handle = None
        self._resize_old_state = None
        super().deactivate(view)

    def mouseDoubleClickEvent(self, event, view) -> None:
        """双击文字框 / 图形 → 打开右侧参数侧边栏输入文字。

        v1.3.0 起文字参数（内容、字体、字号、颜色、换行）都在侧边栏里，
        不再弹模态对话框 —— 边看画布边改才看得到效果。

        判定用 ``interior=True``：图形默认不填充，只按描边判定的话
        "双击图形中间"根本进不去。
        """
        scene = view.scene()
        item = scene.topmost_item_at(self.scene_pos(event, view), interior=True)
        handler = getattr(view.window(), "focus_text_input", None)
        if item is not None and callable(handler):
            # 文字框本体、以及带内部文字的图形（有 raw_label）都能双击进去改字
            if isinstance(item, TextItem) or getattr(item, "raw_label", None) is not None:
                handler(item)
        event.accept()

    def mouseHoverEvent(self, event, view) -> None:
        view.viewport().setCursor(self.cursor)

    # ------------------------------------------------------------- 内部
    def _begin_rubber(self, scene, pos: QPointF) -> None:
        """开始框选：画一个虚线矩形，随鼠标更新，并在其上做选择。"""
        self._clear_rubber(scene)
        item = QGraphicsRectItem()
        item.setPen(QPen(QColor(0, 120, 215, 180), 0, Qt.PenStyle.DashLine))
        item.setBrush(QColor(0, 120, 215, 24))
        item.setZValue(1e9)                 # 始终画在最上层
        item.setRect(QRectF(pos, pos))
        mark_preview(item)                  # 不参与保存
        scene.addItem(item)
        self._rubber_item = item
        self._mode = "rubber"

    def _clear_rubber(self, scene=None) -> None:
        """移除当前（或残留的）框选矩形。

        注意不能只把 ``self._rubber_item`` 置空：图形项一旦还在场景里，
        场景又持有它的 Python 引用，它就会一直留在白板上越积越多。
        """
        item = self._rubber_item
        self._rubber_item = None
        if item is None:
            return
        target = item.scene() if item.scene() is not None else None
        if target is not None and (scene is None or target is scene):
            target.removeItem(item)

    def _begin_drag(self, scene) -> None:
        selected = [it for it in scene.selectedItems() if not it.parentItem()]
        if not selected:
            return
        self._mode = "drag"
        self._drag_origins = {it: it.scenePos() for it in selected}
        view = scene.views()[0] if scene.views() else None
        if view is not None:
            view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def _finish_drag(self, scene, view) -> None:
        items = list(self._drag_origins.keys())
        new_positions = {}
        moved = False
        for item in items:
            current = item.scenePos()
            origin = self._drag_origins[item]
            if (current - origin).manhattanLength() > 0.5:
                moved = True
            new_positions[item] = current
        if moved:
            stack = self.undo_stack(view)
            cmd = MoveItemsCommand(items, self._drag_origins, new_positions, "移动")
            if stack is not None:
                stack.push(cmd)
        view.viewport().setCursor(self.cursor)

    # ------------------------------------------------------------- 缩放（图片/文字）
    def _begin_resize(self, view, handle: int) -> None:
        item = view.resize_target()
        if item is None:
            return
        self._mode = "resize"
        self._resize_item = item
        self._resize_handle = handle
        self._resize_old_state = item.resize_state()
        view.viewport().setCursor(view.handle_cursor(handle))

    def _do_resize(self, scene_pos: QPointF, event, view=None) -> None:
        item = self._resize_item
        if item is None or item.scene() is None:
            return
        # 默认比例由图形项自己决定（字体框默认自由拉伸、图片与形状四角等比），
        # 按住 Shift 一律强制等比。
        keep_aspect = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not keep_aspect:
            default = getattr(item, "default_keep_aspect", None)
            if callable(default):
                keep_aspect = bool(default(self._resize_handle))
            else:
                from canvas.resize import CORNER_HANDLES

                keep_aspect = self._resize_handle in CORNER_HANDLES
        item.resize_with(self._resize_handle, scene_pos, keep_aspect)
        if view is not None:
            # 手柄跟着新尺寸走，旧位置必须立刻重绘掉
            view.viewport().update()

    def _finish_resize(self, view) -> None:
        item = self._resize_item
        old_state = self._resize_old_state
        handle = self._resize_handle
        self._resize_item = None
        self._resize_handle = None
        self._resize_old_state = None
        if item is None or old_state is None:
            return
        new_state = item.resize_state()
        if new_state != old_state:
            stack = self.undo_stack(view)
            command = ResizeItemCommand(item, old_state, new_state)
            if stack is not None:
                stack.push(command)
        if handle is not None:
            view.viewport().setCursor(view.handle_cursor(handle))
        view.viewport().update()
