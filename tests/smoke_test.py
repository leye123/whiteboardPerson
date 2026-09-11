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
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QStyleOptionGraphicsItem,
)

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
from core.stroke import qcolor_from_rgba  # noqa: E402
from canvas.items import (  # noqa: E402
    EllipseItem,
    GroupItem,
    ImageItem,
    LineItem,
    PolygonShapeItem,
    RectItem,
    TextItem,
)
from core.page import BoardPage  # noqa: E402
from core.settings import DEFAULT_THICKNESS, DEFAULT_TOOL  # noqa: E402
from persistence import file_handler as fh  # noqa: E402
from persistence import file_handler as fh_module  # noqa: E402
from persistence import serializer  # noqa: E402


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
    # 实线笔迹擦断后必须仍然是「看得见的实线」：曾经因为给实线碎片设了
    # 虚线相位，Qt 把画笔切成「空图案的 CustomDashLine」，碎片整段隐形 →
    # 拖橡皮时有的有墨有的没墨，看起来就是闪烁。
    ok(all(it.line_style() == "solid" for it in survivors),
       "擦断后的碎片仍然是实线（不会换成别的线型）")
    ok(all(it.pen().style() is Qt.PenStyle.SolidLine for it in survivors),
       "实线碎片没有被切成 CustomDashLine（空图案会整段隐形）")
    steps_ink = []
    for fragment in survivors:
        shot = QImage(60, 30, QImage.Format.Format_ARGB32)
        shot.fill(QColor(255, 255, 255))
        painter = QPainter(shot)
        painter.translate(10 - fragment.points()[0].x(), 15 - fragment.points()[0].y())
        fragment.paint(painter, QStyleOptionGraphicsItem(), None)
        painter.end()
        ink = sum(1 for y in range(shot.height()) for x in range(shot.width())
                  if shot.pixelColor(x, y).lightness() < 200)
        steps_ink.append(ink)
    ok(all(ink > 0 for ink in steps_ink),
       f"每个碎片都真的画得出墨迹（{steps_ink}）—— 不会出现「隐形碎片」")
    win.undo_stack.undo()
    app.processEvents()
    restored = [it for it in win.view.scene().items()
                if isinstance(it, StrokeItem)]
    ok(page_counts(win)[0] == count_before and len(restored) == 1
       and restored[0].point_count() == points_before,
       "拖拽擦除可整体撤销（原始笔画与点数完整恢复）")

    # ---- 5. 字体框：拖出框大小，文字在侧边栏里输入
    win.set_active_tool("text")
    drag(vp, QPoint(560, 360), [QPoint(700, 400), QPoint(820, 440)])
    app.processEvents()
    ok(page_counts(win) == [3], "拖拽创建了字体框")
    box_item = [it for it in win.view.scene().items() if isinstance(it, TextItem)][0]
    ok(box_item.is_box(), "创建出来的是「字体框」（有固定框尺寸）")
    ok(box_item.is_empty(), "刚建好的框是空的，等待双击输入")
    ok(abs(box_item.text_width() - 260.0) < 12.0,
       f"框宽跟随拖拽距离（{box_item.text_width():.0f}）")
    ok(box_item.isSelected(), "新建的字体框自动选中，右侧参数栏直接可调")
    # 侧边栏输入文字（相当于双击后输入）
    win.property_panel.text_edit.setPlainText("测试文字")
    win._commit_style_changes()
    app.processEvents()
    ok(box_item.toPlainText() == "测试文字", "侧边栏输入的内容实时写进字体框")
    ok(win.undo_stack.undoText() == "修改参数", "输入文字进撤销栈")

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

    # ---- 14b. 橡皮擦只擦手绘笔迹：图形 / 线段 / 字体框 / 图片一概不碰
    # （v1.3.0：以前橡皮碰到这些对象会**整块删掉**，用户只想擦一点线条，
    #   旁边的图形就整个消失，非常容易误删）
    win.set_active_tool("eraser")
    before = page_counts(win)[0]
    steps_before = win.undo_stack.index()
    star = [it for it in scene.items()
            if isinstance(it, PolygonShapeItem) and it.kind() == "star"][0]
    hit = star.path().pointAtPercent(0.05) + star.scenePos()
    click(vp, win.view.mapFromScene(hit))          # 精确点在星形描边上
    app.processEvents()
    ok(page_counts(win)[0] == before and star.scene() is scene,
       "橡皮擦不碰图形（点在五角星描边上也删不掉它）")
    ok(win.undo_stack.index() == steps_before,
       "橡皮擦碰到图形时不会产生空的撤销步骤")

    # 顺手在这个场景里放上字体框 / 图片 / 线段，一起验证橡皮都不碰
    area = win.view.mapToScene(vp.rect()).boundingRect()
    box_item = TextItem("橡皮别碰我", QColor(0, 0, 0), 24)
    box_item.set_box_size(200.0, 90.0)
    box_item.setPos(area.left() + 40.0, area.top() + 40.0)
    scene.addItem(box_item)
    image_path = os.path.join(_tmp, "eraser_sample.png")
    sample = QImage(60, 40, QImage.Format.Format_ARGB32)
    sample.fill(QColor("#1a4fb4"))
    sample.save(image_path)
    image_item = ImageItem(QPixmap(image_path))
    image_item.setPos(area.left() + 300.0, area.top() + 40.0)
    scene.addItem(image_item)
    line_item = LineItem(QPointF(area.left() + 40.0, area.top() + 200.0),
                         QPointF(area.left() + 240.0, area.top() + 200.0),
                         QColor(0, 0, 0), 3.0)
    scene.addItem(line_item)
    app.processEvents()

    before = page_counts(win)[0]
    for item in (box_item, image_item, line_item):
        centre = win.view.mapFromScene(item.sceneBoundingRect().center())
        click(vp, centre)
        app.processEvents()
    ok(page_counts(win)[0] == before,
       f"橡皮擦不碰字体框/图片/线段（{before} 个对象一个没少）")
    ok(box_item.scene() is scene and image_item.scene() is scene
       and line_item.scene() is scene, "这三类对象仍然都在场景里")

    # 橡皮擦该干的活照旧：手绘笔迹照样能擦断
    win.set_active_tool("pen")
    drag(vp, QPoint(200, 620), [QPoint(300, 640), QPoint(400, 650),
                                QPoint(500, 655), QPoint(600, 660)])
    app.processEvents()
    ink = [it for it in scene.items() if isinstance(it, StrokeItem)]
    ok(len(ink) == 1, "先画一条笔迹备用")
    ink_points = ink[0].points()
    mid = ink[0].mapToScene(ink_points[len(ink_points) // 2])
    win.set_active_tool("eraser")
    click(vp, win.view.mapFromScene(mid))
    app.processEvents()
    fragments = [it for it in scene.items() if isinstance(it, StrokeItem)]
    ok(len(fragments) == 2 and all(it.isSelected() is False for it in fragments),
       f"橡皮擦仍然能把笔迹擦断（1 -> {len(fragments)} 段）")
    ok(page_counts(win)[0] == before + 1 + 1,
       "擦断后对象数 = 原对象 + 两段笔迹（其余对象都没被动过）")

    # ---- 14c. 工具栏「形状」下拉按钮：用形状时高亮（和其它工具按钮一致）
    win.set_active_tool("pen")
    app.processEvents()
    ok(win.shape_button.isCheckable() and not win.shape_button.isChecked(),
       "用画笔时形状按钮不高亮")
    for kind in ("rect", "diamond", "dashed_arrow"):
        win.set_active_tool(kind)
        app.processEvents()
        ok(win.shape_button.isChecked(),
           f"用「{kind}」时形状按钮高亮（和工具栏其它工具按钮一样）")
    win.set_active_tool("text")
    app.processEvents()
    ok(not win.shape_button.isChecked() and win.tool_actions["text"].isChecked(),
       "换回文字工具后高亮回到文字按钮上")
    win.set_active_tool("rect")
    app.processEvents()
    ok(win.shape_button.isChecked()
       and not win.tool_actions["text"].isChecked(),
       "再选回形状时高亮又回到形状按钮上")

    # 光 isChecked 为真还不够：按钮得**真的画出**选中底色（像素级，与主题无关）
    def highlight_count(button, reference=None):
        """按钮矩形里出现的颜色统计；给参考色时返回该颜色的像素数。

        参考色从「画笔按钮选中时的底色」里取，所以深浅主题都适用。
        """
        shot = win.grab().toImage()
        origin = button.mapTo(win, QPoint(0, 0))
        box = QRect(origin, button.size())
        counts = {}
        for y in range(box.top(), box.bottom() + 1):
            for x in range(box.left(), box.right() + 1):
                if not (0 <= x < shot.width() and 0 <= y < shot.height()):
                    continue
                name = shot.pixelColor(x, y).name()
                counts[name] = counts.get(name, 0) + 1
        if reference is None:
            top = max(counts, key=counts.get) if counts else None
            return counts, top
        return counts.get(reference, 0), reference

    bar = win.shape_button.parentWidget()
    pen_button = bar.widgetForAction(win.tool_actions["pen"])
    win.set_active_tool("pen")
    app.processEvents()
    pen_counts, highlight = highlight_count(pen_button)
    ok(highlight is not None and pen_counts.get(highlight, 0) > 100,
       f"画笔按钮选中时确实有底色（{highlight}，{pen_counts.get(highlight, 0)} 像素）")
    idle, _ = highlight_count(win.shape_button, highlight)
    ok(idle == 0, "用画笔时形状按钮完全没有高亮底色")
    win.set_active_tool("rect")
    app.processEvents()
    lit, _ = highlight_count(win.shape_button, highlight)
    pen_idle, _ = highlight_count(pen_button, highlight)
    ok(lit > 100, f"用矩形时形状按钮画出了同一套高亮底色（{lit} 像素）")
    ok(pen_idle == 0, "用矩形时画笔按钮的高亮底色已经消失")

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

    # ---- 16b. 空字体框的占位虚线框：画在视口叠加层，不进 .wbd / 不进导出的 PNG
    scene = fresh_scene()
    app.processEvents()
    base_shot = win.view.viewport().grab().toImage()      # 没有字体框时的画布
    win.set_active_tool("text")
    drag(vp, QPoint(320, 260), [QPoint(430, 300), QPoint(560, 340)])
    app.processEvents()
    empty_box = [it for it in scene.items() if isinstance(it, TextItem)][0]
    win.set_active_tool("selector")
    scene.clearSelection()
    app.processEvents()
    ok(empty_box.is_box() and empty_box.is_empty(), "新建的字体框是空的且是「框」形态")

    box_view = win.view.mapFromScene(empty_box.resize_rect()).boundingRect()
    shot = win.view.viewport().grab().toImage()

    def _differs(x, y) -> bool:
        """两次渲染的同一像素是否不同（用来单独挑出占位框画了什么）。"""
        if not (0 <= x < shot.width() and 0 <= y < shot.height()):
            return False
        before, after = base_shot.pixelColor(x, y), shot.pixelColor(x, y)
        return (abs(before.red() - after.red()) > 8
                or abs(before.green() - after.green()) > 8
                or abs(before.blue() - after.blue()) > 8)

    frame_hits = 0
    for x in range(box_view.left(), box_view.right() + 1):
        for y in range(box_view.top() - 3, box_view.top() + 4):
            frame_hits += 1 if _differs(x, y) else 0
        for y in range(box_view.bottom() - 3, box_view.bottom() + 4):
            frame_hits += 1 if _differs(x, y) else 0
    ok(frame_hits >= 20, f"空字体框画出了虚线占位框（识别到 {frame_hits} 个虚线段像素）")
    gaps = sum(1 for x in range(box_view.left(), box_view.right() + 1)
               if not _differs(x, box_view.top()))
    ok(gaps > 0, f"占位框是虚线（上边有 {gaps} 个空白缺口）")

    center = box_view.center()
    inner_hits = 0
    for x in range(center.x() - 20, center.x() + 21):
        for y in range(center.y() - 12, center.y() + 13):
            inner_hits += 1 if _differs(x, y) else 0
    ok(inner_hits == 0, "占位框只画边框，框内是干净的（不会挡住后面的内容）")

    # 占位框是"叠加层"：导出 PNG 里一点痕迹都不该有
    png_empty = os.path.join(_tmp, "empty_box.png")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (png_empty, ""))
    win.export_png()
    empty_image = QImage(png_empty)
    png_background = empty_image.pixelColor(2, 2)
    stray = 0
    for y in range(0, empty_image.height(), 2):
        for x in range(0, empty_image.width(), 2):
            color = empty_image.pixelColor(x, y)
            if (abs(color.red() - png_background.red()) > 24
                    or abs(color.green() - png_background.green()) > 24
                    or abs(color.blue() - png_background.blue()) > 24):
                stray += 1
    ok(stray == 0, f"空字体框的占位框不会被导出到 PNG（杂质像素 {stray}）")
    ok(all(it.to_dict()["type"] != "rect" for it in scene.items()),
       "占位框不是场景里的图形项（不会进 .wbd）")

    # ---- 16c. 侧边栏改文字参数：字体 / 字号 / 颜色 / 粗斜体 / 对齐 + 双击进输入
    scene = fresh_scene()
    win.set_active_tool("text")
    drag(vp, QPoint(300, 240), [QPoint(420, 280), QPoint(540, 320)])
    app.processEvents()
    texts = [it for it in scene.items() if isinstance(it, TextItem)]
    ok(len(texts) == 1 and texts[0].is_box(), "文字工具拖出的是字体框")
    text_item = texts[0]
    panel = win.property_panel
    ok(panel.isVisible(), "选中字体框后参数侧边栏自动出现")
    ok(not panel.text_group.isHidden() and panel.outline_group.isHidden(),
       "字体框只显示「文字参数」")

    # 双击 -> 光标进内容框（"双击才输入文字"）
    win.set_active_tool("selector")
    scene.clearSelection()
    win.property_panel.hide()
    inside = win.view.mapFromScene(text_item.scenePos() + QPointF(40, 20))
    double_click(vp, inside)
    app.processEvents()
    ok(panel.isVisible() and panel.text_edit.hasFocus(),
       "双击字体框后参数栏弹出且内容框获得焦点")
    ok(text_item.isSelected(), "双击后该字体框被选中")

    # 在侧边栏里改参数：每改一项都实时落到图形项上
    panel.text_edit.setPlainText("自动换行\n的文字")
    panel.size_spin.setValue(28)
    panel.bold_button.setChecked(True)
    panel.color_button.set_color(QColor("#c0392b"))
    panel.align_combo.setCurrentIndex(panel.align_combo.findData("center"))
    app.processEvents()
    ok(text_item.toPlainText() == "自动换行\n的文字", "内容实时写进字体框")
    ok(text_item.font().pixelSize() == 28 and text_item.font().bold(),
       "字号与粗体实时生效")
    ok(text_item.defaultTextColor().name() == "#c0392b", "颜色实时生效")
    ok(text_item.text_align() == "center", "对齐实时生效")
    clone = TextItem.from_dict(text_item.to_dict())
    ok(clone.to_dict() == text_item.to_dict(),
       "文字排版可无损序列化（内容/字体/字号/颜色/对齐/框尺寸）")

    # 连续调节只压成一条撤销命令（否则拖滑块会塞满撤销栈）
    steps_before = win.undo_stack.index()
    win._commit_style_changes()
    ok(win.undo_stack.index() == steps_before + 1, "一串参数改动 = 一个撤销步骤")
    win.undo_stack.undo()
    app.processEvents()
    ok(text_item.toPlainText() == "" and text_item.font().pixelSize() != 28,
       "参数改动可整体撤销（内容与排版一起回退）")
    win.undo_stack.redo()
    app.processEvents()
    ok(text_item.toPlainText() == "自动换行\n的文字", "重做恢复参数改动")

    # 工具栏改颜色 / 粗细也会作用到选中对象（和侧边栏一致）
    win.color_picker.set_color(QColor("#1a4fb4"))
    app.processEvents()
    win._commit_style_changes()
    ok(text_item.defaultTextColor().name() == "#1a4fb4",
       "工具栏颜色也作用到选中对象")

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

    # ---- 18. 图片可自由伸缩（拖手柄 + 撤销）
    scene = win.view.scene()
    images = [it for it in scene.items() if isinstance(it, ImageItem)]
    ok(bool(images), "场景里有导入的图片")
    image = images[0]
    scene.clearSelection()
    win.set_active_tool("selector")
    image.setSelected(True)
    app.processEvents()
    ok(win.view.resize_target() is image, "选中图片后出现缩放手柄")
    handles = win.view.handle_points_view()
    ok(len(handles) == 8, f"图片有 8 个缩放手柄（实际 {len(handles)}）")
    rect_before = image.resize_rect()
    corner = handles[4]                       # 右下手柄
    drag(vp, QPoint(int(corner.x()), int(corner.y())),
         [QPoint(int(corner.x()) + 40, int(corner.y()) + 20),
          QPoint(int(corner.x()) + 80, int(corner.y()) + 40)])
    app.processEvents()
    rect_after = image.resize_rect()
    ok(rect_after.width() > rect_before.width() + 20,
       f"拖右下手柄把图片拉大了（{rect_before.width():.0f} -> "
       f"{rect_after.width():.0f}）")
    ok(abs(rect_after.left() - rect_before.left()) < 1.5,
       "四角缩放时对角（左上）保持不动")
    win.undo_stack.undo()
    app.processEvents()
    ok(abs(image.resize_rect().width() - rect_before.width()) < 0.5,
       "图片缩放可撤销")

    # ---- 18b. 字体框自由拉伸：改的是框，不是字号（默认不等比）
    scene = fresh_scene()
    win.set_active_tool("text")
    win.property_panel.size_spin.setValue(20)
    drag(vp, QPoint(360, 260), [QPoint(440, 300), QPoint(520, 340)])
    app.processEvents()
    text_item = [it for it in scene.items() if isinstance(it, TextItem)][0]
    win.set_active_tool("selector")
    scene.clearSelection()
    text_item.setSelected(True)
    app.processEvents()
    ok(win.view.resize_target() is text_item, "选中字体框后出现缩放手柄")
    font_before = text_item.font().pixelSize()
    box_before = text_item.resize_rect()
    ok(box_before.width() > 100 and box_before.height() > 40,
       f"字体框有明确尺寸（{box_before.width():.0f}x{box_before.height():.0f}）")
    corner = win.view.handle_points_view()[4]
    drag(vp, QPoint(int(corner.x()), int(corner.y())),
         [QPoint(int(corner.x()) + 120, int(corner.y()) + 40)])
    app.processEvents()
    box_after = text_item.resize_rect()
    ok(box_after.width() > box_before.width() + 80,
       f"拖右下手柄把框拉宽（{box_before.width():.0f} -> {box_after.width():.0f}）")
    ok(text_item.font().pixelSize() == font_before,
       "拉框不改字号（字体框默认非等比缩放）")
    ok(box_after.height() / max(box_after.width(), 1e-6)
       != box_before.height() / max(box_before.width(), 1e-6),
       "宽高不再保持原比例（自由拉伸）")
    win.undo_stack.undo()
    app.processEvents()
    ok(abs(text_item.resize_rect().width() - box_before.width()) < 0.5,
       "字体框拉伸可撤销")

    # ---- 18c. 形状/笔迹/文字/图片都能自由伸缩（拖手柄）
    def drag_handle(item, handle_index, delta: QPoint):
        """选中 item 后拖动它的某个手柄。"""
        scene.clearSelection()
        item.setSelected(True)
        app.processEvents()
        points = win.view.handle_points_view()
        corner = points[handle_index]
        drag(vp, QPoint(int(corner.x()), int(corner.y())),
             [QPoint(int(corner.x()) + delta.x(), int(corner.y()) + delta.y())])
        app.processEvents()

    # 矩形：改的是几何（线宽不变）
    scene = fresh_scene()
    win.set_active_tool("rect")
    drag(vp, QPoint(200, 200), [QPoint(300, 260)])
    app.processEvents()
    win.set_active_tool("selector")
    shape = [it for it in scene.items() if isinstance(it, RectItem)][0]
    scene.clearSelection()
    shape.setSelected(True)
    app.processEvents()
    ok(win.view.resize_target() is shape, "矩形也能自由伸缩（出现手柄）")
    shape_before = shape.resize_rect()
    pen_before = shape.pen().widthF()
    drag_handle(shape, 4, QPoint(80, 60))
    shape_after = shape.resize_rect()
    ok(shape_after.width() > shape_before.width() + 20
       and shape_after.height() > shape_before.height() + 20,
       f"拖手柄把矩形拉大（{shape_before.width():.0f}x{shape_before.height():.0f} -> "
       f"{shape_after.width():.0f}x{shape_after.height():.0f}）")
    ok(abs(shape.pen().widthF() - pen_before) < 0.01, "缩放矩形时线宽保持不变")
    win.undo_stack.undo()
    app.processEvents()
    ok(abs(shape.resize_rect().width() - shape_before.width()) < 0.5, "形状缩放可撤销")

    # 画笔笔迹：改的是 transform（线宽跟着一起放大）
    scene = fresh_scene()
    win.set_active_tool("pen")
    drag(vp, QPoint(200, 200), [QPoint(240, 220), QPoint(300, 250)])
    app.processEvents()
    win.set_active_tool("selector")
    stroke_item = [it for it in scene.items() if isinstance(it, StrokeItem)][0]
    scene.clearSelection()
    stroke_item.setSelected(True)
    app.processEvents()
    ok(win.view.resize_target() is stroke_item, "画笔画的笔迹也能自由伸缩")
    stroke_before = stroke_item.resize_rect()
    drag_handle(stroke_item, 4, QPoint(100, 70))
    stroke_after = stroke_item.resize_rect()
    ok(stroke_after.width() > stroke_before.width() + 20,
       f"拖手柄把笔迹拉大（{stroke_before.width():.0f} -> {stroke_after.width():.0f}）")
    ok(stroke_item.scene_scale() > 1.2,
       f"笔迹缩放倍数生效（{stroke_item.scene_scale():.2f}）")
    win.undo_stack.undo()
    app.processEvents()
    ok(abs(stroke_item.scene_scale() - 1.0) < 0.01, "笔迹缩放可撤销")

    # ---- 18d. 手柄真的画出来了（叠加层绘制路径）
    scene = fresh_scene()
    img_path2 = os.path.join(_tmp, "resize_sample.png")
    sample2 = QImage(80, 60, QImage.Format.Format_ARGB32)
    sample2.fill(QColor("#2f9e6f"))
    sample2.save(img_path2)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (img_path2, ""))
    win.import_image()
    app.processEvents()
    resized_image = [it for it in scene.items() if isinstance(it, ImageItem)][0]
    scene.clearSelection()
    resized_image.setSelected(True)
    app.processEvents()
    shot = win.view.viewport().grab().toImage()
    drawn = 0
    for handle in win.view.handle_points_view():
        found = False
        for dx in range(-5, 6):
            for dy in range(-5, 6):
                x, y = int(handle.x()) + dx, int(handle.y()) + dy
                if not (0 <= x < shot.width() and 0 <= y < shot.height()):
                    continue
                color = shot.pixelColor(x, y)
                # 手柄边框是蓝色（#2684ff 附近），白底画布上没有这种颜色
                if color.blue() > 150 and color.blue() - color.red() > 60:
                    found = True
                    break
            if found:
                break
        drawn += 1 if found else 0
    ok(drawn >= 6, f"缩放手柄真的画在视口上（识别到 {drawn}/8 个）")

    # ---- 19. 拖拽导入：图片 -> 落在鼠标位置；.wbd -> 追加为新页面
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    def drop_files(paths, pos=QPoint(500, 400), target=None):
        """模拟把文件拖进窗口，返回 (dragEnter 是否接收, drop 是否完成)。

        默认发给 ``win.view``（画布）：**真实拖拽就是这样**——Qt 只会把拖放事件
        发给光标下那个接收拖放的控件，而 QGraphicsView 的 acceptDrops 默认是
        True，事件不会再上传给主窗口。曾经把测试写成直接发给主窗口，
        结果真实使用完全无效（测试假通过）。
        """
        target = target if target is not None else win.view
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        enter = QDragEnterEvent(pos, Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, enter)
        drop = QDropEvent(QPointF(pos), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, drop)
        app.processEvents()
        return enter.isAccepted(), drop.isAccepted()

    def drop_point_to_scene(target, pos):
        """把落点换算成场景坐标：发给视图时事件坐标是视口坐标，
        发给主窗口时是窗口坐标（差一个工具栏的高度）。"""
        if target is win:
            return win.view.mapToScene(win.view.mapFrom(win, pos))
        return win.view.mapToScene(pos)

    ok(win.view.acceptDrops(), "画布控件本身接收拖放（拖拽事件的真正入口）")

    scene = fresh_scene()
    drop_target = QPoint(430, 360)
    drop_image = os.path.join(_tmp, "dropped.png")
    canvas_image = QImage(60, 40, QImage.Format.Format_ARGB32)
    canvas_image.fill(QColor("#7a4fd0"))
    canvas_image.save(drop_image)
    entered, dropped = drop_files([drop_image], drop_target)
    ok(entered and dropped, "拖动图片进窗口被接受")
    dropped_items = [it for it in scene.items() if isinstance(it, ImageItem)]
    ok(len(dropped_items) == 1, "拖入的图片被导入到当前页")
    # 落点应该就在鼠标位置附近（宽高 60x40，以落点为中心）
    expected = drop_point_to_scene(win.view, drop_target)
    center = dropped_items[0].scenePos() + QPointF(30.0, 20.0)
    ok(abs(center.x() - expected.x()) < 2.0 and abs(center.y() - expected.y()) < 2.0,
       f"图片落在鼠标位置（落点 {expected.x():.0f},{expected.y():.0f}，"
       f"图片中心 {center.x():.0f},{center.y():.0f}）")
    ok(dropped_items[0].isSelected(), "拖入的图片自动选中，可以直接拖动/缩放")

    # .wbd 拖进窗口 = 追加为新页面（不是替换当前文档）

    source_doc = os.path.join(_tmp, "dragged_doc.wbd")
    source_pages = [BoardPage("来源页 1"), BoardPage("来源页 2")]
    source_pages[0].scene.addItem(
        RectItem(QRectF(0.0, 0.0, 60.0, 40.0), QColor(0, 0, 0), 2.0))
    fh_module.save_document(source_doc, source_pages, 0)

    pages_before = len(win.pages)
    items_before = page_counts(win)[0]
    entered, dropped = drop_files([source_doc])
    ok(entered and dropped, "拖动 .wbd 文件进窗口被接受")
    ok(len(win.pages) == pages_before + 2,
       f"追加为新页面（{pages_before} -> {len(win.pages)}）")
    ok(page_counts(win)[0] == items_before, "原来的页面内容没被替换")
    ok(win.page_index == pages_before, "视图已切到刚追加的第一页")
    ok("dragged_doc.wbd" in win.current_page.name,
       f"新页面名字带来源文件名（{win.current_page.name}）")
    ok(win.current_page.item_count() == 1, "新页面上带着来源文件里的内容")
    ok("*" in win.windowTitle(), "追加页面后文档被标记为有未保存改动")

    # 一次拖多个文件（图片 + .wbd）与不支持的类型
    fresh_scene()
    win.pages = [win.pages[win.page_index]]     # 只留当前页，方便数数量
    win.page_index = 0
    win._document_modified = False
    entered, dropped = drop_files([drop_image, source_doc])
    ok(entered and dropped and len(win.pages) == 3,
       f"一次拖入图片 + .wbd 都能处理（现在 {len(win.pages)} 页）")
    txt_path = os.path.join(_tmp, "not_supported.txt")
    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write("不是白板也不是图片")
    entered, _dropped = drop_files([txt_path])
    ok(not entered, "不支持的文件类型不会被接受（不弹错误）")

    # 工具栏/状态栏等非画布区域：事件走主窗口自己的处理器
    scene = fresh_scene()
    entered, dropped = drop_files([drop_image], drop_target, target=win)
    ok(entered and dropped, "拖到非画布区域（工具栏/状态栏）也能导入")
    ok(len([it for it in scene.items() if isinstance(it, ImageItem)]) == 1,
       "主窗口路径同样把图片导入当前页")

    # 真实路径：Qt 把拖放投递给光标下最深那个接收拖放的控件，也就是 viewport
    scene = fresh_scene()
    entered, dropped = drop_files([drop_image], drop_target,
                                  target=win.view.viewport())
    ok(entered and dropped, "拖到画布视口（Qt 实际投递的控件）能导入")
    ok(len([it for it in scene.items() if isinstance(it, ImageItem)]) == 1,
       "视口路径把图片导入当前页")

    # ---- 20. 组合（Ctrl+G）：整体选中 / 移动 / 自由缩放 / 存读 / 拆开

    scene = fresh_scene()
    win.set_active_tool("rect")
    drag(vp, QPoint(200, 200), [QPoint(300, 260)])
    app.processEvents()
    win.set_active_tool("star")
    drag(vp, QPoint(360, 200), [QPoint(440, 270)])
    app.processEvents()
    win.set_active_tool("selector")
    pieces = [it for it in scene.items()
              if isinstance(it, (RectItem, PolygonShapeItem))]
    ok(len(pieces) == 2, "准备好两个图形用于组合")
    ok(win._edit_menu_actions["group"].shortcut().toString() == "Ctrl+G",
       "「组合」绑定在 Ctrl+G 上")
    ok(win._edit_menu_actions["ungroup"].shortcut().toString() == "Ctrl+Shift+G",
       "「取消组合」绑定在 Ctrl+Shift+G 上")

    scene.clearSelection()
    for piece in pieces:
        piece.setSelected(True)
    app.processEvents()
    ok(win._edit_menu_actions["group"].isEnabled(), "选中两个对象后「组合」可用")
    win.group_selected()
    app.processEvents()
    groups = [it for it in scene.items() if isinstance(it, GroupItem)]
    ok(len(groups) == 1, "Ctrl+G 生成了一个组合")
    group = groups[0]
    ok(len(group.children_items()) == 2, "组合里有 2 个成员")
    ok(all(piece.parentItem() is group for piece in pieces), "两个图形都挂到了组合上")
    ok(group.isSelected(), "组合后自动选中整个组合")
    ok(win.view.resize_target() is group, "组合像常规图形一样可以自由伸缩")

    # 组合的成员不能再被单独点到（命中测试返回的是组合本身）
    probe = pieces[0].sceneBoundingRect().center()
    hit = scene.topmost_item_at(probe)
    ok(hit is group, f"点击成员命中的是整个组合（{type(hit).__name__}）")

    # 移动整体：两个成员一起动，且只产生一个撤销步骤
    positions_before = [piece.scenePos() for piece in pieces]
    steps_before = win.undo_stack.index()
    origin = group.boundingRect().center()
    drag(vp, win.view.mapFromScene(origin),
         [QPoint(int(win.view.mapFromScene(origin).x()) + 60,
                 int(win.view.mapFromScene(origin).y()) + 40)])
    app.processEvents()
    moved = [piece.scenePos() - before
             for piece, before in zip(pieces, positions_before)]
    ok(all(abs(delta.x() - moved[0].x()) < 0.5 and abs(delta.y() - moved[0].y()) < 0.5
           for delta in moved) and abs(moved[0].x()) > 10,
       f"拖动组合时成员一起移动（位移 {moved[0].x():.0f},{moved[0].y():.0f}）")
    ok(win.undo_stack.index() == steps_before + 1, "整体移动只产生一个撤销步骤")
    win.undo_stack.undo()
    app.processEvents()

    # 缩放整体：成员跟着变大
    before_rect = group.resize_rect()
    drag_handle(group, 4, QPoint(120, 90))
    after_rect = group.resize_rect()
    ok(after_rect.width() > before_rect.width() + 20,
       f"拖动组合的手柄整体缩放（{before_rect.width():.0f} -> {after_rect.width():.0f}）")
    ok(group.scene_scale() > 1.1, "组合缩放倍数生效")

    # 存盘再读回来：组合要完整保留
    doc = serializer.document_to_dict(win.pages, win.page_index)
    pages2, cur2 = serializer.document_from_dict(doc)
    reloaded = [it for it in pages2[cur2].scene.items()
                if isinstance(it, GroupItem)]
    ok(len(reloaded) == 1 and len(reloaded[0].children_items()) == 2,
       f"组合能完整保存/读取（组合数 {len(reloaded)}，"
       f"成员数 {len(reloaded[0].children_items()) if reloaded else '-'}）")
    ok(abs(reloaded[0].scene_scale() - group.scene_scale()) < 0.01,
       "读回来的组合缩放倍数一致")

    # 取消组合：成员回到场景顶层，外观不变，且可撤销
    scene.clearSelection()
    group.setSelected(True)
    app.processEvents()
    ok(win._edit_menu_actions["ungroup"].isEnabled(), "选中组合后「取消组合」可用")
    members_before = [(piece, piece.scenePos(), piece.sceneBoundingRect())
                      for piece in group.children_items()]
    win.ungroup_selected()
    app.processEvents()
    ok(not [it for it in scene.items() if isinstance(it, GroupItem)],
       "Ctrl+Shift+G 拆开了组合")
    ok(all(piece.parentItem() is None and piece.scene() is scene
           for piece, _pos, _rect in members_before),
       "拆开后成员回到场景里（不再有父项）")
    for piece, pos_before, rect_before in members_before:
        now = piece.sceneBoundingRect()
        ok(abs(now.x() - rect_before.x()) < 1.0 and abs(now.width() - rect_before.width()) < 1.0,
           f"拆开后成员外观不变（{rect_before.width():.0f} -> {now.width():.0f}）")
    win.undo_stack.undo()
    app.processEvents()
    ok(len([it for it in scene.items() if isinstance(it, GroupItem)]) == 1,
       "取消组合可以撤销（组合回来了）")

    # ---- 20b. 真实按键也能触发组合/取消组合（走 Qt 的快捷键机制）
    from PySide6.QtTest import QTest

    scene = fresh_scene()
    win.set_active_tool("rect")
    drag(vp, QPoint(200, 200), [QPoint(280, 250)])
    drag(vp, QPoint(340, 200), [QPoint(420, 250)])
    app.processEvents()
    win.set_active_tool("selector")
    scene.clearSelection()
    for piece in [it for it in scene.items() if isinstance(it, RectItem)]:
        piece.setSelected(True)
    app.processEvents()
    QTest.keyClick(win, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    ok(len([it for it in scene.items() if isinstance(it, GroupItem)]) == 1,
       "按真实 Ctrl+G 键即可组合")
    QTest.keyClick(win, Qt.Key.Key_G,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    app.processEvents()
    ok(not [it for it in scene.items() if isinstance(it, GroupItem)],
       "按真实 Ctrl+Shift+G 键即可取消组合")

    # ---- 21. 页面越界/为空时撤销栈不能抛异常（曾经在 Qt 槽里抛 IndexError，
    #          异常只会打到 stderr，界面刷新会静默中断）
    saved_pages, saved_index = win.pages, win.page_index
    failure = None
    fallback = None
    try:
        win.pages = []
        win.page_index = 5
        win._on_history_changed()
        fallback = win.undo_stack
    except Exception as exc:  # noqa: BLE001
        failure = exc
    finally:
        win.pages, win.page_index = saved_pages, saved_index
    ok(failure is None and fallback is not None,
       f"页面为空/越界时撤销栈不抛异常（{failure!r}）")

    # ---- 22. 图形内部写文字（含字体数据），在参数窗口里查看与修改
    scene = fresh_scene()
    win.set_active_tool("rect")
    drag(vp, QPoint(300, 240), [QPoint(460, 300), QPoint(620, 380)])
    app.processEvents()
    win.set_active_tool("selector")
    shape = [it for it in scene.items() if isinstance(it, RectItem)][0]
    scene.clearSelection()
    shape.setSelected(True)
    app.processEvents()
    panel = win.property_panel
    ok(panel.title_label.text().startswith("图形"), "选中图形后侧边栏显示「图形」")
    ok(not panel.text_group.isHidden(), "图形也能看到「文字参数」（写内部文字）")
    ok(panel.text_edit.toPlainText() == "", "还没有内部文字时内容框是空的")

    panel.text_edit.setPlainText("图形里的标题")
    panel.size_spin.setValue(24)
    panel.bold_button.setChecked(True)
    panel.text_color_button.set_color(QColor("#00695c"))
    panel.align_combo.setCurrentIndex(panel.align_combo.findData("center"))
    app.processEvents()
    win._commit_style_changes()
    ok(shape.label_text() == "图形里的标题", "文字写进了图形内部")
    label = shape.raw_label()
    ok(label["pixel_size"] == 24 and label["bold"] is True
       and qcolor_from_rgba(label["color"]).name() == "#00695c"
       and label["align"] == "center",
       "字体数据（字号/粗体/颜色/对齐）一起存进图形")

    # 重新选中时参数窗口要把这些值显示出来（"可以在参数窗口中查看"）
    scene.clearSelection()
    app.processEvents()
    ok(panel.text_edit.toPlainText() == "", "取消选中后内容框清空")
    shape.setSelected(True)
    app.processEvents()
    ok(panel.text_edit.toPlainText() == "图形里的标题"
       and panel.size_spin.value() == 24
       and panel.bold_button.isChecked()
       and panel.align_combo.currentData() == "center",
       "重新选中图形时参数窗口回显内部文字与字体设置")

    # 双击图形 -> 参数栏聚焦到内容框（方便直接改字）
    win.set_active_tool("selector")
    inside = win.view.mapFromScene(shape.sceneBoundingRect().center())
    double_click(vp, inside)
    app.processEvents()
    ok(panel.text_edit.hasFocus(), "双击图形后内容框获得焦点")

    # v1.3.0：图形默认不填充，"点图形中间"也要能选中/拖动它
    scene.clearSelection()
    app.processEvents()
    click(vp, inside)
    app.processEvents()
    ok(shape.isSelected(), "鼠标点图形中间就能选中它")
    count_before = page_counts(win)[0]
    moved = shape.pos()
    drag(vp, inside, [QPoint(inside.x() + 20, inside.y() + 12),
                      QPoint(inside.x() + 40, inside.y() + 24)])
    app.processEvents()
    ok(shape.pos() != moved, "按住图形中间可以拖动它（位移 %s）"
       % (shape.pos() - moved,))

    # 橡皮擦仍按描边判定：点图形中间不会误删整个图形
    win.set_active_tool("eraser")
    click(vp, win.view.mapFromScene(shape.sceneBoundingRect().center()))
    app.processEvents()
    ok(page_counts(win)[0] == count_before, "橡皮擦点图形中间不会误删整个图形")
    win.set_active_tool("selector")
    shape.setSelected(True)
    app.processEvents()

    # ---- 22b. 侧边栏改图形参数（颜色/线宽/线型/填充）即时生效 + 一个撤销步骤
    ok(not panel.outline_group.isHidden() and panel.title_label.text().startswith("图形"),
       "选中图形后侧边栏显示「图形参数」")
    shape.set_fill_color(None)
    win._refresh_property_panel()
    app.processEvents()
    ok(panel.color_button.color().name() == shape.pen().color().name()
       and panel.width_slider.value() == int(round(shape.pen().widthF())),
       "侧边栏回显了图形当前的描边颜色与线宽")
    ok(not panel.fill_check.isChecked() and not panel.fill_button.isEnabled(),
       "没有填充时「填充」开关是关的、颜色按钮不可点")

    steps = win.undo_stack.index()
    panel.color_button.set_color(QColor("#8e44ad"))
    panel.width_slider.setValue(6)
    panel.style_combo.setCurrentIndex(panel.style_combo.findData("dot"))
    app.processEvents()
    ok(shape.pen().color().name() == "#8e44ad", "侧边栏改颜色实时落到图形上")
    ok(abs(shape.pen().widthF() - 6.0) < 0.01, "侧边栏改线宽实时落到图形上")
    ok(shape.current_line_style() == "dot", "侧边栏改线型实时落到图形上")
    win._commit_style_changes()
    ok(win.undo_stack.index() == steps + 1, "图形参数串改也只占一个撤销步骤")
    win.undo_stack.undo()
    app.processEvents()
    ok(shape.pen().color().name() != "#8e44ad"
       and shape.current_line_style() != "dot",
       "图形参数改动可以整体撤销")

    # 填充：勾上 -> 有填充色；取消勾选 -> 回到无填充（None 不能被过滤掉）
    panel.fill_check.setChecked(True)
    app.processEvents()
    ok(shape.fill_color() is not None, "勾选「填充」后图形有了填充色")
    panel.fill_button.set_color(QColor("#fff3cd"))
    app.processEvents()
    win._commit_style_changes()
    ok(shape.fill_color() is not None
       and shape.fill_color().name() == "#fff3cd", "填充颜色实时生效")
    panel.fill_check.setChecked(False)
    app.processEvents()
    win._commit_style_changes()
    ok(shape.fill_color() is None, "取消「填充」真的取消了（None 没被过滤掉）")

    # 图形与标签一起存进 .wbd
    from persistence import serializer as serializer_mod
    doc = serializer_mod.document_to_dict(win.pages, win.page_index)
    pages2, cur2 = serializer_mod.document_from_dict(doc)
    reloaded = [it for it in pages2[cur2].scene.items() if isinstance(it, RectItem)]
    ok(len(reloaded) == 1 and reloaded[0].label_text() == "图形里的标题",
       "图形的内部文字能完整保存/读取")
    ok(reloaded[0].raw_label()["pixel_size"] == 24,
       "读回来的字体数据一致")

    print(f"\n全部 {checks} 项冒烟检查通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(str(exc))
        sys.exit(1)
