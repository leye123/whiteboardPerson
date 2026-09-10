"""缩放手柄的几何计算（canvas/resize.py）。

选中**图片**或**文字**时，视图会在选中框上画出 8 个手柄（四角 + 四边中点），
拖动手柄即可自由伸缩：

* 四角手柄等比缩放（按住 Shift 时四边手柄也等比）；
* 四边手柄单向拉伸（图片可以拉成扁的，文字会按主方向等比改字号）；
* 缩放时**对角（或对边）保持不动**，和常见绘图软件的手感一致。

这里只管几何：给定原矩形、手柄编号、鼠标位置，算出「新矩形」。
具体怎么把这个矩形应用到图形项上由图形项自己实现
（图片改 scale，文字改字号）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt

# 手柄编号：0 左上、1 上、2 右上、3 右、4 右下、5 下、6 左下、7 左
TOP_LEFT, TOP, TOP_RIGHT, RIGHT, BOTTOM_RIGHT, BOTTOM, BOTTOM_LEFT, LEFT = range(8)
HANDLE_COUNT = 8
CORNER_HANDLES = (TOP_LEFT, TOP_RIGHT, BOTTOM_RIGHT, BOTTOM_LEFT)
# 拖动这些手柄会改左/右边，这些会改上/下边
LEFT_HANDLES = (TOP_LEFT, LEFT, BOTTOM_LEFT)
RIGHT_HANDLES = (TOP_RIGHT, RIGHT, BOTTOM_RIGHT)
TOP_HANDLES = (TOP_LEFT, TOP, TOP_RIGHT)
BOTTOM_HANDLES = (BOTTOM_LEFT, BOTTOM, BOTTOM_RIGHT)

# 手柄的屏幕尺寸（视口像素）与命中容差
HANDLE_SIZE_PX = 8
HANDLE_TOLERANCE_PX = 7
# 缩放后的最小边长（场景单位），避免拖成一条线之后再也不好选回来
MIN_SIZE = 12.0

# 每个手柄对应的鼠标指针，方便用户预判能往哪拖
CURSORS = {
    TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
    BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
    BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
    TOP: Qt.CursorShape.SizeVerCursor,
    BOTTOM: Qt.CursorShape.SizeVerCursor,
    LEFT: Qt.CursorShape.SizeHorCursor,
    RIGHT: Qt.CursorShape.SizeHorCursor,
}


def handle_points(rect: QRectF) -> list:
    """8 个手柄在给定矩形上的位置（顺序见模块开头的编号说明）。"""
    left, right = rect.left(), rect.right()
    top, bottom = rect.top(), rect.bottom()
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    return [
        QPointF(left, top), QPointF(center_x, top), QPointF(right, top),
        QPointF(right, center_y), QPointF(right, bottom),
        QPointF(center_x, bottom), QPointF(left, bottom),
        QPointF(left, center_y),
    ]


def anchor_point(rect: QRectF, index: int) -> QPointF:
    """拖动某个手柄时保持不动的那个点（对角点 / 对边中点）。"""
    return handle_points(rect)[(index + 4) % HANDLE_COUNT]


def resized_rect(rect: QRectF, index: int, point: QPointF,
                 keep_aspect: bool = False) -> QRectF:
    """按手柄拖动结果算出新矩形。

    :param rect: 原始矩形（场景坐标）
    :param index: 被拖动的手柄编号
    :param point: 鼠标当前位置（场景坐标）
    :param keep_aspect: 是否保持原宽高比
    """
    rect = QRectF(rect).normalized()
    left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()

    if not keep_aspect:
        if index in LEFT_HANDLES:
            left = min(point.x(), right - MIN_SIZE)
        if index in RIGHT_HANDLES:
            right = max(point.x(), left + MIN_SIZE)
        if index in TOP_HANDLES:
            top = min(point.y(), bottom - MIN_SIZE)
        if index in BOTTOM_HANDLES:
            bottom = max(point.y(), top + MIN_SIZE)
        return QRectF(QPointF(left, top), QPointF(right, bottom))

    # 等比缩放：锚点（对角点 / 对边）保持不动
    anchor = anchor_point(rect, index)
    width, height = rect.width(), rect.height()
    ratios = []
    if index in LEFT_HANDLES or index in RIGHT_HANDLES:
        if width > 0:
            ratios.append(abs(point.x() - anchor.x()) / width)
    if index in TOP_HANDLES or index in BOTTOM_HANDLES:
        if height > 0:
            ratios.append(abs(point.y() - anchor.y()) / height)
    ratio = max(ratios) if ratios else 1.0
    new_width = max(MIN_SIZE, width * ratio)
    new_height = max(MIN_SIZE, height * ratio)

    if index in LEFT_HANDLES:
        left, right = anchor.x() - new_width, anchor.x()
    elif index in RIGHT_HANDLES:
        left, right = anchor.x(), anchor.x() + new_width
    if index in TOP_HANDLES:
        top, bottom = anchor.y() - new_height, anchor.y()
    elif index in BOTTOM_HANDLES:
        top, bottom = anchor.y(), anchor.y() + new_height
    return QRectF(QPointF(left, top), QPointF(right, bottom))


def handle_at(rect_view: QRectF, point, tolerance: float = HANDLE_TOLERANCE_PX):
    """命中测试：返回鼠标下面的手柄编号（没有则 None）。

    在**视口坐标**里比较，这样手柄的命中范围与屏幕上的大小一致，
    不受画布缩放影响。
    """
    best, best_distance = None, None
    points = handle_points(rect_view)
    for index, handle in enumerate(points):
        dx = abs(handle.x() - point.x())
        dy = abs(handle.y() - point.y())
        if dx <= tolerance and dy <= tolerance:
            distance = dx * dx + dy * dy
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
    return best
