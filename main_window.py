"""主窗口（main_window.py）：集成菜单、工具栏、画布视图、状态栏与多页面。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
)
from shiboken6 import isValid

from canvas.view import WhiteboardView
from canvas.items import LINE_STYLE_LABELS, ImageItem
from core import fonts, paths
from core.version import APP_TITLE, __version__
from core.history import (
    AddItemCommand,
    ClearPageCommand,
    RemoveItemsCommand,
)
from core.page import BoardPage
from core.settings import AppSettings
from core.text_format import TextFormat
from persistence import file_handler as fh
from tools.eraser_tool import EraserTool
from tools.pan_tool import PanTool
from tools.pen_tool import PenTool
from tools.selector_tool import SelectorTool
from tools.shape_tool import SHAPE_SPECS, ShapeTool, is_line_kind, shape_label
from tools.text_tool import TextTool
from widgets import icons
from widgets.color_picker import ColorPickerButton
from widgets.line_style_picker import LineStylePicker
from widgets.page_navigator import PageNavigator
from widgets.thickness_slider import ThicknessSlider

# 非形状工具（选择/拖动/画笔/橡皮/文字）
TOOL_META = [
    ("selector", "选择", SelectorTool),
    ("pan", "拖动", PanTool),
    ("pen", "画笔", PenTool),
    ("eraser", "橡皮", EraserTool),
    ("text", "文字", TextTool),
]

# 形状工具：种类名直接作为工具键（"rect"/"round_rect"/"triangle"/"dashed_arrow"…），
# 所以设置里保存的 tools/current 就能记住用户上次用的是哪个形状。
SHAPE_TOOL_KEYS = tuple(SHAPE_SPECS.keys())

# 工具栏上按这个顺序摆放（形状按钮是一个带菜单的下拉按钮）
TOOLBAR_BASE_ORDER = ("selector", "pan", "pen", "eraser")

RESOURCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")


# 工具栏按钮的悬浮提示（不写就用工具名）
TOOL_TIPS = {
    "selector": "选择/移动：点击选中、Ctrl 多选、空白处拖拽框选、双击文字可编辑",
    "pan": "拖动画布：按住左键拖动（也可用空格+左键或中键拖拽）",
    "pen": "画笔：自由手绘（线宽随「粗细」、线型随「线型」）",
    "eraser": "橡皮擦：擦掉经过的笔迹片段；范围随「粗细」变化",
    "text": "文字：点击画布输入文字；可设字体、字号、颜色、自动换行，也能导入字体",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 800)

        self.settings = AppSettings()
        # 重新注册用户之前导入的字体（fonts/ 目录里的 .ttf/.otf）
        self.imported_font_families = fonts.load_imported_fonts(self.settings)
        self._theme = self.settings.theme()
        self.pages: list[BoardPage] = [BoardPage("页面 1")]
        self.page_index = 0
        self.current_file = None

        # ---------------------------------------------------------- 画布
        self.view = WhiteboardView(self.pages[0].scene, self)
        self.view.zoomChanged.connect(self._on_zoom_changed)
        self.view.cursorMoved.connect(self._on_cursor_moved)
        self.view.deleteRequested.connect(self.delete_selected)
        self.setCentralWidget(self.view)
        self._bind_page(self.pages[0])

        # ---------------------------------------------------------- 工具
        self.tools: dict = {}
        self.tool_actions: dict = {}
        self.shape_actions: dict = {}
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)

        self.text_format = self.settings.text_format()
        for key, label, factory in TOOL_META:
            if key == "text":
                tool = TextTool(text_format=self.text_format, settings=self.settings)
                tool.format_changed = self._on_text_format_changed
            else:
                tool = factory() if callable(factory) else factory
            tool.color = self.settings.color()
            # 需要读写设置的工具（文字对话框要用 settings 记住导入的字体）
            if hasattr(tool, "settings"):
                tool.settings = self.settings
            self.tools[key] = tool
            self.tool_group.addAction(self._build_tool_action(key, label))

        # 形状工具：每个种类一个 QAction，集中放在「形状」下拉菜单里
        for kind in SHAPE_TOOL_KEYS:
            tool = ShapeTool(kind, self.settings.color(), self.settings.thickness(),
                             line_style=self.settings.line_style())
            self.tools[kind] = tool
            action = self._build_tool_action(kind, shape_label(kind))
            self.shape_actions[kind] = action
            self.tool_group.addAction(action)

        # 交互样式控件
        self.color_picker = ColorPickerButton(self.settings.color(), self)
        self.color_picker.colorChanged.connect(self._on_color_changed)
        self.thickness_slider = ThicknessSlider(self)
        self.thickness_slider.set_value(self.settings.thickness())
        self.thickness_slider.thicknessChanged.connect(self._on_thickness_changed)
        self.line_style_picker = LineStylePicker(self.settings.line_style(), self)
        self.line_style_picker.set_theme(self._theme)
        self.line_style_picker.lineStyleChanged.connect(self._on_line_style_changed)

        self._create_actions()
        self._create_menus()
        self._create_toolbars()
        self._create_statusbar()

        # ---------------------------------------------------------- 设置还原
        geom = self.settings.window_geometry()
        if geom is not None:
            self.restoreGeometry(geom)
        state = self.settings.window_state()
        if state is not None:
            self.restoreState(state)

        theme = self.settings.theme()
        self.theme_action.setChecked(theme == "dark")
        self._apply_theme(theme)
        self._apply_style_state()
        self.set_active_tool(self.settings.current_tool())
        self._update_page_ui()
        self.setWindowIcon(icons.app_icon(theme))
        self._update_title()

        # ---------------------------------------------------------- 自动保存
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave)
        if self.settings.autosave_enabled():
            self.autosave_timer.start(self.settings.autosave_interval_ms())

        QTimer.singleShot(0, self._maybe_offer_autosave_restore)
        if self.settings.migrated_from_registry:
            self.statusBar().showMessage(
                f"已把旧版注册表设置迁移到 {os.path.basename(self.settings.location)}", 6000)
        elif paths.is_portable():
            self.statusBar().showMessage(
                f"就绪 - 左键使用当前工具，空格/中键或「拖动」工具平移画布"
                f"（配置保存于 {os.path.basename(self.settings.location)}）")
        else:
            # 软件目录不可写（或落在临时解包目录）时如实告知，免得以为设置没保存
            self.statusBar().showMessage(
                f"就绪 - 软件目录不可写，配置改存到 {self.settings.location}", 10000)

    # ========================================================== 构建 UI
    def _build_tool_action(self, key: str, label: str) -> QAction:
        action = QAction(label, self)
        action.setCheckable(True)
        action.setData(key)
        tip = TOOL_TIPS.get(key)
        if tip is None and key in SHAPE_SPECS:
            category = "线段" if is_line_kind(key) else "形状"
            tip = f"{category}：{label}（按住左键拖拽绘制；线型由工具栏的「线型」决定）"
        tip = tip or label
        action.setToolTip(tip)
        action.setStatusTip(tip)
        action.setIcon(icons.tool_icon(key, self._theme))
        self.tool_actions[key] = action
        return action

    def _refresh_icons(self) -> None:
        """主题切换后按新配色重绘所有图标。"""
        for key, action in self.tool_actions.items():
            action.setIcon(icons.tool_icon(key, self._theme))
        for menu in (self._file_menu_actions, self._edit_menu_actions,
                     self._page_menu_actions, self._view_menu_actions):
            for action in menu.values():
                if action is None:
                    continue
                name = action.data()
                if isinstance(name, str) and name:
                    action.setIcon(icons.tool_icon(name, self._theme))
        self.setWindowIcon(icons.app_icon(self._theme))
        if getattr(self, "line_style_picker", None) is not None:
            self.line_style_picker.set_theme(self._theme)
        if getattr(self, "shape_button", None) is not None:
            self._sync_shape_button()

    def _create_actions(self) -> None:
        f = self._file_menu_actions = {}
        e = self._edit_menu_actions = {}
        g = self._page_menu_actions = {}
        v = self._view_menu_actions = {}
        h = self._help_menu_actions = {}

        f["new"] = self._act("新建白板", QKeySequence.StandardKey.New,
                             self.new_document, "new")
        f["open"] = self._act("打开…", QKeySequence.StandardKey.Open,
                              self.open_document, "open")
        f["save"] = self._act("保存", QKeySequence.StandardKey.Save,
                              self.save_document, "save")
        f["save_as"] = self._act("另存为…", QKeySequence.StandardKey.SaveAs,
                                 self.save_document_as, "save")
        f["export_png"] = self._act("导出为 PNG…", "Ctrl+E", self.export_png, "export")
        f["import_image"] = self._act("导入图片…", "Ctrl+I", self.import_image, "image")
        f["exit"] = self._act("退出", QKeySequence.StandardKey.Quit, self.close)

        e["undo"] = self._act("撤销", QKeySequence.StandardKey.Undo,
                              lambda: self.undo_stack.undo(), "undo")
        e["redo"] = self._act("重做", QKeySequence.StandardKey.Redo,
                              lambda: self.undo_stack.redo(), "redo")
        e["delete"] = self._act("删除选中", QKeySequence.StandardKey.Delete,
                                self.delete_selected, "trash")
        e["clear"] = self._act("清空当前页", "Ctrl+Shift+Backspace",
                               self.clear_current_page, "trash")
        e["sep"] = None

        g["add"] = self._act("新建页面", "Ctrl+T", self.add_page, "page")
        g["delete"] = self._act("删除当前页", "Ctrl+Shift+D", self.delete_page, "trash")
        g["prev"] = self._act("上一页", "PgUp", self.goto_prev_page)
        g["next"] = self._act("下一页", "PgDn", self.goto_next_page)

        v["zoom_in"] = self._act("放大", QKeySequence.StandardKey.ZoomIn,
                                 self.view.zoom_in, "zoom_in")
        v["zoom_out"] = self._act("缩小", QKeySequence.StandardKey.ZoomOut,
                                  self.view.zoom_out, "zoom_out")
        v["zoom_fit"] = self._act("适应窗口", "Ctrl+0", self.view.zoom_fit_scene, "fit")
        v["zoom_reset"] = self._act("实际大小 100%", "Ctrl+1", self.view.zoom_reset)
        v["reset_settings"] = self._act("恢复默认设置…", None, self.reset_settings)

        self.theme_action = QAction("深色模式", self)
        self.theme_action.setCheckable(True)
        self.theme_action.setIcon(icons.tool_icon("moon", self._theme))
        self.theme_action.triggered.connect(
            lambda checked: self._apply_theme("dark" if checked else "light"))
        v["theme"] = self.theme_action

        h["about"] = self._act("关于", None, self._about)
        h["shortcuts"] = self._act("快捷键帮助", None, self._shortcut_help)

    @staticmethod
    def _act(text, shortcut, slot, icon_name: str = None) -> QAction:
        action = QAction(text, None)
        if shortcut:
            action.setShortcut(shortcut)
        if icon_name:
            # 图标由 _refresh_icons 统一按主题重绘
            action.setData(icon_name)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    def _create_menus(self) -> None:
        f = self.menuBar().addMenu("文件(&F)")
        fm = self._file_menu_actions
        f.addAction(fm["new"]); f.addAction(fm["open"])
        f.addSeparator()
        f.addAction(fm["save"]); f.addAction(fm["save_as"])
        f.addSeparator()
        f.addAction(fm["export_png"]); f.addAction(fm["import_image"])
        f.addSeparator()
        f.addAction(fm["exit"])

        m = self.menuBar().addMenu("编辑(&E)")
        em = self._edit_menu_actions
        m.addAction(em["undo"]); m.addAction(em["redo"])
        m.addSeparator()
        m.addAction(em["delete"]); m.addAction(em["clear"])

        p = self.menuBar().addMenu("页面(&P)")
        pm = self._page_menu_actions
        p.addAction(pm["add"]); p.addAction(pm["delete"])
        p.addSeparator()
        p.addAction(pm["prev"]); p.addAction(pm["next"])

        v = self.menuBar().addMenu("视图(&V)")
        vm = self._view_menu_actions
        v.addAction(vm["zoom_in"]); v.addAction(vm["zoom_out"])
        v.addSeparator()
        v.addAction(vm["zoom_fit"]); v.addAction(vm["zoom_reset"])
        v.addSeparator()
        v.addAction(vm["theme"])
        v.addSeparator()
        v.addAction(vm["reset_settings"])

        hb = self.menuBar().addMenu("帮助(&H)")
        hb.addAction(self._help_menu_actions["about"])
        hb.addAction(self._help_menu_actions["shortcuts"])

        self.menuBar().setNativeMenuBar(False)

    def _create_toolbars(self) -> None:
        bar = QToolBar("工具", self)
        bar.setObjectName("tools_bar")
        bar.setMovable(False)
        self.addToolBar(bar)
        for key in TOOLBAR_BASE_ORDER:
            bar.addAction(self.tool_actions[key])
        bar.addSeparator()

        # 「形状」下拉按钮：图标显示当前形状，点开是全部形状
        # （矩形/圆角矩形/椭圆/三角形/菱形/五角星/直线/虚线/箭头/虚线箭头/双向箭头）
        self.shape_button = QToolButton(bar)
        self.shape_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.shape_button.setToolTip("形状：点开选择要画的图形")
        shape_menu = QMenu(self.shape_button)
        shape_menu.setToolTipsVisible(True)
        for kind in SHAPE_TOOL_KEYS:
            shape_menu.addAction(self.shape_actions[kind])
        self.shape_button.setMenu(shape_menu)
        bar.addWidget(self.shape_button)
        bar.addAction(self.tool_actions["text"])
        bar.addSeparator()

        bar.addWidget(self.color_picker)
        bar.addWidget(self.thickness_slider)
        bar.addWidget(self.line_style_picker)

        self.tool_group.triggered.connect(self._on_tool_triggered)

        # 选中分组后由 _on_tool_triggered 负责 setChecked，防止单选失效
        self.tool_group.setExclusive(True)
        self._sync_shape_button()

    def _create_statusbar(self) -> None:
        bar = self.statusBar()
        self.zoom_label = QLabel("100%", self)
        self.coord_label = QLabel("x: 0, y: 0", self)
        bar.addPermanentWidget(self.coord_label)
        bar.addPermanentWidget(self.zoom_label)
        self.page_navigator = PageNavigator(self)
        self.page_navigator.prevRequested.connect(self.goto_prev_page)
        self.page_navigator.nextRequested.connect(self.goto_next_page)
        self.page_navigator.newRequested.connect(self.add_page)
        self.page_navigator.deleteRequested.connect(self.delete_page)
        bar.addPermanentWidget(self.page_navigator)

    # ========================================================== 工具状态
    def set_active_tool(self, key: str) -> None:
        if key not in self.tools:
            key = "pen"
        action = self.tool_actions[key]
        action.setChecked(True)
        self._activate_tool(key)

    def _on_tool_triggered(self, action: QAction) -> None:
        key = str(action.data())
        if key in self.tools:
            self._activate_tool(key)

    def _activate_tool(self, key: str) -> None:
        tool = self.tools[key]
        self.view.set_tool(tool)
        self.settings.set_current_tool(key)
        if key in SHAPE_SPECS:
            self.settings.set_shape_kind(key)
            self.settings.sync()
        self.tool_actions[key].setChecked(True)
        self._sync_shape_button()
        if key in SHAPE_SPECS:
            line = LINE_STYLE_LABELS.get(tool.effective_line_style(), "")
            self.statusBar().showMessage(
                f"当前工具：{shape_label(key)}（线型：{line}）", 2000)
        else:
            self.statusBar().showMessage(f"当前工具：{tool.name}", 2000)

    def _sync_shape_button(self) -> None:
        """让「形状」按钮的图标/提示反映当前选中的形状。"""
        button = getattr(self, "shape_button", None)
        if button is None:
            return
        current = self.settings.current_tool()
        if current not in SHAPE_SPECS:
            current = self.settings.shape_kind()
        action = self.shape_actions.get(current) or self.shape_actions["rect"]
        button.setIcon(action.icon())
        button.setToolTip(f"形状：{shape_label(str(action.data()))}（点开选择其它图形）")

    # --------------------------------------------------------- 样式联动
    def _apply_style_state(self) -> None:
        color = self.color_picker.color()
        thickness = self.thickness_slider.value()
        self._on_color_changed(color)
        self._on_thickness_changed(thickness)
        self._on_line_style_changed(self.line_style_picker.current_style())

    def _on_color_changed(self, color: QColor) -> None:
        self.settings.set_color(color)
        for key, tool in self.tools.items():
            if hasattr(tool, "color"):
                tool.color = QColor(color)
        # 文字工具的颜色存在排版参数里，设置里也同步一份
        self.text_format = self.text_format.copy(color=QColor(color))
        self.settings.set_text_format(self.text_format)

    def _on_thickness_changed(self, value: float) -> None:
        self.settings.set_thickness(value)
        for key in ("pen",) + SHAPE_TOOL_KEYS:
            self.tools[key].thickness = float(value)
        # 橡皮直径跟随粗细，但映射要温和：
        # 以前用 value*4+8，粗细调到 40 时橡皮直径会变成 64 像素，
        # 点一下就能把整条笔迹“吃掉”，看起来就像整块删除。
        self.tools["eraser"].size = max(8.0, min(48.0, value * 2 + 8))
        # 注意：文字字号**不再**跟着粗细走 —— 文字有自己的字号参数
        # （在文字对话框里设置，并记在设置文件中）。

    def _on_line_style_changed(self, style: str) -> None:
        """线型变化：作用到画笔与所有形状工具，保存到设置。"""
        self.settings.set_line_style(style)
        self.settings.sync()
        # 画笔也算描边工具：选了虚线/点线之后手绘笔迹也该是虚线
        self.tools["pen"].line_style = style
        for kind in SHAPE_TOOL_KEYS:
            self.tools[kind].line_style = style
        current = self.settings.current_tool()
        label = LINE_STYLE_LABELS.get(style, style)
        if current in SHAPE_SPECS and SHAPE_SPECS[current][2]:
            self.statusBar().showMessage(
                f"线型：{label}（当前图形「{shape_label(current)}」固定为虚线）", 2000)
        else:
            self.statusBar().showMessage(f"线型：{label}（画笔与形状都用它）", 2000)

    def _on_text_format_changed(self, fmt: TextFormat) -> None:
        """文字对话框里改了颜色时，同步工具栏颜色按钮与设置。"""
        self.text_format = fmt.copy()
        self.settings.set_text_format(self.text_format)
        if self.color_picker.color() != fmt.color:
            self.color_picker.set_color(QColor(fmt.color), emit=False)
            self.settings.set_color(fmt.color)

    # ========================================================== 历史/撤销
    @property
    def undo_stack(self):
        """当前页的撤销栈（每页独立，避免跨页误撤销）。"""
        return self.pages[self.page_index].undo_stack

    def _bind_page(self, page: BoardPage) -> None:
        """把某一页的撤销栈接到界面刷新上（切页时也会重新绑定视图）。

        用 ``UniqueConnection`` 避免同一页被重复连接（每次切页都会调用）。
        """
        stack = page.undo_stack
        stack.indexChanged.connect(self._on_history_changed,
                                   Qt.ConnectionType.UniqueConnection)
        stack.cleanChanged.connect(self._on_clean_changed,
                                   Qt.ConnectionType.UniqueConnection)
        self.view.undo_stack = stack

    def _bind_pages(self, pages) -> None:
        for page in pages:
            self._bind_page(page)

    def _is_dirty(self) -> bool:
        """只要任意一页有未保存改动，整个文档就算脏。"""
        for page in getattr(self, "pages", None) or []:
            stack = page.undo_stack
            if isValid(stack) and not stack.isClean():
                return True
        return False

    def _mark_clean(self) -> None:
        for page in self.pages:
            if isValid(page.undo_stack):
                page.undo_stack.setClean()

    def _on_history_changed(self) -> None:
        stack = self.undo_stack
        if not isValid(stack):
            return
        # __init__ 里绑定撤销栈时菜单可能还没建好，这里做一次容错
        actions = getattr(self, "_edit_menu_actions", None)
        if actions:
            actions["undo"].setEnabled(stack.canUndo())
            actions["redo"].setEnabled(stack.canRedo())
        self._update_title()

    def _on_clean_changed(self, _clean: bool) -> None:
        self._update_title()

    def _update_title(self) -> None:
        # 窗口销毁时撤销栈会先被删除并可能触发 cleanChanged，
        # 此时再访问它会抛 RuntimeError，这里直接跳过。
        if not isValid(self):
            return
        name = os.path.basename(self.current_file) if self.current_file else "未命名"
        dirty = " *" if self._is_dirty() else ""
        self.setWindowTitle(f"{APP_TITLE} {__version__} - {name}{dirty}")

    # ========================================================== 文件操作
    def new_document(self) -> None:
        if not self._confirm_discard():
            return
        self.current_file = None
        self.pages = [BoardPage("页面 1")]
        self.page_index = 0
        self._bind_page(self.pages[0])
        self._reload_view_for_pages()
        self.statusBar().showMessage("已新建白板", 2000)

    def open_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开白板", fh.default_directory(), fh._FILE_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        try:
            pages, current = fh.load_document(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "打开失败", f"无法读取文件：\n{exc}")
            return False
        self.pages = pages
        self.page_index = current
        self.current_file = path
        self._bind_pages(pages)
        self._reload_view_for_pages()
        fh.clear_autosave()
        self.statusBar().showMessage(f"已打开：{path}", 3000)
        return True

    def save_document(self) -> bool:
        if not self.current_file:
            return self.save_document_as()
        try:
            fh.save_document(self.current_file, self.pages, self.page_index)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return False
        self._mark_clean()
        fh.clear_autosave()
        self._update_title()
        self.statusBar().showMessage(f"已保存：{self.current_file}", 3000)
        return True

    def save_document_as(self) -> bool:
        initial = self.current_file or os.path.join(fh.default_directory(), "未命名.wbd")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存白板", initial, fh._FILE_FILTER)
        if not path:
            return False
        if not path.lower().endswith(fh.FILE_EXT):
            path += fh.FILE_EXT
        self.current_file = path
        return self.save_document()

    # --------------------------------------------------------- 导出/导入
    def export_png(self) -> None:
        """把当前页导出为 PNG。

        导出范围是“所有图形项的外接矩形 + 边距”，而不是整个场景矩形
        （场景是 20000x20000 的近似无限画布，直接渲染会得到一张超大空图）。
        """
        from PySide6.QtCore import QRectF

        scene = self.view.scene()
        rect = scene.itemsBoundingRect()
        if rect.isEmpty() or rect.width() < 1 or rect.height() < 1:
            QMessageBox.information(self, "导出 PNG", "当前页面还没有内容，无需导出。")
            return
        margin = 24.0
        rect = QRectF(rect).adjusted(-margin, -margin, margin, margin)

        path, _ = QFileDialog.getSaveFileName(
            self, "导出 PNG", os.path.join(fh.default_directory(), "白板.png"),
            "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"

        factor = 2.0  # 2x 超采样，输出更清晰
        image = QImage(max(1, int(rect.width() * factor)),
                       max(1, int(rect.height() * factor)),
                       QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)

        # 导出时暂时取消选中：QGraphicsTextItem 等对象在选中态会用高亮色绘制，
        # 不清掉的话导出图里会带上选中高亮（导出应该是纯粹的画布内容）。
        selected = list(scene.selectedItems())
        for item in selected:
            item.setSelected(False)

        # 导出时隐藏点阵网格，只保留背景色与内容
        grid_color = scene.grid_color()
        scene.set_grid_color(QColor(0, 0, 0, 0))
        try:
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            # 注意：QGraphicsScene.render() 自己会把 source 映射到 target，
            # 目标矩形已经是 source 的 factor 倍，就等于做了 factor 倍超采样。
            # 这里**不能**再 painter.scale(factor, factor)，否则会缩放两次，
            # 结果只导出左上角那一块（表现为「图片被裁掉一半」）。
            scene.render(painter, target=QRectF(image.rect()), source=rect)
            painter.end()
        finally:
            scene.set_grid_color(grid_color)
            for item in selected:
                if isValid(item):
                    item.setSelected(True)

        if image.save(path, "PNG"):
            self.statusBar().showMessage(f"已导出：{path}", 3000)
        else:
            QMessageBox.critical(self, "导出失败", "PNG 写入失败")

    def import_image(self) -> None:
        """把图片文件作为图形项插入到视图中心。

        踩过的坑：``QGraphicsView.mapToScene()`` **没有 QPointF 重载**，
        传浮点坐标会抛 ``TypeError``（而且异常发生在 Qt 槽函数里，
        界面上只是「点了没反应」）。这里统一用 ``mapToScene(QPoint)`` /
        ``mapToScene(QRect)``。
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "导入图片", fh.default_directory(),
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff *.svg);;"
            "所有文件 (*)")
        if not path:
            return

        # 用 QImageReader 而不是 QPixmap(path)：失败时能拿到具体原因，
        # 并且 setAutoTransform 会按 EXIF 方向把竖拍照片摆正。
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            QMessageBox.warning(
                self, "导入失败",
                f"无法读取该图片文件：\n{path}\n\n原因：{reader.errorString()}")
            return
        pixmap = QPixmap.fromImage(image)

        # 太大的图缩到视图的 80%，否则一张照片会盖住整个画布
        view = self.view
        viewport = view.mapToScene(view.viewport().rect()).boundingRect()
        limit_w = max(64.0, viewport.width() * 0.8)
        limit_h = max(64.0, viewport.height() * 0.8)
        scaled = False
        if pixmap.width() > limit_w or pixmap.height() > limit_h:
            pixmap = pixmap.scaled(int(limit_w), int(limit_h),
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            scaled = True

        item = ImageItem(pixmap)
        center = view.mapToScene(view.viewport().rect().center())
        bounds = item.boundingRect()
        item.setPos(center - QPointF(bounds.width() / 2.0, bounds.height() / 2.0))

        scene = view.scene()
        scene.clearSelection()
        item.setSelected(True)              # 导入后直接选中，方便马上拖动
        self.undo_stack.push(AddItemCommand(scene, item, "导入图片"))
        message = f"已导入：{os.path.basename(path)}（{pixmap.width()}×{pixmap.height()}）"
        if scaled:
            message += "，已按视图大小缩放"
        self.statusBar().showMessage(message, 4000)

    # ========================================================== 编辑操作
    def delete_selected(self) -> None:
        scene = self.view.scene()
        items = [it for it in scene.selectedItems() if not it.parentItem()]
        if not items:
            return
        self.undo_stack.push(
            RemoveItemsCommand(scene, items, f"删除 {len(items)} 个对象"))

    def clear_current_page(self) -> None:
        scene = self.view.scene()
        if not scene.items():
            return
        self.undo_stack.push(ClearPageCommand(scene, "清空页面"))
        self.statusBar().showMessage("已清空当前页（可撤销）", 2000)

    # ========================================================== 页面操作
    @property
    def current_page(self) -> BoardPage:
        return self.pages[self.page_index]

    def _reload_view_for_pages(self) -> None:
        self._theme_pages()
        page = self.pages[self.page_index]
        self._bind_page(page)
        self.view.setScene(page.scene)
        self._update_page_ui()
        self._update_title()

    def _update_page_ui(self) -> None:
        self.page_navigator.set_page(self.page_index, len(self.pages))
        self._page_menu_actions["delete"].setEnabled(len(self.pages) > 1)
        self._page_menu_actions["prev"].setEnabled(self.page_index > 0)
        self._page_menu_actions["next"].setEnabled(
            self.page_index < len(self.pages) - 1)

    def _switch_page(self, index: int) -> None:
        if not (0 <= index < len(self.pages)) or index == self.page_index:
            return
        self.page_index = index
        page = self.pages[index]
        self._bind_page(page)
        self.view.setScene(page.scene)
        self.view.scene().clearSelection()
        self._update_page_ui()
        self._update_title()
        self.statusBar().showMessage(f"已切换到：{page.name}", 2000)

    def add_page(self) -> None:
        page = BoardPage(f"页面 {len(self.pages) + 1}")
        self._theme_pages([page])
        self.pages.append(page)
        self._bind_page(page)
        self._switch_page(len(self.pages) - 1)
        self.statusBar().showMessage("已新建页面", 2000)

    def delete_page(self) -> None:
        if len(self.pages) <= 1:
            return
        page = self.pages[self.page_index]
        ret = QMessageBox.question(
            self, "删除页面",
            f"确定删除“{page.name}”吗？（含 {page.item_count()} 个对象，删除不可撤销）")
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.pages.pop(self.page_index)
        self.page_index = max(0, min(self.page_index, len(self.pages) - 1))
        self._reload_view_for_pages()
        self.statusBar().showMessage("已删除当前页", 2000)

    def goto_prev_page(self) -> None:
        self._switch_page(self.page_index - 1)

    def goto_next_page(self) -> None:
        self._switch_page(self.page_index + 1)

    # ========================================================== 状态栏联动
    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{zoom * 100:.0f}%")

    def _on_cursor_moved(self, scene_pos) -> None:
        self.coord_label.setText(
            f"x: {scene_pos.x():.0f}, y: {scene_pos.y():.0f}")

    # ========================================================== 自动保存
    def autosave(self) -> None:
        """定时把文档写入应用数据目录；内容没有变化时跳过。"""
        if not self._is_dirty():
            return
        try:
            path = fh.save_autosave(self.pages, self.page_index)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"自动保存失败：{exc}", 4000)
            return
        self.statusBar().showMessage(f"已自动保存到 {path}", 2500)

    def _maybe_offer_autosave_restore(self) -> None:
        if not self.settings.autosave_enabled():
            return
        if not fh.autosave_exists():
            return
        try:
            pages, current = fh.load_document(fh.autosave_path())
        except Exception:  # noqa: BLE001
            return
        ret = QMessageBox.question(
            self, "恢复自动备份",
            "检测到上次未保存的自动备份，是否恢复？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.pages = pages
        self.page_index = current
        self.current_file = None
        self._bind_pages(pages)
        self._reload_view_for_pages()
        self.statusBar().showMessage("已恢复自动备份内容", 3000)
    # ========================================================== 主题
    def _apply_theme(self, theme: str) -> None:
        if theme not in ("light", "dark"):
            theme = "light"
        self._theme = theme
        self.settings.set_theme(theme)
        qss_path = os.path.join(RESOURCE_DIR, "styles", f"app_{theme}.qss")
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                self.setStyleSheet(f.read())
        except OSError:
            self.setStyleSheet("")
        if theme == "dark":
            bg, grid = QColor("#1e1e1e"), QColor(255, 255, 255, 22)
        else:
            bg, grid = QColor("#fbfbfb"), QColor(0, 0, 0, 28)
        self._canvas_colors = (bg, grid)
        self._theme_pages()
        if getattr(self, "tool_actions", None):
            self._refresh_icons()

    def _theme_pages(self, pages=None) -> None:
        """把当前主题的背景/网格色应用到指定页面（默认全部页面）。

        新建页面、打开文件、恢复自动备份之后都要调用，
        否则新页面的画布会停留在默认浅色背景。
        """
        bg, grid = getattr(self, "_canvas_colors",
                           (QColor("#fbfbfb"), QColor(0, 0, 0, 28)))
        for page in (self.pages if pages is None else pages):
            page.apply_theme(bg, grid)

    # ========================================================== 收尾
    def reset_settings(self) -> None:
        """把颜色/粗细/线型/工具/主题/窗口位置等偏好恢复为默认值。"""
        from core.settings import (
            DEFAULT_COLOR,
            DEFAULT_LINE_STYLE,
            DEFAULT_SHAPE,
            DEFAULT_THEME,
            DEFAULT_THICKNESS,
            DEFAULT_TOOL,
        )

        ret = QMessageBox.question(
            self, "恢复默认设置",
            "将清除已保存的偏好（颜色、粗细、线型、形状、当前工具、主题、"
            "窗口位置、文字排版、自动保存开关），恢复为出厂默认。\n\n"
            "画布内容与已导入的字体不受影响。是否继续？")
        if ret != QMessageBox.StandardButton.Yes:
            return

        self.settings.reset()
        self.color_picker.set_color(QColor(DEFAULT_COLOR), emit=False)
        self.theme_action.setChecked(DEFAULT_THEME == "dark")
        self._apply_theme(DEFAULT_THEME)
        self.thickness_slider.set_value(DEFAULT_THICKNESS)
        self.line_style_picker.set_current(DEFAULT_LINE_STYLE)
        self.text_format = TextFormat(color=QColor(DEFAULT_COLOR))
        self.settings.set_shape_kind(DEFAULT_SHAPE)
        self.tools["text"].format = self.text_format.copy()
        self._apply_style_state()
        self.settings.set_current_tool(DEFAULT_TOOL)
        self.set_active_tool(DEFAULT_TOOL)
        self.statusBar().showMessage("已恢复默认设置", 3000)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.settings.set_window_geometry(self.saveGeometry())
        self.settings.set_window_state(self.saveState())
        super().closeEvent(event)

    def _confirm_discard(self) -> bool:
        if not self._is_dirty():
            return True
        ret = QMessageBox.question(
            self, "未保存的更改",
            "当前内容有未保存的更改，是否放弃并继续？")
        return ret == QMessageBox.StandardButton.Yes

    def _about(self) -> None:
        QMessageBox.about(
            self, f"关于 {APP_TITLE}",
            f"{APP_TITLE} {__version__}\n"
            "基于 PySide6 / Qt Graphics View 的个人白板\n\n"
            "画笔 · 橡皮（擦断）· 11 种形状 · 文字（可导入字体）\n"
            "选择/框选 · 拖动画布 · 多页面 · 撤销重做\n"
            ".wbd 文件 · PNG 导出 · 导入图片 · 自动保存\n\n"
            "滚轮缩放 · 空格/中键或「拖动」工具平移\n\n"
            f"配置文件：{self.settings.location}\n"
            f"字体目录：{fonts.fonts_directory(create=False)}")

    def _shortcut_help(self) -> None:
        QMessageBox.information(
            self, "快捷键",
            "Ctrl+Z 撤销 · Ctrl+Y / Ctrl+Shift+Z 重做\n"
            "Ctrl+S 保存 · Ctrl+Shift+S 另存为\n"
            "Ctrl+O 打开 · Ctrl+E 导出 PNG · Ctrl+I 导入图片\n"
            "Ctrl+T 新建页面 · Ctrl+Shift+D 删除当前页\n"
            "PgUp/PgDn 切换页面 · Delete 删除选中\n"
            "+ / - 缩放 · Ctrl+0 适应窗口 · Ctrl+1 实际大小\n"
            "空格/中键拖拽 = 平移画布\n\n"
            "选择工具下双击文字 = 编辑内容与字体/字号/换行")
