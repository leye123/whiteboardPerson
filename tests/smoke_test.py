"""白板应用冒烟测试（无显示器环境可运行）。

用法：
    python tests/smoke_test.py
"""
import os
import sys

# 允许从仓库根目录导入包
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Windows 控制台默认是 GBK，中文/符号输出会报 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

# ---------------------------------------------------------------- 隔离用户设置
def _make_tmp_dir() -> str:
    """在仓库内创建测试用临时目录。

    不用 ``tempfile.mkdtemp``：它创建的 0700 目录在部分受限/沙箱环境里
    后续无法写入（POSIX 权限被映射成 ACL）。这里用 0o777 自建目录。
    """
    import uuid

    base = os.path.join(_ROOT, ".test_tmp")
    os.makedirs(base, mode=0o777, exist_ok=True)
    path = os.path.join(base, "run_" + uuid.uuid4().hex[:8])
    os.makedirs(path, mode=0o777)
    return path


_tmp = _make_tmp_dir()
# 必须显式指定配置与数据位置：
# QSettings.setDefaultFormat/setPath 在 Windows 上拦不住 QSettings(org, app)，
# 否则测试会把偏好写进用户真实配置（注册表），还会反过来污染测试；
# 数据目录也要隔离，免得测试往软件目录里丢 autosave.wbd。
os.environ["WHITEBOARD_CONFIG"] = os.path.join(_tmp, "whiteboard.ini")
os.environ["WHITEBOARD_DATA_DIR"] = os.path.join(_tmp, "data")
os.makedirs(os.environ["WHITEBOARD_DATA_DIR"], mode=0o777, exist_ok=True)

# ---------------------------------------------------------------- 避免弹窗挂起
def _fake_question(parent, title, text, *args, **kwargs):
    yes_titles = ("删除页面", "恢复默认设置")
    return (QMessageBox.StandardButton.Yes
            if any(word in str(title) for word in yes_titles)
            else QMessageBox.StandardButton.No)


QMessageBox.question = staticmethod(_fake_question)
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))

from main_window import MainWindow  # noqa: E402
from canvas.items import StrokeItem  # noqa: E402
from tools.text_tool import TextEditDialog  # noqa: E402
TextEditDialog.ask = staticmethod(lambda *a, **k: "测试文字")


def page_counts(win):
    return [p.item_count() for p in win.pages]


# ---------------------------------------------------------------- 鼠标事件
# QTest.mouseMove 不带按键状态，拖拽类交互（画笔/形状/选择）收不到 move 事件，
# 因此这里直接构造 QMouseEvent 发送，保证 buttons() 状态与真实操作一致。
def _send(widget, kind, pos, button, buttons, modifiers=Qt.KeyboardModifier.NoModifier):
    local = QPointF(pos)
    event = QMouseEvent(kind, local, QPointF(widget.mapToGlobal(pos)),
                        button, buttons, modifiers)
    QApplication.sendEvent(widget, event)


def click(widget, pos, modifiers=Qt.KeyboardModifier.NoModifier):
    _send(widget, QEvent.Type.MouseButtonPress, pos,
          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers)
    _send(widget, QEvent.Type.MouseButtonRelease, pos,
          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, modifiers)


def drag(widget, start, waypoints, modifiers=Qt.KeyboardModifier.NoModifier):
    """按下 → 依次经过中间点 → 在最后一个点抬起。"""
    points = [start] + list(waypoints)
    _send(widget, QEvent.Type.MouseButtonPress, points[0],
          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers)
    for pos in points[1:-1]:
        _send(widget, QEvent.Type.MouseMove, pos,
              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers)
    _send(widget, QEvent.Type.MouseMove, points[-1],
          Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers)
    _send(widget, QEvent.Type.MouseButtonRelease, points[-1],
          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, modifiers)


