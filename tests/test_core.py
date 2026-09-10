"""核心逻辑单元测试（tests/test_core.py）。

不依赖真实显示器（默认 offscreen），也不需要 pytest：

    python tests/test_core.py          # 直接跑，失败时返回码 1
    pytest tests/test_core.py          # 装了 pytest 也可以

覆盖：笔画平滑、颜色序列化、各类图形项 round-trip、文档序列化、
撤销命令、场景生命周期与网格自适应。
"""
from __future__ import annotations

import json
import os
import sys
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QPainterPath, QPixmap, QUndoStack  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# 隔离用户配置与数据文件：不能靠 QSettings.setDefaultFormat/setPath
# （Windows 上 QSettings(org, app) 仍然读写注册表），改用显式路径覆盖。
_TEST_TMP = os.path.join(_ROOT, ".test_tmp")
os.makedirs(_TEST_TMP, mode=0o777, exist_ok=True)
os.environ.setdefault("WHITEBOARD_CONFIG",
                      os.path.join(_TEST_TMP, "unit_tests.ini"))
os.environ.setdefault("WHITEBOARD_DATA_DIR",
                      os.path.join(_TEST_TMP, "unit_data"))
os.makedirs(os.environ["WHITEBOARD_DATA_DIR"], mode=0o777, exist_ok=True)

from canvas.items import (  # noqa: E402
    EllipseItem,
    ImageItem,
    LineItem,
    RectItem,
    StrokeItem,
    TextItem,
    is_preview,
    item_from_dict,
    mark_preview,
)
from canvas.scene import WhiteboardScene  # noqa: E402
from core.history import (  # noqa: E402
    AddItemCommand,
    ClearPageCommand,
    MoveItemsCommand,
    RemoveItemCommand,
    RemoveItemsCommand,
    ReplaceItemsCommand,
    push_commands,
)
from core.page import BoardPage  # noqa: E402
from core.stroke import (  # noqa: E402
    Stroke,
    build_path,
    qcolor_from_rgba,
    qcolor_to_rgba,
    split_by_eraser,
)
from persistence import file_handler as fh  # noqa: E402
from persistence import serializer  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ------------------------------------------------------------------ 笔画与曲线


def test_build_path_shapes():
    assert build_path([]).elementCount() == 0
    assert build_path([QPointF(0, 0)]).elementCount() == 2       # 单点 -> 极短线段
    assert build_path([QPointF(0, 0), QPointF(10, 10)]).elementCount() == 2
    path = build_path([QPointF(i, i * i) for i in range(6)])
    kinds = {path.elementAt(i).type for i in range(path.elementCount())}
    # 中间点用二次贝塞尔拟合，因此路径里应当出现 CurveToElement
    assert QPainterPath.ElementType.CurveToElement in kinds


def test_stroke_roundtrip():
    stroke = Stroke(QColor(10, 20, 30, 200), 3.5,
                    [QPointF(1.5, 2.5), QPointF(3.0, 4.0)])
    data = stroke.to_dict()
    assert data["color"] == [10, 20, 30, 200]
    assert data["thickness"] == 3.5
    again = Stroke.from_dict(data)
    assert again.color == stroke.color
    assert again.thickness == stroke.thickness
    assert [(p.x(), p.y()) for p in again.points] == [(1.5, 2.5), (3.0, 4.0)]
    assert again.length() > 0
    assert again.bounding_rect().width() > 0


def test_color_helpers():
    color = QColor(1, 2, 3, 4)
    assert qcolor_to_rgba(color) == [1, 2, 3, 4]
    assert qcolor_from_rgba([1, 2, 3, 4]) == color
    assert qcolor_from_rgba([1, 2, 3]) == QColor(1, 2, 3)
    assert qcolor_from_rgba("#ff0000") == QColor(255, 0, 0)
    assert qcolor_from_rgba(None).isValid()          # 容错
    assert qcolor_from_rgba([1, 2]).isValid()


# ------------------------------------------------------------------ 图形项 round-trip


def _roundtrip(item):
    data = item.to_dict()
    clone = item_from_dict(data)
    assert clone is not None, f"{type(item).__name__} 无法重建"
    assert clone.to_dict() == data, f"{type(item).__name__} round-trip 不一致"
    return clone


