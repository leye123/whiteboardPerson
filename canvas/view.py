"""白板视图（canvas/view.py）。

职责：
- 渲染（抗锯齿 + 平滑变换）；
- 滚轮缩放（以鼠标为中心，带上下限）；
- 平移：按住鼠标中键拖拽，或按住空格键 + 左键拖拽；
- 将鼠标/键盘事件转发给当前激活的工具（策略模式）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion, QWheelEvent
from PySide6.QtWidgets import QGraphicsView

from canvas import resize

ZOOM_STEP = 1.2
ZOOM_MIN = 0.05
ZOOM_MAX = 20.0


class WhiteboardView(QGraphicsView):
    """自定义图形视图。"""

    zoomChanged = Signal(float)          # 当前缩放比例
    cursorMoved = Signal(QPointF)        # 鼠标在场景坐标中的位置
    deleteRequested = Signal()           # 用户按下 Delete/Backspace

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setMouseTracking(True)

        self.current_tool = None
        self._overlay = None          # 视口叠加层（橡皮擦范围圈）
        self._selection_boxes = set()  # 上一次绘制的选中框（视口坐标），用于重绘
        self._last_dirty_region = QRegion()
        # 注意：QGraphicsView(scene) 这种构造方式不会调用 Python 覆盖的 setScene()，
        # 初始场景必须在这里显式连接，否则首页的 selectionChanged 收不到。
        self._connect_scene(scene)
        self._panning = False
        self._pan_middle = False
        self._space_pressed = False
        self._pan_start = QPointF()
        self._hbar0 = 0
        self._vbar0 = 0
        self._is_gesture_pan = False

    # ------------------------------------------------------------- 工具路由
    def _connect_scene(self, scene) -> None:
        """接上场景信号。

        注意 ``QGraphicsView(scene)`` 这种构造方式**不会**调用 Python 覆盖的
        :meth:`setScene`（C++ 构造函数直接设了场景），所以初始场景必须显式连接，
        否则首页收不到 selectionChanged。
        """
        if scene is None:
            return
        for signal, slot in ((scene.selectionChanged, self._on_selection_changed),
                             (scene.changed, self._on_scene_changed)):
            try:
                signal.connect(slot)
            except (RuntimeError, TypeError):
                pass

    def _disconnect_scene(self, scene) -> None:
        if scene is None:
            return
        for signal, slot in ((scene.selectionChanged, self._on_selection_changed),
                             (scene.changed, self._on_scene_changed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def setScene(self, scene) -> None:
        """切页会换场景：断开旧场景的信号，连上新场景，并清空选中框缓存。"""
        previous = self.scene()
        if previous is not None and previous is not scene:
            self._disconnect_scene(previous)
        super().setScene(scene)
        self._connect_scene(scene)
        self._selection_boxes = set()

    def set_tool(self, tool) -> None:
        previous = self.current_tool
        if previous is not None and previous is not tool:
            # 让旧工具收尾（例如橡皮擦要清掉作用范围提示圈）
            previous.deactivate(self)
        self.current_tool = tool
        if tool is not None:
            tool.activate(self)
        self.viewport().update()

    # ------------------------------------------------------------- 缩放
    def current_zoom(self) -> float:
        return self.transform().m11()

    def zoom_by(self, factor: float) -> None:
        zoom = self.current_zoom() * factor
        factor = max(ZOOM_MIN, min(ZOOM_MAX, zoom)) / self.current_zoom()
        self.scale(factor, factor)
        self.zoomChanged.emit(self.current_zoom())

    def zoom_in(self) -> None:
        self.zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_by(1.0 / ZOOM_STEP)

    def zoom_reset(self) -> None:
        self.resetTransform()
        self.zoomChanged.emit(1.0)

    def zoom_fit_scene(self) -> None:
        rect = self.scene().itemsBoundingRect()
        if rect.isNull() or rect.width() < 1 or rect.height() < 1:
            self.zoom_reset()
            return
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        z = self.current_zoom()
        if z > ZOOM_MAX:
            self.scale(ZOOM_MAX / z, ZOOM_MAX / z)
        self.zoomChanged.emit(self.current_zoom())

    # ------------------------------------------------------------- 平移
    def begin_pan(self, pos: QPointF) -> None:
        """开始平移画布（中键、空格+左键、以及「拖动」工具都走这里）。

        调用后 :meth:`mouseMoveEvent` / :meth:`mouseReleaseEvent` 会自行接管
        后续的拖动与收尾，调用方不需要再做别的。
        """
        self._panning = True
        self._pan_start = QPointF(pos)
        self._hbar0 = self.horizontalScrollBar().value()
        self._vbar0 = self.verticalScrollBar().value()
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def do_pan(self, pos: QPointF) -> None:
        if not self._panning:
            return
        dx = pos.x() - self._pan_start.x()
        dy = pos.y() - self._pan_start.y()
        self.horizontalScrollBar().setValue(self._hbar0 - dx)
        self.verticalScrollBar().setValue(self._vbar0 - dy)

    def end_pan(self) -> None:
        self._panning = False
        self._apply_tool_cursor()

    @property
    def is_panning(self) -> bool:
        return self._panning

    def _pan_requested(self, event) -> bool:
        """中键拖拽，或按住空格后左键拖拽。"""
        button = event.button()
        if button == Qt.MouseButton.MiddleButton:
            return True
        if self._space_pressed and button == Qt.MouseButton.LeftButton:
            return True
        return False

    def mousePressEvent(self, event) -> None:
        if self._pan_requested(event):
            self.begin_pan(event.position())
            event.accept()
            return
        tool = self.current_tool
        if tool is not None and event.button() == Qt.MouseButton.LeftButton:
            tool.mousePressEvent(event, self)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            self.do_pan(event.position())
            event.accept()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        self.cursorMoved.emit(scene_pos)
        tool = self.current_tool
        if tool is not None and event.buttons() & Qt.MouseButton.LeftButton:
            tool.mouseMoveEvent(event, self)
            return
        if tool is not None:
            tool.mouseHoverEvent(event, self)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning:
            self.end_pan()
            event.accept()
            return
        tool = self.current_tool
        if tool is not None and event.button() == Qt.MouseButton.LeftButton:
            tool.mouseReleaseEvent(event, self)
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        tool = self.current_tool
        if tool is not None and event.button() == Qt.MouseButton.LeftButton:
            tool.mouseDoubleClickEvent(event, self)
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------- 文件拖放
    # 注意：`QGraphicsView` 的 acceptDrops 默认就是 True（viewport 也是），
    # 而 Qt 只会把拖放事件发给**光标下那个接收拖放的控件**，不会再往上传给父窗口。
    # 所以只给主窗口设 acceptDrops 是没用的 —— 拖到画布上时事件全被这里吃掉，
    # 表现就是「拖进去毫无反应」。真正的入口必须写在这里，再由视图转给主窗口处理。
    def dragEnterEvent(self, event) -> None:
        if self._window_accepts_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        handler = getattr(self.window(), "handle_file_drop", None)
        if not callable(handler):
            event.ignore()
            return
        # 事件坐标是视口坐标，直接换算成场景坐标交给主窗口
        scene_pos = self.mapToScene(event.position().toPoint())
        handler(event, scene_pos)

    def _window_accepts_drop(self, mime_data) -> bool:
        accepts = getattr(self.window(), "accepts_file_drop", None)
        return bool(callable(accepts) and accepts(mime_data))

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)  # Ctrl+滚轮 = 默认（横向滚动等）
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        steps = delta / 120.0
        self.zoom_by(ZOOM_STEP ** steps)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            if not self._panning:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Plus or event.key() == Qt.Key.Key_Equal:
            self.zoom_by(ZOOM_STEP)
            return
        if event.key() == Qt.Key.Key_Minus:
            self.zoom_by(1.0 / ZOOM_STEP)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            self._space_pressed = False
            if not self._panning:
                self._apply_tool_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _apply_tool_cursor(self) -> None:
        tool = self.current_tool
        # 注意：工具类的 cursor 是枚举**值**（类属性），不是方法
        if tool is not None and getattr(tool, "cursor", None) is not None:
            self.viewport().setCursor(tool.cursor)
        else:
            self.viewport().unsetCursor()

    # ------------------------------------------------------------- 选中框
    def _selection_view_rect(self, item) -> QRect:
        """某个图形项的选中框（视口坐标，含线宽与虚线笔宽的余量）。"""
        scene_rect = item.mapRectToScene(item.boundingRect())
        return self.mapFromScene(scene_rect).boundingRect().adjusted(-2, -2, 2, 2)

    def _selection_boxes_now(self) -> set:
        """当前所有选中项的外框（视口坐标）。"""
        boxes = set()
        scene = self.scene()
        if scene is None:
            return boxes
        for item in scene.selectedItems():
            if not item.isVisible():
                continue
            rect = self._selection_view_rect(item)
            boxes.add((rect.x(), rect.y(), rect.width(), rect.height()))
        return boxes

    def _invalidate_selection_boxes(self, boxes: set) -> None:
        """把「缓存里的旧框 ∪ 新框」标记为脏。

        选中框画在视口叠加层上（不是图形项），Qt 的增量重绘不会刷新它，
        所以必须自己失效对应区域，否则框选/取消选中/移动之后框会留在屏幕上。
        外扩的量要盖住缩放手柄（手柄画在框线上，向两侧各伸出半个手柄）。
        """
        margin = resize.HANDLE_SIZE_PX
        dirty = QRegion()
        for box in getattr(self, "_selection_boxes", set()) | boxes:
            dirty += QRegion(QRect(int(box[0]) - margin, int(box[1]) - margin,
                                   int(box[2]) + margin * 2 + 1,
                                   int(box[3]) + margin * 2 + 1))
        self._selection_boxes = boxes
        self._last_dirty_region = dirty          # 便于自检/排查
        if not dirty.isEmpty():
            self.viewport().update(dirty)

    def _on_selection_changed(self) -> None:
        self._invalidate_selection_boxes(self._selection_boxes_now())

    def _on_scene_changed(self, _region=None) -> None:
        """图形项被移动/改形时，选中框也跟着动，缓存必须同步刷新。

        否则缓存里存的是移动前的位置，下一次失效重绘只刷新老位置，
        新位置上的框就成了永远擦不掉的残影。
        """
        scene = self.scene()
        if not getattr(self, "_selection_boxes", set()) and (
                scene is None or not scene.selectedItems()):
            return
        self._invalidate_selection_boxes(self._selection_boxes_now())

    # ------------------------------------------------------------- 缩放手柄
    def resize_target(self):
        """当前可缩放的图形项（只选中一个、且它支持缩放时）。"""
        scene = self.scene()
        if scene is None:
            return None
        selected = [it for it in scene.selectedItems()
                    if it.isVisible() and getattr(it, "is_resizable", None)
                    and it.is_resizable()]
        return selected[0] if len(selected) == 1 else None

    def selection_view_rect(self, item) -> QRect:
        """图形项外框的视口矩形（缩放手柄按它摆放）。"""
        return self._selection_view_rect(item)

    def handle_points_view(self) -> list:
        """当前缩放手柄的视口坐标（顺序见 canvas.resize）。"""
        item = self.resize_target()
        if item is None:
            return []
        return [QPointF(point) for point in
                resize.handle_points(QRectF(self._selection_view_rect(item)))]

    def handle_at(self, viewport_pos) -> int:
        """命中测试：鼠标是否落在某个缩放手柄上（是则返回编号，否则 None）。"""
        item = self.resize_target()
        if item is None:
            return None
        rect = QRectF(self._selection_view_rect(item))
        return resize.handle_at(rect, viewport_pos)

    def handle_cursor(self, index) -> Qt.CursorShape:
        return resize.CURSORS.get(index, Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------- 视口叠加层
    def set_overlay_circle(self, center: QPointF = None, radius: float = 0.0) -> None:
        """在视口上叠加一个圆（橡皮擦作用范围提示）；传 None 清除。

        画在 *视口* 而不是场景里：这样它不会被保存进 .wbd、不会出现在导出
        的 PNG 里，也不需要为它维护图形项的生命周期。
        """
        new_state = None
        if center is not None and radius > 0:
            new_state = (QPointF(center), float(radius))

        dirty = QRegion()
        for state in (self._overlay, new_state):
            if state is not None:
                point, value = state
                rect = QRectF(point.x() - value - 2, point.y() - value - 2,
                              (value + 2) * 2, (value + 2) * 2).toAlignedRect()
                dirty += QRegion(rect)
        self._overlay = new_state
        if not dirty.isEmpty():
            self.viewport().update(dirty)

    def _overlay_rect(self) -> QRect:
        if self._overlay is None:
            return QRect()
        point, value = self._overlay
        return QRectF(point.x() - value - 2, point.y() - value - 2,
                      (value + 2) * 2, (value + 2) * 2).toAlignedRect()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_selection_boxes(painter)
        self._draw_resize_handles(painter)
        self._draw_tool_overlay(painter)
        painter.end()

    def _draw_selection_boxes(self, painter: QPainter) -> None:
        """画选中虚线框。

        放在视口叠加层而不是 ``scene.drawForeground``：
        - 选中框不会出现在导出的 PNG 里，也不会被保存；
        - 绘制与失效都用同一套视口坐标，配合
          :meth:`_on_selection_changed` / :meth:`_on_scene_changed` 不会留残影。
        """
        scene = self.scene()
        if scene is None:
            return
        selected = [it for it in scene.selectedItems() if it.isVisible()]
        if not selected:
            return
        pen = QPen(QColor(38, 132, 255, 220), 1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in selected:
            painter.drawRect(self._selection_view_rect(item))
        painter.restore()

    def _draw_resize_handles(self, painter: QPainter) -> None:
        """给「可缩放的选中项」画 8 个手柄（图片 / 文字）。

        只选中一个可缩放对象时出现；形状/笔迹没有手柄（它们的尺寸靠重新绘制）。
        画在视口叠加层：不进 .wbd、不会出现在导出的 PNG 里。
        """
        points = self.handle_points_view()
        if not points:
            return
        half = resize.HANDLE_SIZE_PX / 2.0
        pen = QPen(QColor(38, 132, 255, 230), 1.0)
        pen.setCosmetic(True)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(QColor(255, 255, 255, 240))
        for point in points:
            painter.drawRect(QRectF(point.x() - half, point.y() - half,
                                    resize.HANDLE_SIZE_PX, resize.HANDLE_SIZE_PX))
        painter.restore()

    def _draw_tool_overlay(self, painter: QPainter) -> None:
        if self._overlay is None:
            return
        point, radius = self._overlay
        pen = QPen(QColor(70, 80, 95, 210), 1.2)
        pen.setCosmetic(True)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(QColor(120, 135, 155, 38))
        painter.drawEllipse(point, radius, radius)
        painter.restore()

    # ------------------------------------------------------------- 视图操作
    def center_on_scene_point(self, point: QPointF) -> None:
        self.centerOn(point)