def double_click(widget, pos):
    click(widget, pos)
    _send(widget, QEvent.Type.MouseButtonDblClick, pos,
          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    app.processEvents()
    vp = win.view.viewport()

    checks = 0
    def ok(cond, msg):
        nonlocal checks
        checks += 1
        if not cond:
            raise AssertionError("FAILED: " + msg)
        print("  ok -", msg)

    # ---- 1. 画笔绘制
    win.set_active_tool("pen")
    drag(vp, QPoint(200, 200),
         [QPoint(210, 240), QPoint(225, 240), QPoint(240, 240),
          QPoint(255, 240), QPoint(270, 240), QPoint(285, 240),
          QPoint(300, 250)])
    app.processEvents()
    ok(page_counts(win) == [1], "画笔提交 1 条笔画")
    stroke_item = win.view.scene().items()[0]
    ok(stroke_item.point_count() >= 5, "笔画采样点已记录（平滑曲线数据）")

    # ---- 2. 撤销 / 重做
    win.undo_stack.undo()
    ok(page_counts(win) == [0], "撤销后笔画被移除")
    win.undo_stack.redo()
    ok(page_counts(win) == [1], "重做后笔画恢复")

    # ---- 3. 形状工具（矩形）
    win.set_active_tool("rect")
    drag(vp, QPoint(500, 200), [QPoint(570, 260), QPoint(640, 330)])
    app.processEvents()
    ok(page_counts(win) == [2], "矩形绘制成功")

    # ---- 4. 橡皮擦：部分擦除（把笔画擦断，而不是整条删除）
    win.set_active_tool("eraser")
    count_before = page_counts(win)[0]
    strokes_before = len([it for it in win.view.scene().items()
                          if isinstance(it, StrokeItem)])
    click(vp, QPoint(240, 240))          # 落在笔画中段
    app.processEvents()
    strokes_after = len([it for it in win.view.scene().items()
                         if isinstance(it, StrokeItem)])
    ok(strokes_before == 1 and strokes_after == 2,
       f"橡皮擦把笔画擦成两段（{strokes_before} -> {strokes_after}）")
    ok(page_counts(win) == [count_before + 1], "被擦断的笔迹替换掉了原笔画")
    win.undo_stack.undo()
    ok(page_counts(win) == [count_before], "擦除可撤销（恢复成一条完整笔画）")

    # ---- 4b. 橡皮擦拖拽：整段手势只产生一个撤销步骤
    win.set_active_tool("eraser")
    steps_before = win.undo_stack.index()
    original = [it for it in win.view.scene().items()
                if isinstance(it, StrokeItem)][0]
    points_before = original.point_count()
    drag(vp, QPoint(215, 240), [QPoint(240, 240), QPoint(265, 240)])
    app.processEvents()
    ok(win.undo_stack.index() == steps_before + 1, "一次橡皮拖拽 = 一个撤销步骤")
    survivors = [it for it in win.view.scene().items()
                 if isinstance(it, StrokeItem)]
    points_after = sum(it.point_count() for it in survivors)
    ok(survivors and points_after < points_before,
       f"拖拽只擦掉经过的采样点（{points_before} -> {points_after} 点，"
       f"{len(survivors)} 段）")
    win.undo_stack.undo()
    app.processEvents()
    restored = [it for it in win.view.scene().items()
                if isinstance(it, StrokeItem)]
    ok(page_counts(win)[0] == count_before and len(restored) == 1
       and restored[0].point_count() == points_before,
       "拖拽擦除可整体撤销（原始笔画与点数完整恢复）")

    # ---- 5. 文字（mock 对话框）
    win.set_active_tool("text")
    click(vp, QPoint(600, 400))
    app.processEvents()
    ok(page_counts(win) == [3], "文字对象插入")

    # ---- 6. 选择 + 拖动（带撤销）
    win.set_active_tool("selector")
    scene = win.view.scene()
    # 注意：不能用 hasattr(it, "path") 找笔画 —— 矩形/多边形等形状改成
    # 路径实现之后也带 path 属性，这里按类型精确筛选。
    stroke = [it for it in scene.items() if isinstance(it, StrokeItem)]
    ok(len(stroke) == 1, "找到笔画对象")
    target = stroke[0]
    pos_before = target.scenePos()
    # 点击笔画中部选中并拖走
    drag(vp, QPoint(200, 200), [QPoint(220, 230), QPoint(240, 260)])
    app.processEvents()
    ok(target.scenePos() != pos_before, "对象被拖动")
    win.undo_stack.undo()
    ok(target.scenePos() == pos_before, "拖动可撤销")

    # ---- 6b. 框选（橡皮筋）能选中笔迹
    # 注意：QGraphicsScene.setSelectionArea 的 mode 必须用关键字传参，
    # 位置传枚举会被解析成 QTransform 重载（历史上直接导致框选失效/崩溃）。
    scene.clearSelection()
    drag(vp, QPoint(150, 150), [QPoint(250, 200), QPoint(360, 300)])
    app.processEvents()
    selected = scene.selectedItems()
    ok(len(selected) >= 1, f"框选选中了 {len(selected)} 个对象")
    ok(any(isinstance(it, StrokeItem) for it in selected), "框选结果里包含手绘笔迹")
    scene.clearSelection()

    # ---- 6c. 空格平移按下/松开不抛异常（cursor 曾按方法调用）
    from PySide6.QtGui import QKeyEvent
    win.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                                     Qt.KeyboardModifier.NoModifier))
    win.view.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space,
                                       Qt.KeyboardModifier.NoModifier))
    ok(win.view.viewport().cursor().shape() == win.view.current_tool.cursor,
       "松开空格后光标恢复为当前工具的光标")

    # ---- 6d. 选中框不能残留（否则框选/移动后白板上会叠很多虚线框）
    def bare_rubber_rects():
        """场景里不该存在的裸矩形（框选用的橡皮筋）。"""
        return [it for it in scene.items()
                if it.parentItem() is None and not hasattr(it, "to_dict")]

    target.setSelected(True)
    app.processEvents()
    boxes = set(win.view._selection_boxes)
    ok(len(boxes) == 1, "选中后记录了 1 个选中框")
    scene.clearSelection()
    app.processEvents()
    ok(win.view._selection_boxes == set(), "取消选中后不再缓存旧框")
    box = next(iter(boxes))
    center = QPoint(int(box[0] + box[2] / 2), int(box[1] + box[3] / 2))
    ok(win.view._last_dirty_region.contains(center),
       "旧选中框的区域被标记为脏（会被重绘掉，不会留残影）")

    # 框选过程中切走工具：框选矩形必须被清掉
    win.set_active_tool("selector")
    _send(vp, QEvent.Type.MouseButtonPress, QPoint(150, 150),
          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    _send(vp, QEvent.Type.MouseMove, QPoint(260, 240),
          Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    app.processEvents()
    ok(len(bare_rubber_rects()) == 1, "框选进行中，场景里有 1 个框选矩形")
    win.set_active_tool("pen")            # 中途切工具，不给 release
    app.processEvents()
    ok(bare_rubber_rects() == [], "切换工具后残留的框选矩形被清除")
    win.set_active_tool("selector")
    # 再来一次完整的框选+移动，确认场景里没有累积的框
    drag(vp, QPoint(150, 150), [QPoint(260, 240), QPoint(360, 300)])
    drag(vp, QPoint(200, 200), [QPoint(220, 230)])
    app.processEvents()
    ok(bare_rubber_rects() == [], "反复框选+移动后场景里没有累积的框")
    scene.clearSelection()

    # ---- 6f. 拖动画布工具：左键拖拽即可平移，且不会改动内容
    win.set_active_tool("pan")
    ok(win.view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor,
       "拖动工具的光标是张开的手型")
    scene.clearSelection()
    items_before = sorted(id(it) for it in scene.items())
    positions_before = [it.scenePos() for it in scene.items()]
    center_before = win.view.mapToScene(vp.rect().center())
    bar_before = (win.view.horizontalScrollBar().value(),
                  win.view.verticalScrollBar().value())
    drag(vp, QPoint(600, 400), [QPoint(650, 430), QPoint(700, 460)])
    center_after = win.view.mapToScene(vp.rect().center())
    bar_after = (win.view.horizontalScrollBar().value(),
                 win.view.verticalScrollBar().value())
    ok(bar_before != bar_after or center_before != center_after,
       f"拖动画布工具平移了视图（{bar_before} -> {bar_after}）")
    ok(sorted(id(it) for it in scene.items()) == items_before
       and [it.scenePos() for it in scene.items()] == positions_before,
       "拖动画布不会新建/移动任何图形项")
    win.set_active_tool("pen")            # 切回画笔，确认工具能正常切换
    ok(win.view.viewport().cursor().shape() == Qt.CursorShape.CrossCursor,
       "切回画笔后光标恢复为十字")
    win.set_active_tool("selector")

    # ---- 6e. 移动选中项后，选中框缓存必须跟着走（否则新位置会留残影）
    target.setSelected(True)
    app.processEvents()
    old_boxes = set(win.view._selection_boxes)
    ok(len(old_boxes) == 1, "重新选中后记录了 1 个框")
    origin = target.scenePos()
    target.setPos(origin + QPointF(70, 45))
    app.processEvents()
    new_boxes = set(win.view._selection_boxes)
    ok(new_boxes and new_boxes != old_boxes, "移动后选中框缓存跟着更新到新位置")
    region = win.view._last_dirty_region
    for label, boxes in (("旧位置", old_boxes), ("新位置", new_boxes)):
        box = next(iter(boxes))
        probe = QRect(int(box[0]) + 3, int(box[1]) + 3,
                      max(1, int(box[2]) - 6), max(1, int(box[3]) - 6))
        ok(region.contains(probe), f"{label}的选中框都被标脏（不会留残影）")
    target.setPos(origin)
    app.processEvents()
    scene.clearSelection()
    app.processEvents()
    ok(win.view._selection_boxes == set(), "取消选中后缓存清空")

    # ---- 7. 每页独立撤销 + 页面管理
    content_count = page_counts(win)[0]
    ok(content_count == 3, "页面 1 上有 3 个对象")
    win.add_page()
    ok(len(win.pages) == 2 and win.view.scene() is win.pages[1].scene, "新建并切换到页面 2")
    ok(win.undo_stack is win.pages[1].undo_stack, "撤销栈已切到当前页")
    win.set_active_tool("pen")
    drag(vp, QPoint(150, 150), [QPoint(180, 180)])
    app.processEvents()
    ok(page_counts(win) == [content_count, 1], "页面 2 上新增 1 个对象")
    win.undo_stack.undo()
    ok(page_counts(win) == [content_count, 0], "在页面 2 撤销只影响页面 2")
    win.delete_page()             # 删除刚建的空白页
    app.processEvents()
    ok(len(win.pages) == 1, "删除页面后剩 1 页")
    ok(page_counts(win) == [content_count], "内容页未受影响")
    ok(win.view.scene() is win.pages[0].scene, "视图已切回内容页")

    # ---- 8. 序列化 round-trip + .wbd 文件
    import json
    from persistence import file_handler as fh
    from persistence import serializer
    doc = serializer.document_to_dict(win.pages, win.page_index)
    text = serializer.serialize_document(win.pages, win.page_index)
    pages2, cur2 = serializer.deserialize_document(text)
    ok(page_counts(win) == [p.item_count() for p in pages2], "序列化 round-trip 一致")
    ok(json.loads(text)["app"] == "whiteboard-pyside", "文件包含应用标识")

    save_path = os.path.join(_tmp, "demo.wbd")
    fh.save_document(save_path, win.pages, win.page_index)
    pages3, cur3 = fh.load_document(save_path)
    ok([p.item_count() for p in pages3] == page_counts(win), ".wbd 保存/读取 round-trip")
    ok(cur3 == win.page_index, "当前页索引被保留")
    ok(page_counts(win) == [3], "保存前后对象数量不变")

    # ---- 9. 通过主窗口打开文件
    win.open_file(save_path)
    ok(page_counts(win) == [p.item_count() for p in pages3], "主窗口打开 .wbd")
    ok(win.undo_stack.isClean(), "打开文件后撤销栈为干净状态")
    ok(win.view.scene().itemsBoundingRect().width() > 1, "打开后场景有可见内容")

    # ---- 10. 导出 PNG
    png_path = os.path.join(_tmp, "out.png")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (png_path, ""))
    win.export_png()
    ok(os.path.exists(png_path) and os.path.getsize(png_path) > 0, "PNG 导出成功")
    image = QImage(png_path)
    ok(image.width() > 10 and image.height() > 10
       and image.width() < 5000 and image.height() < 5000,
       f"PNG 尺寸合理（{image.width()}x{image.height()}）")

    # ---- 10b. 选中框不该被画进导出的 PNG
    png_selected = os.path.join(_tmp, "out_selected.png")
    scene = win.view.scene()
    scene.clearSelection()
    item = [it for it in scene.items() if hasattr(it, "to_dict")][0]
    item.setSelected(True)
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (png_selected, ""))
    win.export_png()
    scene.clearSelection()
    ok(QImage(png_selected) == QImage(png_path),
       "导出 PNG 与“无选中状态”的结果完全一致（不含选中框）")

    # ---- 11. 主题切换不抛异常
    win._apply_theme("dark")
    win._apply_theme("light")
    ok(True, "深浅主题切换正常")

    # ---- 11b. 恢复默认设置
    from core.settings import DEFAULT_THICKNESS, DEFAULT_TOOL
    win.settings.set_thickness(33.0)
    win.settings.set_current_tool("eraser")
    win.reset_settings()
    ok(win.settings.thickness() == DEFAULT_THICKNESS
       and win.settings.current_tool() == DEFAULT_TOOL,
       "恢复默认设置清掉了被改过的偏好")
    ok(abs(win.tools["eraser"].size - (DEFAULT_THICKNESS * 2 + 8)) < 0.01,
       "橡皮尺寸按新的温和映射计算（不再是 粗细×4+8）")

    # ---- 12. 新建文档
    win.new_document()
    ok(len(win.pages) == 1 and page_counts(win) == [0], "新建空白文档")

    # ---- 13. 导出内容必须完整、居中、留白正确
    # 曾经因为 painter.scale(factor) 与 scene.render(target=整图) 双重缩放，
    # 只导出了左上角那一块（表现为「导出图片被裁掉一半」）。
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor
    from canvas.items import RectItem

    export_scene = win.view.scene()
    export_scene.addItem(RectItem(QRectF(0.0, 0.0, 200.0, 100.0), QColor(0, 0, 0), 2.0))
    app.processEvents()
    png_geometry = os.path.join(_tmp, "out_geometry.png")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (png_geometry, ""))
    win.export_png()
    geometry_image = QImage(png_geometry)
    background = geometry_image.pixelColor(2, 2)
    ink_x, ink_y = [], []
    for y in range(geometry_image.height()):
        for x in range(geometry_image.width()):
            color = geometry_image.pixelColor(x, y)
            if (abs(color.red() - background.red()) > 40
                    or abs(color.green() - background.green()) > 40
                    or abs(color.blue() - background.blue()) > 40):
                ink_x.append(x)
                ink_y.append(y)
    ok(bool(ink_x), "导出图片里有内容")
    left, top = min(ink_x), min(ink_y)
    right = geometry_image.width() - 1 - max(ink_x)
    bottom = geometry_image.height() - 1 - max(ink_y)
    ok(abs(left - right) <= 3 and abs(top - bottom) <= 3,
       f"导出内容四周留白均匀（左{left} 右{right} 上{top} 下{bottom}）")
    # 期望 24 场景像素 × 2 倍超采样 = 48；自定义图形项会在边界外多留
    # 1 像素抗锯齿余量（见 canvas.items.pen_bounds），所以允许 48~52。
    ok(48 <= left <= 52,
       f"留白等于设定边距 24 × 2 倍超采样（实测 {left}）")
    ok(abs(max(ink_x) - min(ink_x) + 1 - 404) <= 3
       and abs(max(ink_y) - min(ink_y) + 1 - 204) <= 3,
       f"内容尺寸正确（{max(ink_x) - min(ink_x) + 1}x{max(ink_y) - min(ink_y) + 1}，"
       f"期望 404x204）")

    # ------------------------------------------------------------------
    # 以下为「形状种类 / 虚线 / 文字排版 / 导入图片」的回归检查
    # ------------------------------------------------------------------
    from canvas.items import (
        EllipseItem,
        ImageItem,
        LineItem,
        PolygonShapeItem,
        TextItem,
    )
    from core.text_format import TextFormat
    from widgets.text_dialog import TextDialog

    def fresh_scene():
        """清空当前页并让撤销栈变干净。

        不用 ``new_document()``：它会因为“有未保存改动”弹确认框，
        而冒烟测试里那个确认框被替换成一律返回 No。
        """
        stack = win.undo_stack
        stack.clear()
        scene_ = win.view.scene()
        for item in list(scene_.items()):
            scene_.removeItem(item)
        stack.clear()
        stack.setClean()
        app.processEvents()
        return scene_

    # ---- 14. 封闭图形：矩形 / 圆角矩形 / 椭圆 / 三角形 / 菱形 / 五角星
    scene = fresh_scene()
    closed_kinds = ("rect", "round_rect", "ellipse", "triangle", "diamond", "star")
    for index, kind in enumerate(closed_kinds):
        win.set_active_tool(kind)
        x = 120 + index * 90
        drag(vp, QPoint(x, 160), [QPoint(x + 60, 220), QPoint(x + 70, 250)])
        app.processEvents()
    ok(page_counts(win)[0] == len(closed_kinds),
       f"{len(closed_kinds)} 种封闭图形都能绘制（实际 {page_counts(win)[0]}）")
    shapes = list(scene.items())
    rounded = [it for it in shapes if isinstance(it, RectItem) and it.radius_value() > 0]
    ok(len(rounded) == 1, "圆角矩形的圆角半径大于 0")
    ok(len([it for it in shapes if isinstance(it, EllipseItem)]) == 1, "椭圆绘制成功")
    ok(len([it for it in shapes if isinstance(it, PolygonShapeItem)]) == 3,
       "三角形 / 菱形 / 五角星各绘制出一个")
    kinds = sorted(it.kind() for it in shapes if isinstance(it, PolygonShapeItem))
    ok(kinds == ["diamond", "star", "triangle"], f"多边形种类正确：{kinds}")

    # 橡皮擦对多边形/文字/图片是整体删除（以前这三类根本擦不掉）
    win.set_active_tool("eraser")
    before = page_counts(win)[0]
    star = [it for it in scene.items()
            if isinstance(it, PolygonShapeItem) and it.kind() == "star"][0]
    # 命中判定用的是「描边路径」（未填充图形点中间不算命中），
    # 所以取路径上的点而不是外接矩形中心。
    hit = star.path().pointAtPercent(0.05) + star.scenePos()
    click(vp, win.view.mapFromScene(hit))
    app.processEvents()
    ok(page_counts(win)[0] == before - 1, "橡皮擦能把五角星整体擦掉")

    # ---- 15. 线段：直线 / 虚线 / 箭头 / 虚线箭头 / 双向箭头
    scene = fresh_scene()
    line_kinds = ("line", "dashed_line", "arrow", "dashed_arrow", "double_arrow")
    for index, kind in enumerate(line_kinds):
        win.set_active_tool(kind)
        y = 150 + index * 70
        drag(vp, QPoint(180, y), [QPoint(300, y + 30), QPoint(380, y + 40)])
        app.processEvents()
    lines = [it for it in scene.items() if isinstance(it, LineItem)]
    ok(len(lines) == len(line_kinds), f"{len(line_kinds)} 种线段都能绘制")
    combos = {}
    for item in lines:
        data = item.to_dict()
        combos[(data["arrow"], data["line_style"])] = \
            combos.get((data["arrow"], data["line_style"]), 0) + 1
    ok(combos.get(("none", "solid")) == 1 and combos.get(("none", "dash")) == 1,
       "直线 = 实线，虚线 = 虚线")
    ok(combos.get(("end", "solid")) == 1 and combos.get(("end", "dash")) == 1,
       "箭头/虚线箭头都带终点箭头，线型分别为实线/虚线")
    ok(combos.get(("both", "solid")) == 1, "双向箭头两端都有箭头")

    # 线型下拉框：任意形状都能画成虚线（这里用点线验证）
    scene = fresh_scene()
    win.line_style_picker.set_current("dot")
    app.processEvents()
    ok(win.tools["rect"].line_style == "dot" and win.settings.line_style() == "dot",
       "线型下拉框同步到形状工具与设置")
    win.set_active_tool("round_rect")
    drag(vp, QPoint(200, 200), [QPoint(320, 280)])
    app.processEvents()
    drawn = [it for it in scene.items() if isinstance(it, RectItem)]
    ok(len(drawn) == 1 and drawn[0].to_dict()["line_style"] == "dot",
       "圆角矩形按所选线型（点线）绘制")
    win.line_style_picker.set_current("solid")
    ok(win.tools["round_rect"].line_style == "solid", "线型可以切回实线")

    # ---- 15b. 画笔也遵循线型（曾经选了虚线/点线，画出来依然是实线）
    scene = fresh_scene()
    win.set_active_tool("pen")
    win.line_style_picker.set_current("dash")
    app.processEvents()
    ok(win.tools["pen"].line_style == "dash", "线型同步到画笔工具")
    drag(vp, QPoint(200, 200),
         [QPoint(230, 220), QPoint(260, 235), QPoint(290, 243),
          QPoint(320, 250), QPoint(350, 256), QPoint(380, 260)])
    app.processEvents()
    strokes = [it for it in scene.items() if isinstance(it, StrokeItem)]
    ok(len(strokes) == 1 and strokes[0].to_dict()["line_style"] == "dash",
       "画笔笔迹按所选线型（虚线）绘制")

    win.set_active_tool("eraser")
    click(vp, QPoint(290, 243))              # 落在笔迹中段的采样点上
    app.processEvents()
    fragments = [it for it in scene.items() if isinstance(it, StrokeItem)]
    ok(len(fragments) == 2
       and all(it.to_dict()["line_style"] == "dash" for it in fragments),
       f"擦断后的笔迹碎片保持虚线（{len(fragments)} 段）")
    win.line_style_picker.set_current("solid")

    # ---- 16. 文字：字体 / 字号 / 颜色 / 粗斜体 / 自动换行 + 双击编辑
    scene = fresh_scene()
    sample = TextFormat(family="Consolas", pixel_size=28, color=QColor("#c0392b"),
                        bold=True, wrap=True, text_width=180, align="center")
    TextDialog.ask = staticmethod(lambda *a, **k: ("自动换行\n的文字", sample))
    win.set_active_tool("text")
    click(vp, QPoint(420, 300))
    app.processEvents()
    texts = [it for it in scene.items() if isinstance(it, TextItem)]
    ok(len(texts) == 1, "文字对象插入")
    text_item = texts[0]
    ok(text_item.font().pixelSize() == 28 and text_item.font().bold(),
       "字号与粗体按对话框参数生效")
    ok(text_item.font().family() == "Consolas", "字体族按对话框参数生效")
    ok(text_item.is_wrapped() and abs(text_item.text_width() - 180) < 0.01,
       "自动换行与折行宽度按参数生效")
    ok(text_item.defaultTextColor().name() == "#c0392b", "文字颜色按参数生效")
    ok(text_item.text_align() == "center", "对齐方式按参数生效")
    clone = TextItem.from_dict(text_item.to_dict())
    ok(clone.to_dict() == text_item.to_dict(),
       "文字排版可无损序列化（字体/字号/颜色/换行/对齐）")

    edited = TextFormat(pixel_size=12, color=QColor("#1a4fb4"), wrap=False)
    TextDialog.ask = staticmethod(lambda *a, **k: ("改过的文字", edited))
    win.set_active_tool("selector")
    screen_pos = win.view.mapFromScene(text_item.scenePos() + QPointF(30, 12))
    double_click(vp, screen_pos)
    app.processEvents()
    ok(text_item.toPlainText() == "改过的文字"
       and text_item.font().pixelSize() == 12
       and not text_item.is_wrapped(),
       "双击文字可编辑内容与排版")
    win.undo_stack.undo()
    app.processEvents()
    ok(text_item.toPlainText() == "自动换行\n的文字"
       and text_item.font().pixelSize() == 28
       and text_item.is_wrapped(),
       "文字编辑可撤销（内容与排版一起回退）")

    # ---- 17. 导入图片（回归：mapToScene(QPointF) 曾抛 TypeError，表现为“点了没反应”）
    scene = fresh_scene()
    img_path = os.path.join(_tmp, "sample.png")
    sample_image = QImage(120, 80, QImage.Format.Format_ARGB32)
    sample_image.fill(QColor("#2f7bd8"))
    ok(sample_image.save(img_path), "准备好测试图片")
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (img_path, ""))
    failure = None
    try:
        win.import_image()
    except Exception as exc:  # noqa: BLE001
        failure = exc
    app.processEvents()
    ok(failure is None, f"导入图片不再抛异常（{failure!r}）")
    images = [it for it in scene.items() if isinstance(it, ImageItem)]
    ok(len(images) == 1, "导入的图片进入场景")
    ok(images[0].pixmap().width() == 120 and images[0].pixmap().height() == 80,
       "小于视图的图片保持原始尺寸")
    ok(images[0].isSelected(), "导入后图片被选中（可以马上拖动）")

    warnings_seen = []
    QMessageBox.warning = staticmethod(lambda *a, **k: warnings_seen.append(a[1:]))
    bad_path = os.path.join(_tmp, "not_an_image.txt")
    with open(bad_path, "w", encoding="utf-8") as handle:
        handle.write("这根本不是图片")
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (bad_path, ""))
    win.import_image()
    ok(len(warnings_seen) == 1, "读到非法图片时给出明确提示（而不是静默失败）")

    print(f"\n全部 {checks} 项冒烟检查通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(str(exc))
        sys.exit(1)