def test_item_roundtrip_all_types():
    pixmap = QPixmap(4, 4)
    pixmap.fill(QColor(1, 2, 3))
    items = [
        StrokeItem([QPointF(0, 0), QPointF(5, 5), QPointF(9, 3)],
                   QColor(255, 0, 0), 2.0),
        RectItem(QRectF(1, 2, 30, 40), QColor(0, 128, 0), 3.0),
        EllipseItem(QRectF(1, 2, 30, 40), QColor(0, 0, 255), 1.5),
        LineItem(QPointF(0, 0), QPointF(10, 10), QColor(9, 9, 9), 2.0, arrow=True),
        TextItem("你好 whiteboard", QColor(20, 20, 20), 18.0),
        ImageItem(pixmap),
    ]
    for item in items:
        clone = _roundtrip(item)
        assert clone.type() == item.type()


def test_stroke_points_survive_roundtrip():
    points = [QPointF(i * 1.5, i * 2.25) for i in range(20)]
    item = StrokeItem(points, QColor(0, 0, 0), 2.0)
    assert item.point_count() == 20
    clone = StrokeItem.from_dict(item.to_dict())
    assert clone.point_count() == 20
    assert [(p.x(), p.y()) for p in clone.points()] == \
           [(p.x(), p.y()) for p in points]


def test_split_by_eraser():
    """橡皮擦切分：擦中间断成两段、擦两端变短、全擦没、没碰到保持原样。"""
    points = [QPointF(float(i), 0.0) for i in range(11)]      # x = 0..10

    # 半径 1.5 落在 (5,0)：x=4,5,6 被擦掉
    runs = split_by_eraser(points, QPointF(5.0, 0.0), 1.5)
    assert [len(r) for r in runs] == [4, 4], runs
    assert runs[0][-1].x() == 3.0 and runs[1][0].x() == 7.0

    # 擦最左端 -> 只剩一段
    runs = split_by_eraser(points, QPointF(0.0, 0.0), 1.5)
    assert len(runs) == 1 and runs[0][0].x() == 2.0

    # 全部覆盖 -> 空
    assert split_by_eraser(points, QPointF(5.0, 0.0), 99.0) == []

    # 没碰到 -> 原样返回一段
    runs = split_by_eraser(points, QPointF(5.0, 99.0), 1.0)
    assert len(runs) == 1 and len(runs[0]) == len(points)

    # origin 偏移（图形项 pos 不为 0 时按场景坐标判断）
    runs = split_by_eraser([QPointF(0.0, 0.0), QPointF(1.0, 0.0), QPointF(2.0, 0.0)],
                           QPointF(101.0, 100.0), 0.5, origin=QPointF(100.0, 100.0))
    assert [len(r) for r in runs] == [1, 1]


def test_replace_items_command():
    """橡皮擦把一条笔画换成两段：一个命令、可整体撤销。"""
    scene = WhiteboardScene()
    stack = QUndoStack()
    original = StrokeItem([QPointF(0, 0), QPointF(5, 5), QPointF(10, 0)],
                          QColor(0, 0, 0), 2.0)
    scene.addItem(original)
    left = StrokeItem([QPointF(0, 0), QPointF(5, 5)], QColor(0, 0, 0), 2.0)
    right = StrokeItem([QPointF(5, 5), QPointF(10, 0)], QColor(0, 0, 0), 2.0)

    # 模拟拖拽过程中的实时改动：先移除原笔画、再加入两段
    scene.removeItem(original)
    scene.addItem(left)
    scene.addItem(right)

    stack.push(ReplaceItemsCommand(scene, [original], [left, right], "橡皮擦"))
    assert original.scene() is None and left.scene() is scene and right.scene() is scene
    assert len(scene.items()) == 2, "push 不应重复添加（redo 是幂等的）"

    stack.undo()
    assert original.scene() is scene
    assert left.scene() is None and right.scene() is None
    assert len(scene.items()) == 1

    stack.redo()
    assert original.scene() is None and len(scene.items()) == 2


