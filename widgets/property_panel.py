"""右侧参数侧边栏（widgets/property_panel.py）。

选中对象后在这里改参数，改一下就立刻反映到画布上（实时预览）：
颜色、线宽、线型、填充、文字内容与排版、图形内部的文字标签、图片缩放与不透明度。

为什么用侧边栏而不是弹窗：

* 参数是"边看边调"的，弹窗会挡住画布，看不到效果；
* 一个地方就能改所有对象类型的参数（图形 / 文字框 / 图形内文字 / 图片 / 组合）；
* 原来的「文字输入」模态对话框也并进来了：双击文字框后直接在侧边栏里输入内容。

面板本身**不碰图形项**，只做两件事：根据 :class:`TargetInfo` 显示当前值、
以及把用户改动以 ``changed(dict)`` 信号发出去（只带改动的那几项），
具体怎么应用、怎么进撤销栈由主窗口决定。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from canvas.items import LINE_STYLE_LABELS, LINE_STYLES
from core import fonts
from core.text_format import ALIGN_LABELS, ALIGNS
from widgets.color_picker import ColorPickerButton
from widgets.line_style_picker import STYLE_ORDER

MIN_FONT_SIZE, MAX_FONT_SIZE = 6, 400
MIN_BOX, MAX_BOX = 20, 4000


@dataclass
class TargetInfo:
    """当前选中对象的"参数视图模型"：面板据此显示，None 表示该项目不适用。"""

    kind: str = "none"            # none/stroke/shape/line/text/image/group
    title: str = "未选中对象"
    hint: str = ""
    count: int = 1
    # 描边类
    color: QColor = None
    width: float = None
    line_style: str = None
    fill: QColor = None
    has_fill: bool = False
    # 文字类（文字框本体 / 图形内标签）
    text: str = None
    text_color: QColor = None
    font_family: str = None
    pixel_size: float = None
    bold: bool = None
    italic: bool = None
    align: str = None
    box_width: float = None
    box_height: float = None
    is_text_box: bool = False
    # 图片
    scale_percent: float = None
    opacity_percent: float = None
    # 组合
    group_size: int = None

    def has_outline(self) -> bool:
        return self.color is not None or self.width is not None

    def has_text(self) -> bool:
        return self.text is not None


class PropertyPanel(QDockWidget):
    """参数侧边栏。"""

    changed = Signal(dict)          # {"color": QColor} / {"width": 4.0} / ...

    def __init__(self, parent=None, settings=None) -> None:
        super().__init__("参数", parent)
        self.settings = settings
        self._info = TargetInfo()
        self._loading = False        # 回填数值时屏蔽信号，避免误发 changed
        self.setObjectName("property_panel")
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea
                             | Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setMinimumWidth(240)
        self._build_ui()
        self.bind(TargetInfo())

    # ============================================================ 界面
    def _build_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget(scroll)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.title_label = QLabel("未选中对象", body)
        self.title_label.setStyleSheet("font-weight: bold;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.hint_label = QLabel("", body)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.hint_label)

        layout.addWidget(self._build_outline_group(body))
        layout.addWidget(self._build_text_group(body))
        layout.addWidget(self._build_image_group(body))
        layout.addStretch(1)
        scroll.setWidget(body)
        self.setWidget(scroll)

    def _build_outline_group(self, parent) -> QGroupBox:
        box = QGroupBox("图形参数", parent)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)

        self.color_button = ColorPickerButton(QColor("#000000"), box)
        self.color_button.setToolTip("描边 / 笔迹 / 文字颜色")
        self.color_button.colorChanged.connect(
            lambda color: self._emit({"color": QColor(color)}))
        form.addRow("颜色", self.color_button)

        width_row = QHBoxLayout()
        self.width_slider = QSpinBox(box)
        self.width_slider.setRange(1, 60)
        self.width_slider.setSuffix(" px")
        self.width_slider.setSizePolicy(QSizePolicy.Policy.Fixed,
                                        QSizePolicy.Policy.Fixed)
        self.width_slider.valueChanged.connect(
            lambda value: self._emit({"width": float(value)}))
        width_row.addWidget(self.width_slider)
        width_row.addStretch(1)
        form.addRow("线宽", width_row)

        self.style_combo = QComboBox(box)
        for name in STYLE_ORDER:
            if name in LINE_STYLES:
                self.style_combo.addItem(LINE_STYLE_LABELS[name], name)
        self.style_combo.currentIndexChanged.connect(
            lambda _index: self._emit({"line_style": self.style_combo.currentData()}))
        form.addRow("线型", self.style_combo)

        fill_row = QHBoxLayout()
        self.fill_check = QCheckBox("填充", box)
        self.fill_check.toggled.connect(self._on_fill_toggled)
        self.fill_button = ColorPickerButton(QColor("#ffffff"), box)
        self.fill_button.colorChanged.connect(
            lambda color: self._emit({"fill": QColor(color)}))
        fill_row.addWidget(self.fill_check)
        fill_row.addWidget(self.fill_button)
        fill_row.addStretch(1)
        form.addRow("", fill_row)

        self.outline_group = box
        return box

    def _build_text_group(self, parent) -> QGroupBox:
        box = QGroupBox("文字参数", parent)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)

        self.text_edit = QTextEdit(box)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setPlaceholderText("在这里输入文字（画布上实时预览）")
        self.text_edit.setMinimumHeight(90)
        self.text_edit.textChanged.connect(self._on_text_changed)
        form.addRow(self.text_edit)

        self.font_combo = QComboBox(box)
        self.font_combo.setToolTip("列表按各自字体预览；导入的字体也会出现在这里")
        self.font_combo.currentIndexChanged.connect(
            lambda _index: self._emit({"font_family": self.font_combo.currentData() or ""}))
        form.addRow("字体", self.font_combo)

        self.import_button = QPushButton("导入字体…", box)
        self.import_button.setToolTip(
            "把 .ttf/.otf/.ttc 复制到软件目录的 fonts/ 并注册到本软件\n"
            "（不写系统字体目录，不需要管理员权限）")
        self.import_button.clicked.connect(self.import_font)
        form.addRow("", self.import_button)

        size_row = QHBoxLayout()
        self.size_spin = QSpinBox(box)
        self.size_spin.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self.size_spin.setSuffix(" px")
        self.size_spin.valueChanged.connect(
            lambda value: self._emit({"pixel_size": float(value)}))
        size_row.addWidget(self.size_spin)
        self.bold_button = QToolButton(box)
        self.bold_button.setText("B")
        self.bold_button.setCheckable(True)
        self.bold_button.setToolTip("粗体")
        bold_font = self.bold_button.font()
        bold_font.setBold(True)
        self.bold_button.setFont(bold_font)
        self.bold_button.toggled.connect(lambda on: self._emit({"bold": bool(on)}))
        self.italic_button = QToolButton(box)
        self.italic_button.setText("I")
        self.italic_button.setCheckable(True)
        self.italic_button.setToolTip("斜体")
        italic_font = self.italic_button.font()
        italic_font.setItalic(True)
        self.italic_button.setFont(italic_font)
        self.italic_button.toggled.connect(lambda on: self._emit({"italic": bool(on)}))
        size_row.addWidget(self.bold_button)
        size_row.addWidget(self.italic_button)
        size_row.addStretch(1)
        form.addRow("字号", size_row)

        self.align_combo = QComboBox(box)
        for key in ALIGNS:
            self.align_combo.addItem(ALIGN_LABELS[key], key)
        self.align_combo.currentIndexChanged.connect(
            lambda _index: self._emit({"align": self.align_combo.currentData()}))
        form.addRow("对齐", self.align_combo)

        box_row = QHBoxLayout()
        self.box_width_spin = QSpinBox(box)
        self.box_width_spin.setRange(MIN_BOX, MAX_BOX)
        self.box_width_spin.setSuffix(" px")
        self.box_width_spin.setToolTip("字体框宽度（文字按这个宽度折行）")
        self.box_width_spin.valueChanged.connect(
            lambda value: self._emit({"box_width": float(value)}))
        self.box_height_spin = QSpinBox(box)
        self.box_height_spin.setRange(MIN_BOX, MAX_BOX)
        self.box_height_spin.setSuffix(" px")
        self.box_height_spin.setToolTip("字体框高度")
        self.box_height_spin.valueChanged.connect(
            lambda value: self._emit({"box_height": float(value)}))
        box_row.addWidget(self.box_width_spin)
        box_row.addWidget(self.box_height_spin)
        form.addRow("框宽/高", box_row)

        self.text_color_button = ColorPickerButton(QColor("#000000"), box)
        self.text_color_button.setToolTip("文字颜色（优先级高于「图形参数 → 颜色」）")
        self.text_color_button.colorChanged.connect(
            lambda color: self._emit({"text_color": QColor(color)}))
        form.addRow("文字颜色", self.text_color_button)

        self.text_group = box
        return box

    def _build_image_group(self, parent) -> QGroupBox:
        box = QGroupBox("图片参数", parent)
        form = QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)

        self.scale_spin = QSpinBox(box)
        self.scale_spin.setRange(2, 2000)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setToolTip("显示尺寸比例（100% = 原始像素大小）")
        self.scale_spin.valueChanged.connect(
            lambda value: self._emit({"scale_percent": float(value)}))
        form.addRow("缩放", self.scale_spin)

        self.opacity_spin = QSpinBox(box)
        self.opacity_spin.setRange(5, 100)
        self.opacity_spin.setSuffix(" %")
        self.opacity_spin.valueChanged.connect(
            lambda value: self._emit({"opacity_percent": float(value)}))
        form.addRow("不透明度", self.opacity_spin)

        self.image_group = box
        return box

    # ============================================================ 回填 / 发信号
    def bind(self, info: TargetInfo) -> None:
        """按当前选中对象显示/隐藏各分组并回填数值（不会触发 changed）。"""
        self._info = info
        self._loading = True
        try:
            self.title_label.setText(info.title)
            self.hint_label.setText(info.hint)
            self.hint_label.setVisible(bool(info.hint))
            if not info.has_text():
                # 没有文字对象时清空内容框，免得下次选中别的对象时看到上一段文字
                self.text_edit.setPlainText("")
            self.outline_group.setVisible(info.has_outline())
            self.text_group.setVisible(info.has_text())
            self.image_group.setVisible(info.scale_percent is not None
                                        or info.opacity_percent is not None)
            if info.has_outline():
                if info.color is not None:
                    self.color_button.set_color(info.color, emit=False)
                    self.text_color_button.set_color(info.color, emit=False)
                self.width_slider.setValue(int(round(info.width or 2.0)))
                index = self.style_combo.findData(info.line_style or "solid")
                self.style_combo.setCurrentIndex(max(0, index))
                self.fill_check.setChecked(bool(info.has_fill))
                self.fill_button.setEnabled(bool(info.has_fill))
                if info.fill is not None:
                    self.fill_button.set_color(info.fill, emit=False)
            if info.has_text():
                self.text_edit.setPlainText(info.text or "")
                self._rebuild_font_combo(info.font_family or "")
                self.size_spin.setValue(int(max(MIN_FONT_SIZE,
                                                min(MAX_FONT_SIZE,
                                                    info.pixel_size or 16.0))))
                self.bold_button.setChecked(bool(info.bold))
                self.italic_button.setChecked(bool(info.italic))
                align_index = self.align_combo.findData(info.align or "left")
                self.align_combo.setCurrentIndex(max(0, align_index))
                if info.text_color is not None:
                    self.text_color_button.set_color(info.text_color, emit=False)
                has_box = info.box_width is not None
                self.box_width_spin.setVisible(has_box)
                self.box_height_spin.setVisible(bool(info.is_text_box))
                if has_box:
                    self.box_width_spin.setValue(
                        int(max(MIN_BOX, min(MAX_BOX, info.box_width))))
                    self.box_height_spin.setValue(
                        int(max(MIN_BOX, min(MAX_BOX, info.box_height or MIN_BOX))))
            if info.scale_percent is not None:
                self.scale_spin.setValue(
                    int(max(2, min(2000, round(info.scale_percent)))))
            if info.opacity_percent is not None:
                self.opacity_spin.setValue(
                    int(max(5, min(100, round(info.opacity_percent)))))
        finally:
            self._loading = False

    def focus_text_editor(self) -> None:
        """双击文字对象后把光标送进内容框（"双击才输入文字"）。"""
        if not self.text_group.isVisible():
            return
        self.text_edit.setFocus()
        cursor = self.text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)

    # ============================================================ 字体列表
    def _rebuild_font_combo(self, select: str = "") -> None:
        from PySide6.QtGui import QFontDatabase

        families = []
        for name in QFontDatabase.families():
            if name and not name.startswith(".") and name not in families:
                families.append(name)
        if select and select not in families:
            families.insert(0, select)
        if not families:
            families = [""]
        self.font_combo.blockSignals(True)
        self.font_combo.clear()
        for name in families:
            self.font_combo.addItem(name or "（系统默认字体）", name)
            self.font_combo.setItemData(self.font_combo.count() - 1,
                                        QFont(name) if name else QFont(),
                                        Qt.ItemDataRole.FontRole)
        index = self.font_combo.findData(select) if select else -1
        self.font_combo.setCurrentIndex(index if index >= 0 else 0)
        self.font_combo.blockSignals(False)

    def import_font(self) -> None:
        from PySide6.QtCore import QStandardPaths

        start = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation) or "."
        path, _ = QFileDialog.getOpenFileName(self, "导入字体", start,
                                              fonts.FONT_FILE_FILTER)
        if not path:
            return
        try:
            family, stored, families = fonts.import_font(path, self.settings)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入字体失败", f"无法导入字体：\n{exc}")
            return
        if not family:
            QMessageBox.warning(
                self, "导入字体失败",
                "这个文件里没有可用的字体（请确认是 .ttf / .otf / .ttc 字体文件）。")
            return
        self._rebuild_font_combo(family)
        self._emit({"font_family": family})
        QMessageBox.information(
            self, "字体已导入",
            f"已导入字体：{'、'.join(families) or family}\n\n"
            f"字体文件复制到：\n{stored}\n\n"
            "该字体只对本软件生效，已经可以在字体列表中选用。")

    def refresh_fonts(self) -> None:
        """外部导入字体后刷新列表（例如文字工具里导入的字体）。"""
        self._rebuild_font_combo(self.font_combo.currentData() or "")

    # ============================================================ 内部
    def _emit(self, changes: dict) -> None:
        if self._loading:
            return
        # 注意不要过滤掉 None：取消填充时发的就是 {"fill": None}，
        # 过滤掉就变成"取消不了填充"。
        changes = {key: value for key, value in changes.items()
                   if key in changes and not callable(value)}
        if changes:
            self.changed.emit(changes)

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self.changed.emit({"text": self.text_edit.toPlainText()})

    def _on_fill_toggled(self, checked: bool) -> None:
        self.fill_button.setEnabled(bool(checked))
        if self._loading:
            return
        self.changed.emit({"fill": QColor(self.fill_button.color()) if checked else None})
