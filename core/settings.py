"""用户偏好设置（core/settings.py）：基于 QSettings 的轻量封装。

**默认存文件、不写注册表**：配置放在软件目录下的 ``whiteboard.ini``
（便携模式，见 :mod:`core.paths`）。软件目录不可写时自动退回系统应用数据目录。

历史说明：早期版本用的是 ``QSettings(org, app)``，在 Windows 上会写注册表
``HKCU\\Software\\WhiteboardPyside``；现在首次运行时会把里面的旧偏好**一次性迁移**
到 ini 文件里，并清掉旧注册表项（此后系统里不再有本应用的注册表痕迹）。
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

from core import paths
from core.paths import (  # noqa: F401  —— 保持 `from core.settings import ORG_NAME` 可用
    APP_NAME,
    CONFIG_ENV,
    DATA_DIR_ENV,
    ORG_NAME,
)
from core.text_format import TextFormat

DEFAULT_TOOL = "pen"
DEFAULT_COLOR = "#000000"
DEFAULT_THICKNESS = 2.0
DEFAULT_ERASER_SIZE = 16.0
DEFAULT_FONT_SIZE = 16.0
DEFAULT_THEME = "light"
DEFAULT_LINE_STYLE = "solid"       # 线型：实线/虚线/点线/点划线
DEFAULT_SHAPE = "rect"             # 形状工具上次选中的种类
FONTS_KEY = "fonts/imported"       # 导入字体的文件清单（重置设置时保留）


class AppSettings:
    """读写用户偏好；未显式保存时使用默认值。"""

    def __init__(self, migrate: bool = True) -> None:
        self.path = paths.settings_file()
        fresh = not os.path.exists(self.path)
        self._s = QSettings(self.path, QSettings.Format.IniFormat)
        self.migrated_from_registry = False
        if fresh and migrate:
            self._migrate_from_native()

    # ------------------------------------------------------------- 位置信息
    @property
    def location(self) -> str:
        """当前配置文件位置（调试用）。"""
        return self._s.fileName()

    def _migrate_from_native(self) -> None:
        """把旧版本写在注册表里的偏好搬到 ini，然后清掉旧键。

        只搬一次：搬完就删掉注册表项，否则用户删掉 ini 想"恢复默认"时，
        旧值会从注册表"复活"，看起来像重置失败。
        只有确认值真的写进 ini 了才动注册表（目录不可写时保持原样，下次再试）。
        """
        legacy = QSettings(ORG_NAME, APP_NAME)
        keys = legacy.allKeys()
        if not keys:
            return
        for key in keys:
            self._s.setValue(key, legacy.value(key))
        self._s.sync()
        if not all(self._s.contains(key) for key in keys):
            return
        legacy.clear()
        legacy.sync()
        self.migrated_from_registry = True

    def reset(self) -> None:
        """清空全部偏好（颜色、粗细、工具、主题、窗口位置…），恢复出厂默认。

        已导入的字体清单会被保留：字体是用户**导入的资源**（文件还在 fonts/
        目录里），清掉清单只会让它们“消失”，而不是恢复默认外观。
        """
        imported_fonts = self._s.value(FONTS_KEY)
        self._s.clear()
        if imported_fonts is not None:
            self._s.setValue(FONTS_KEY, imported_fonts)
        self._s.sync()

    def sync(self) -> None:
        """立即把偏好写入磁盘（QSettings 平时是延迟落盘的）。"""
        self._s.sync()

    # ------------------------------------------------------------- 原始键值
    def raw_value(self, key: str, default=None):
        return self._s.value(key, default)

    def set_raw_value(self, key: str, value) -> None:
        self._s.setValue(key, value)

    # ------------------------------------------------------------- 工具与样式
    def current_tool(self) -> str:
        return str(self._s.value("tools/current", DEFAULT_TOOL))

    def set_current_tool(self, name: str) -> None:
        self._s.setValue("tools/current", name)

    def color(self) -> QColor:
        return QColor(str(self._s.value("style/color", DEFAULT_COLOR)))

    def set_color(self, color: QColor) -> None:
        self._s.setValue("style/color", color.name(QColor.NameFormat.HexArgb))

    def thickness(self) -> float:
        return float(self._s.value("style/thickness", DEFAULT_THICKNESS))

    def set_thickness(self, value: float) -> None:
        self._s.setValue("style/thickness", float(value))

    def eraser_size(self) -> float:
        return float(self._s.value("style/eraser_size", DEFAULT_ERASER_SIZE))

    def set_eraser_size(self, value: float) -> None:
        self._s.setValue("style/eraser_size", float(value))

    def font_size(self) -> float:
        return float(self._s.value("style/font_size", DEFAULT_FONT_SIZE))

    def set_font_size(self, value: float) -> None:
        self._s.setValue("style/font_size", float(value))

    # ------------------------------------------------------------- 线型 / 形状
    def line_style(self) -> str:
        from canvas.items import LINE_STYLES

        value = str(self._s.value("style/line_style", DEFAULT_LINE_STYLE))
        return value if value in LINE_STYLES else DEFAULT_LINE_STYLE

    def set_line_style(self, name: str) -> None:
        self._s.setValue("style/line_style", str(name))

    def shape_kind(self) -> str:
        from tools.shape_tool import SHAPE_SPECS

        value = str(self._s.value("tools/shape", DEFAULT_SHAPE))
        return value if value in SHAPE_SPECS else DEFAULT_SHAPE

    def set_shape_kind(self, kind: str) -> None:
        self._s.setValue("tools/shape", str(kind))

    # ------------------------------------------------------------- 文字排版
    def text_format(self) -> TextFormat:
        """上次使用的文字排版（字体、字号、颜色、换行…）。"""
        raw = self._s.value("text/format", "")
        if not raw:
            return TextFormat(color=self.color(), pixel_size=DEFAULT_FONT_SIZE)
        try:
            return TextFormat.from_dict(json.loads(str(raw)))
        except (ValueError, TypeError):
            return TextFormat(color=self.color(), pixel_size=DEFAULT_FONT_SIZE)

    def set_text_format(self, fmt: TextFormat) -> None:
        self._s.setValue("text/format", json.dumps(fmt.to_dict(), ensure_ascii=False))

    def theme(self) -> str:
        value = str(self._s.value("app/theme", DEFAULT_THEME))
        return value if value in ("light", "dark") else DEFAULT_THEME

    def set_theme(self, theme: str) -> None:
        self._s.setValue("app/theme", theme)

    # ------------------------------------------------------------- 自动保存
    def autosave_enabled(self) -> bool:
        return self._s.value("autosave/enabled", True, type=bool)

    def set_autosave_enabled(self, enabled: bool) -> None:
        self._s.setValue("autosave/enabled", bool(enabled))

    def autosave_interval_ms(self) -> int:
        return int(self._s.value("autosave/interval_ms", 60_000))

    def set_autosave_interval_ms(self, ms: int) -> None:
        self._s.setValue("autosave/interval_ms", int(ms))

    # ------------------------------------------------------------- 窗口状态
    def window_geometry(self):
        return self._s.value("window/geometry")

    def set_window_geometry(self, value) -> None:
        self._s.setValue("window/geometry", value)

    def window_state(self):
        return self._s.value("window/state")

    def set_window_state(self, value) -> None:
        self._s.setValue("window/state", value)