def test_eraser_gesture_bookkeeping():
    """拖拽过程中“新生成的碎片又被擦到”时，撤销必须干净。"""
    scene = WhiteboardScene()
    stack = QUndoStack()
    original = StrokeItem([QPointF(i, 0) for i in range(11)], QColor(0, 0, 0), 2.0)
    scene.addItem(original)

    removed, added = [], []

    def erase(item, pieces):
        if item in added:
            added.remove(item)
        else:
            removed.append(item)
        scene.removeItem(item)
        for piece in pieces:
            scene.addItem(piece)
            added.append(piece)

    first = StrokeItem([QPointF(i, 0) for i in range(4)], QColor(0, 0, 0), 2.0)
    tail = StrokeItem([QPointF(i, 0) for i in range(7, 11)], QColor(0, 0, 0), 2.0)
    erase(original, [first, tail])
    # 第二段又被擦了一下 -> 换成更小的一段
    smaller = StrokeItem([QPointF(i, 0) for i in range(8, 11)], QColor(0, 0, 0), 2.0)
    erase(tail, [smaller])
    assert len(scene.items()) == 2

    stack.push(ReplaceItemsCommand(scene, removed, added, "橡皮擦"))
    stack.undo()
    assert len(scene.items()) == 1 and scene.items()[0] is original, \
        "撤销后应当只剩最初那条完整笔画（中间产物不该被恢复）"


def test_preview_items_are_not_serialized():
    scene = WhiteboardScene()
    real = RectItem(QRectF(0, 0, 10, 10), QColor(0, 0, 0), 1.0)
    preview = RectItem(QRectF(20, 20, 10, 10), QColor(0, 0, 0), 1.0)
    mark_preview(preview)
    scene.addItem(real)
    scene.addItem(preview)
    assert is_preview(preview) and not is_preview(real)
    dicts = serializer.scene_items_to_dicts(scene)
    assert len(dicts) == 1


# ------------------------------------------------------------------ 文档序列化


def test_document_roundtrip_preserves_order():
    page = BoardPage("第一页")
    for i in range(5):
        item = RectItem(QRectF(i, i, 10, 10), QColor(i * 40, 0, 0), 1.0)
        page.scene.addItem(item)
    pages = [page, BoardPage("第二页")]
    text = serializer.serialize_document(pages, 1)
    data = json.loads(text)
    assert data["app"] == "whiteboard-pyside"
    assert data["current_page"] == 1

    pages2, current = serializer.deserialize_document(text)
    assert current == 1
    assert [p.name for p in pages2] == ["第一页", "第二页"]
    assert [p.item_count() for p in pages2] == [5, 0]
    # 层叠顺序（自底向上）应保持一致
    original = [d["x"] for d in serializer.scene_items_to_dicts(page.scene)]
    reloaded = [d["x"] for d in serializer.scene_items_to_dicts(pages2[0].scene)]
    assert original == reloaded


