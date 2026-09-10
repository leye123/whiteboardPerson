"""文字输入 / 排版对话框（widgets/text_dialog.py）。

一个对话框同时承担三件事：

1. **输入或修改文字**（编辑已有文字时预填原文与原来的排版）；
2. **选字体、字号、颜色、粗体/斜体、对齐**；
3. **自动换行**（可设折行宽度）+ **导入字体**（.ttf/.otf/.ttc）。

所见即所得：上方的编辑区会实时套用当前参数 —— 字号、颜色、字体、
折行宽度都直接反映在编辑框里，确定前就能看出效果。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
)

from core import fonts
from core.text_format import (
    ALIGN_LABELS,
    ALIGNS,
    TextFormat,
)
from widgets.color_picker import ColorPickerButton

# 字号范围与折行宽度范围
MIN_FONT_SIZE, MAX_FONT_SIZE = 6, 400
MIN_WRAP_WIDTH, MAX_WRAP_WIDTH = 40, 4000


class TextDialog(QDialog):
    """文字输入对话框：文本 + 排版参数一起返回。"""

    def __init__(self, parent=None, title: str = "输入文字", text: str = "",
                 fmt: TextFormat = None, settings=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.settings = settings
        # 注意：这里要**先**把调用方给的排版存一份。
        # _build_ui() 里重建字体列表会触发一次预览，而预览会把 self._format
        # 更新成「控件当前值」—— 如果直接用它去 _load_format，
        # 传进来的字号/换行宽度就被控件默认值（6px / 40px）覆盖掉了。
        self._initial_format = (fmt or TextFormat()).copy()
        self._format = self._initial_format.copy()
        self._build_ui()
        self.editor.setPlainText(text or "")
        self._load_format(self._initial_format)
        self.editor.setFocus()
        # 光标放到末尾，编辑已有文字时接着写
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    # ============================================================ 界面
    def _build_ui(self) -> None:
        self.setMinimumSize(520, 420)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        # ---- 字体 / 字号 / 粗斜体 / 颜色
        grid.addWidget(QLabel("字体", self), 0, 0)
        self.font_combo = QComboBox(self)
        self.font_combo.setMinimumWidth(190)
        self.font_combo.setToolTip("列表里的字体按自身样式预览；导入的字体也会出现在这里")
        self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        grid.addWidget(self.font_combo, 0, 1, 1, 2)

        self.import_button = QPushButton("导入字体…", self)
        self.import_button.setToolTip(
            "把 .ttf/.otf/.ttc 字体复制到软件目录的 fonts/ 并注册到本软件\n"
            "（不写系统字体目录，不需要管理员权限）")
        self.import_button.clicked.connect(self.import_font)
        grid.addWidget(self.import_button, 0, 3)

        grid.addWidget(QLabel("字号", self), 1, 0)
        self.size_spin = QSpinBox(self)
        self.size_spin.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self.size_spin.setSuffix(" px")
        self.size_spin.valueChanged.connect(self._on_style_changed)
        grid.addWidget(self.size_spin, 1, 1)

        style_box = QHBoxLayout()
        style_box.setSpacing(4)
        self.bold_button = QToolButton(self)
        self.bold_button.setText("B")
        self.bold_button.setCheckable(True)
        self.bold_button.setToolTip("粗体")
        font = self.bold_button.font()
        font.setBold(True)
        self.bold_button.setFont(font)
        self.bold_button.toggled.connect(self._on_style_changed)
        self.italic_button = QToolButton(self)
        self.italic_button.setText("I")
        self.italic_button.setCheckable(True)
        self.italic_button.setToolTip("斜体")
        font = self.italic_button.font()
        font.setItalic(True)
        self.italic_button.setFont(font)
        self.italic_button.toggled.connect(self._on_style_changed)
        style_box.addWidget(self.bold_button)
        style_box.addWidget(self.italic_button)
        style_box.addStretch(1)
        grid.addLayout(style_box, 1, 2)

        self.color_picker = ColorPickerButton(self._format.color, self)
        self.color_picker.setToolTip("文字颜色")
        self.color_picker.colorChanged.connect(self._on_color_changed)
        grid.addWidget(self.color_picker, 1, 3)

        # ---- 自动换行 / 折行宽度 / 对齐
        self.wrap_check = QCheckBox("自动换行", self)
        self.wrap_check.setToolTip("勾选后文字在指定宽度内自动折行（宽度用「行宽」设定）")
        self.wrap_check.toggled.connect(self._on_wrap_toggled)
        grid.addWidget(self.wrap_check, 2, 0)

        self.width_spin = QSpinBox(self)
        self.width_spin.setRange(MIN_WRAP_WIDTH, MAX_WRAP_WIDTH)
        self.width_spin.setSuffix(" px")
        self.width_spin.setToolTip("自动换行的折行宽度")
        self.width_spin.valueChanged.connect(self._on_style_changed)
        grid.addWidget(self.width_spin, 2, 1)

        self.align_combo = QComboBox(self)
        for key in ALIGNS:
            self.align_combo.addItem(ALIGN_LABELS[key], key)
        self.align_combo.currentIndexChanged.connect(self._on_style_changed)
        grid.addWidget(self.align_combo, 2, 2, 1, 2)

        layout.addLayout(grid)

        # ---- 编辑区（同时作为预览）
        self.editor = QTextEdit(self)
        self.editor.setAcceptRichText(False)
        self.editor.setTabChangesFocus(False)
        layout.addWidget(self.editor, 1)

        self.hint = QLabel(
            "提示：可先点「导入字体…」导入 .ttf/.otf 字体，再在上面选择它。"
            "换行宽度只在勾选「自动换行」后生效。", self)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._rebuild_font_combo(self._format.family)

    # ============================================================ 字体列表
    def _rebuild_font_combo(self, select: str = "") -> None:
        """重建字体下拉框（用普通 QComboBox 而不是 QFontComboBox）。

        原因：``QFontDatabase.addApplicationFont()`` **不会**让 QFontComboBox
        刷新它缓存的字体列表，导入字体后必须自己重建，否则新字体选不到。
        列表项用各自字体渲染，选之前就能看到字形。
        """
        families = []
        for name in QFontDatabase.families():
            if name and not name.startswith(".") and name not in families:
                families.append(name)
        if select and select not in families:
            families.insert(0, select)          # 缺失的字体也保留一个占位项
        if not families:
            # 极端情况下（例如没有字体目录的精简/CI 环境）别给用户一个空列表：
            # 放一项「系统默认字体」，family 为空 = 跟随 Qt 的默认字体。
            families = [""]
        self.font_combo.blockSignals(True)
        self.font_combo.clear()
        for name in families:
            self.font_combo.addItem(name or "（系统默认字体）", name)
            self.font_combo.setItemData(
                self.font_combo.count() - 1, QFont(name) if name else QFont(),
                Qt.ItemDataRole.FontRole)
        index = self.font_combo.findData(select) if select else -1
        self.font_combo.setCurrentIndex(index if index >= 0 else 0)
        self.font_combo.blockSignals(False)
        self._on_style_changed()

    def import_font(self) -> None:
        """导入字体文件：复制到 fonts/ 并注册到本软件。"""
        start = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "导入字体", start, fonts.FONT_FILE_FILTER)
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
        names = "、".join(families) if families else family
        QMessageBox.information(
            self, "字体已导入",
            f"已导入字体：{names}\n\n"
            f"字体文件复制到：\n{stored}\n\n"
            "该字体只对本软件生效，已经可以在字体列表中选用。")

    # ============================================================ 参数 <-> 界面
    def _load_format(self, fmt: TextFormat) -> None:
        widgets = (self.font_combo, self.size_spin, self.width_spin,
                   self.align_combo, self.bold_button, self.italic_button,
                   self.wrap_check)
        for widget in widgets:
            widget.blockSignals(True)
        self._format = fmt.copy()
        self._rebuild_font_combo(fmt.family)
        self.size_spin.setValue(int(max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, fmt.pixel_size))))
        self.width_spin.setValue(
            int(max(MIN_WRAP_WIDTH, min(MAX_WRAP_WIDTH, fmt.text_width))))
        self.bold_button.setChecked(bool(fmt.bold))
        self.italic_button.setChecked(bool(fmt.italic))
        self.wrap_check.setChecked(bool(fmt.wrap))
        index = self.align_combo.findData(fmt.align if fmt.align in ALIGNS else "left")
        self.align_combo.setCurrentIndex(max(0, index))
        self.color_picker.blockSignals(True)
        self.color_picker.set_color(fmt.color, emit=False)
        self.color_picker.blockSignals(False)
        for widget in widgets:
            widget.blockSignals(False)
        self.width_spin.setEnabled(bool(fmt.wrap))
        self._apply_preview()

    def current_format(self) -> TextFormat:
        """按界面当前状态生成排版参数。"""
        family = self.font_combo.currentData() or ""
        return TextFormat(
            family=str(family),
            pixel_size=float(self.size_spin.value()),
            color=QColor(self.color_picker.color()),
            bold=self.bold_button.isChecked(),
            italic=self.italic_button.isChecked(),
            wrap=self.wrap_check.isChecked(),
            text_width=float(self.width_spin.value()),
            align=str(self.align_combo.currentData() or "left"),
            font_file=self._format.font_file if family == self._format.family else "",
        )

    def result_text(self) -> str:
        return self.editor.toPlainText().strip()

    # ============================================================ 交互
    def _on_font_changed(self, _index: int) -> None:
        self._on_style_changed()

    def _on_color_changed(self, _color) -> None:
        self._on_style_changed()

    def _on_wrap_toggled(self, checked: bool) -> None:
        self.width_spin.setEnabled(bool(checked))
        self._on_style_changed()

    def _on_style_changed(self, *_args) -> None:
        self._apply_preview()

    def _apply_preview(self) -> None:
        """把当前参数实时套到编辑区（编辑区就是预览区）。"""
        fmt = self.current_format()
        self._format = fmt
        scroll = self.editor.verticalScrollBar().value()
        cursor = self.editor.textCursor()
        position = cursor.position()

        self.editor.setFont(fmt.qfont())
        self.editor.setLineWrapMode(
            QTextEdit.LineWrapMode.FixedPixelWidth if fmt.wrap
            else QTextEdit.LineWrapMode.NoWrap)
        if fmt.wrap:
            self.editor.setLineWrapColumnOrWidth(int(fmt.text_width))

        # 对整个文档套用前景色与对齐（selectAll 后再设置，不改变文本内容）
        self.editor.selectAll()
        self.editor.setTextColor(fmt.color)
        self.editor.setAlignment({
            "left": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
        }[fmt.align if fmt.align in ALIGNS else "left"])
        cursor = self.editor.textCursor()
        cursor.setPosition(min(position, len(self.editor.toPlainText())))
        cursor.clearSelection()
        self.editor.setTextCursor(cursor)
        self.editor.verticalScrollBar().setValue(scroll)

    # ============================================================ 快捷调用
    @classmethod
    def ask(cls, parent=None, title: str = "输入文字", text: str = "",
            fmt: TextFormat = None, settings=None):
        """弹窗；返回 ``(文本, TextFormat)``，取消时返回 ``None``。"""
        dialog = cls(parent, title, text, fmt, settings)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        content = dialog.result_text()
        if not content:
            return None
        return content, dialog.current_format()


# 兼容旧名字（早期只有“输入文字”这一个用途）
TextEditDialog = TextDialog