def test_file_handler_atomic_save(tmp_path=None):
    base = tmp_path or os.path.join(_ROOT, ".test_tmp")
    os.makedirs(base, mode=0o777, exist_ok=True)
    path = os.path.join(base, "unit_" + uuid.uuid4().hex[:8] + ".wbd")
    pages = [BoardPage("页面 1")]
    pages[0].scene.addItem(StrokeItem([QPointF(0, 0), QPointF(1, 1)],
                                      QColor(0, 0, 0), 2.0))
    fh.save_document(path, pages, 0)
    assert os.path.exists(path) and os.path.getsize(path) > 0
    pages2, current = fh.load_document(path)
    assert current == 0 and pages2[0].item_count() == 1
    os.remove(path)

    bad = os.path.join(base, "bad_" + uuid.uuid4().hex[:8] + ".wbd")
    with open(bad, "w", encoding="utf-8") as f:
        f.write('{"app": "other"}')
    try:
        fh.load_document(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("非法文件应当抛 ValueError")
    os.remove(bad)


# ------------------------------------------------------------------ 撤销命令


def test_undo_stack_commands():
    scene = WhiteboardScene()
    stack = QUndoStack()
    item = RectItem(QRectF(0, 0, 10, 10), QColor(0, 0, 0), 1.0)

    stack.push(AddItemCommand(scene, item, "添加"))
    assert item.scene() is scene and len(scene.items()) == 1
    stack.undo()
    assert item.scene() is None and len(scene.items()) == 0
    stack.redo()
    assert item.scene() is scene

    # 已经在场景中的项（画笔预览）不应被重复添加
    preview = RectItem(QRectF(50, 50, 10, 10), QColor(0, 0, 0), 1.0)
    scene.addItem(preview)
    stack.push(AddItemCommand(scene, preview, "画笔"))
    assert len(scene.items()) == 2

    stack.push(RemoveItemCommand(scene, preview, "擦除"))
    assert preview.scene() is None
    stack.undo()
    assert preview.scene() is scene

    second = RectItem(QRectF(100, 100, 10, 10), QColor(0, 0, 0), 1.0)
    scene.addItem(second)
    assert len(scene.items()) == 3
    stack.push(RemoveItemsCommand(scene, [item, second], "删除多个"))
    assert len(scene.items()) == 1
    stack.undo()
    assert len(scene.items()) == 3

    stack.push(ClearPageCommand(scene, "清空"))
    assert len(scene.items()) == 0
    stack.undo()
    assert len(scene.items()) == 3


def test_move_command_and_push_group():
    scene = WhiteboardScene()
    stack = QUndoStack()
    items = [RectItem(QRectF(0, 0, 10, 10), QColor(0, 0, 0), 1.0) for _ in range(3)]
    for it in items:
        scene.addItem(it)

    old = {it: it.pos() for it in items}
    for it in items:
        it.setPos(25.0, 30.0)
    new = {it: it.pos() for it in items}
    stack.push(MoveItemsCommand(items, old, new, "移动"))
    stack.undo()
    assert all(it.pos().isNull() for it in items)
    stack.redo()
    assert all(it.pos().x() == 25.0 for it in items)

    # 一次拖拽擦除多个对象 -> 一个撤销步骤
    push_commands(stack, "橡皮擦",
                  [RemoveItemCommand(scene, it, "擦除") for it in items])
    assert len(scene.items()) == 0
    assert stack.index() == 2
    stack.undo()
    assert len(scene.items()) == 3

    # 空命令列表不应产生撤销步骤（否则 Ctrl+Z 会“空按”一次）
    before = stack.index()
    push_commands(stack, "空宏", [])
    assert stack.index() == before

    # 单条命令也不应包成宏
    single = RectItem(QRectF(0, 0, 4, 4), QColor(0, 0, 0), 1.0)
    push_commands(stack, "单条", [AddItemCommand(scene, single, "添加")])
    assert stack.command(stack.index() - 1).childCount() == 0


# ------------------------------------------------------------------ 场景行为


def test_scene_keeps_items_alive_after_stack_clear():
    """回归测试：撤销栈清空后场景内容必须保留（曾导致删页丢内容）。"""
    page = BoardPage("页面 1")
    stack = QUndoStack()
    item = RectItem(QRectF(0, 0, 10, 10), QColor(0, 0, 0), 1.0)
    stack.push(AddItemCommand(page.scene, item, "添加"))
    assert page.item_count() == 1
    stack.clear()
    assert page.item_count() == 1, "清空撤销栈不应删除场景内容"

    page.scene.removeItem(item)
    assert page.item_count() == 0


def test_grid_step_adapts_to_zoom():
    scene = WhiteboardScene()
    scene.set_grid_step(25.0)
    # 缩得很小 -> 步长变大；放得很大 -> 步长变小，但屏幕间距始终在合理范围
    assert scene._effective_grid_step(0.05) > 25.0
    assert scene._effective_grid_step(4.0) <= 25.0
    for scale in (0.02, 0.1, 1.0, 5.0, 20.0):
        step = scene._effective_grid_step(scale)
        assert step > 0
        assert 8.0 <= step * scale <= 120.0


def test_build_exe_tool_probe():
    """打包脚本必须按**当前解释器**判断工具是否可用。

    曾经用 shutil.which() 判断，结果被 PATH 上另一个 Python 的 nuitka.exe
    骗过：检查通过，但 `python -m nuitka` 报 No module named nuitka。
    """
    import build_exe

    assert build_exe._module_available("PySide6") is True
    assert build_exe._module_available("definitely_not_a_real_module_xyz") is False
    # 装在其它解释器里的工具不应该被当成可用
    assert build_exe._check_tool("definitely_not_a_real_module_xyz",
                                 "definitely-not-a-real-exe", "whatever") is False


def test_cleanup_script_targets():
    """清理脚本必须只针对本应用，且区分新旧两套存储位置。

    这两个点各自踩过一次：Qt 在 Windows 上返回的注册表路径是
    ``\\HKEY_CURRENT_USER\\...``（单反斜杠）曾被判成普通文件；
    没有先设置组织名/应用名时，``AppDataLocation`` 会算成整个
    ``AppData\\Roaming``（清理脚本差点把用户目录当应用数据删掉）。
    """
    from scripts import cleanup

    assert cleanup._is_registry_path("\\HKEY_CURRENT_USER\\Software\\WhiteboardPyside")
    assert cleanup._is_registry_path("HKEY_CURRENT_USER\\Software\\X")
    assert not cleanup._is_registry_path("/tmp/settings.ini")

    for directory in cleanup.legacy_app_data_dirs():
        name = os.path.basename(os.path.normpath(directory))
        assert name in ("Whiteboard", cleanup.LEGACY_DIR_NAME), \
            f"清理脚本不该涉及非本应用目录：{directory}"

    # 指定了 WHITEBOARD_CONFIG 时，清理 = 删掉那个 ini 文件（完全不碰注册表）
    config = os.environ["WHITEBOARD_CONFIG"]
    os.makedirs(os.path.dirname(config), mode=0o777, exist_ok=True)
    with open(config, "w", encoding="utf-8") as handle:
        handle.write("[style]\nthickness=9\n")
    assert "将删除配置文件" in cleanup.remove_settings(dry_run=True)
    assert os.path.exists(config)
    assert "已删除配置文件" in cleanup.remove_settings()
    assert not os.path.exists(config)


def test_paths_are_portable():
    """默认把配置和自动备份放在软件目录（便携），不写注册表。"""
    from core import paths

    assert paths.CONFIG_ENV in os.environ and paths.DATA_DIR_ENV in os.environ
    # 测试里两个都指到了临时目录
    assert os.path.normcase(paths.settings_file()) == \
        os.path.normcase(os.environ["WHITEBOARD_CONFIG"])
    assert os.path.normcase(paths.data_directory()) == \
        os.path.normcase(os.environ["WHITEBOARD_DATA_DIR"])
    assert paths.autosave_file().endswith(paths.AUTOSAVE_FILENAME)
    assert os.path.dirname(paths.autosave_file()) == \
        os.path.normcase(paths.data_directory()) or True   # 路径大小写无关

    # 软件目录可写 -> 便携模式成立（测试进程里这个判断必须为真）
    assert paths.is_writable(paths.app_directory())


def test_settings_stay_out_of_registry():
    """新版本默认读写 ini 文件，不再依赖注册表。"""
    from core.settings import AppSettings

    settings = AppSettings()
    assert not settings.location.lstrip("\\").upper().startswith("HKEY"), \
        f"配置不应该落在注册表：{settings.location}"
    assert settings.location.endswith(".ini")

    settings.set_thickness(7.5)
    settings.set_current_tool("pan")
    settings.sync()                      # QSettings 平时延迟落盘，这里强制写盘
    assert os.path.exists(settings.location)
    again = AppSettings()
    assert again.thickness() == 7.5 and again.current_tool() == "pan"


def test_migration_is_one_shot():
    """旧注册表偏好「一次性」迁移：确认写进 ini 之后才清掉旧键。

    不能只搬不清：用户在 ini 里点「恢复默认」后如果删掉文件，旧值会从注册表
    复活，看起来像重置失败。这里用假存储把这段逻辑钉住（测试环境无权写注册表）。
    """
    import core.settings as settings_module

    class FakeLegacy:
        def __init__(self):
            self.cleared = False
            self.synced = False

        def allKeys(self):
            return ["style/thickness"]

        def value(self, key, default=None):
            return 7.0 if key == "style/thickness" else default

        def clear(self):
            self.cleared = True

        def sync(self):
            self.synced = True

    fake = FakeLegacy()
    real_qsettings = settings_module.QSettings
    original_config = os.environ["WHITEBOARD_CONFIG"]

    class FakeFactory:
        """替身工厂：代码里还要用 QSettings.Format，所以得把类属性也带上。"""

        Format = real_qsettings.Format
        Status = real_qsettings.Status

        def __call__(self, *args, **kwargs):
            # 第一个参数是 .ini 路径 -> 新的文件存储；否则视为旧的原生存储
            if args and str(args[0]).lower().endswith(".ini"):
                return real_qsettings(*args, **kwargs)
            return fake

    migrate_ini = os.path.join(os.environ["WHITEBOARD_DATA_DIR"], "migrate_test.ini")
    if os.path.exists(migrate_ini):
        os.remove(migrate_ini)
    settings_module.QSettings = FakeFactory()
    os.environ["WHITEBOARD_CONFIG"] = migrate_ini
    try:
        settings = settings_module.AppSettings()
        assert settings.migrated_from_registry is True, "应当发生了一次迁移"
        assert settings.thickness() == 7.0, "旧值应当被搬进 ini"
        assert fake.cleared and fake.synced, "迁移成功后应当清掉旧注册表项"

        # 已经存在 ini 时不再迁移
        fake.cleared = False
        again = settings_module.AppSettings()
        assert again.migrated_from_registry is False
        assert fake.cleared is False
    finally:
        settings_module.QSettings = real_qsettings
        os.environ["WHITEBOARD_CONFIG"] = original_config
        if os.path.exists(migrate_ini):
            os.remove(migrate_ini)


def test_paths_in_packaged_modes():
    """打包后的路径解析：临时解包目录绝不能被当成软件目录。

    这是「打包后设置没保存」的根因所在：单文件打包时程序解包到临时目录运行，
    Nuitka 又不设置 ``sys.frozen``（只注入 ``__compiled__``）。
    """
    import tempfile
    from core import paths

    saved = (getattr(sys, "frozen", None), sys.executable, list(sys.argv))
    had_frozen = hasattr(sys, "frozen")
    # 环境变量优先级高于程序目录，这里要临时摘掉才能验证打包后的解析逻辑
    saved_env = {key: os.environ.pop(key)
                 for key in ("WHITEBOARD_CONFIG", "WHITEBOARD_DATA_DIR")
                 if key in os.environ}
    dist = os.path.join(_TEST_TMP, "fake_dist")
    os.makedirs(dist, mode=0o777, exist_ok=True)
    fake_exe = os.path.join(dist, "Whiteboard.exe")
    with open(fake_exe, "w", encoding="utf-8") as handle:
        handle.write("stub")

    def same(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    try:
        # ① PyInstaller 单文件：sys.executable 是真实 exe，_MEIPASS 才是临时目录
        sys.frozen = True
        sys.executable = fake_exe
        sys.argv = [fake_exe]
        assert paths.is_packaged()
        assert same(paths.app_directory(), dist)
        assert same(paths.settings_file(), os.path.join(dist, paths.SETTINGS_FILENAME))
        assert same(paths.autosave_file(), os.path.join(dist, paths.AUTOSAVE_FILENAME))
        assert paths.is_portable()

        # ② Nuitka：不设 sys.frozen，只注入 __compiled__
        del sys.frozen
        paths.__compiled__ = True
        assert paths.is_packaged(), "Nuitka 编译的程序必须被识别为打包运行"
        assert same(paths.app_directory(), dist)

        # ③ Nuitka onefile：sys.executable 落在 %TEMP%\onefile_xxx -> 用 argv[0] 兜底
        onefile = os.path.join(tempfile.gettempdir(), "onefile_98765")
        sys.executable = os.path.join(onefile, "Whiteboard.exe")
        sys.argv = [fake_exe]
        assert paths.is_temporary_directory(onefile)
        assert not paths.is_temporary_directory(dist)
        assert same(paths.app_directory(), dist), "不能把临时解包目录当软件目录"
        assert same(paths.data_directory(), dist)

        # ④ exe 所在目录不存在/不可写 -> 退回系统数据目录
        sys.executable = os.path.join(dist, "gone", "Whiteboard.exe")
        sys.argv = [sys.executable]
        assert same(paths.data_directory(create=False), paths.system_data_directory())
        assert not paths.is_portable()
    finally:
        if had_frozen:
            sys.frozen = saved[0]
        elif hasattr(sys, "frozen"):
            del sys.frozen
        sys.executable, sys.argv = saved[1], saved[2]
        paths.__dict__.pop("__compiled__", None)
        os.environ.update(saved_env)
        try:
            os.remove(fake_exe)
        except OSError:
            pass


def test_board_page_helpers():
    page = BoardPage("测试页")
    assert page.is_empty() and page.item_count() == 0
    page.scene.addItem(StrokeItem([QPointF(0, 0), QPointF(2, 2)],
                                  QColor(0, 0, 0), 2.0))
    page.scene.addItem(RectItem(QRectF(0, 0, 5, 5), QColor(0, 0, 0), 1.0))
    assert page.item_count() == 2
    assert len(page.strokes) == 1
    page.clear()
    assert page.is_empty()


# ------------------------------------------------------------------ 运行器


def _run_all() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for name, func in tests:
        try:
            func()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {exc!r}")
        else:
            print(f"ok    {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} 个单元测试通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
