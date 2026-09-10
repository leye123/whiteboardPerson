"""字体导入与注册（core/fonts.py）。

Windows 上装了字体才是系统字体，而「导入字体」应该只影响本软件，于是：

1. 把字体文件**复制到软件目录的 ``fonts/``** 里（便携模式，删目录即清理干净）；
2. 用 ``QFontDatabase.addApplicationFont()`` 在本次运行中注册（进程内生效）；
3. 把文件清单记在设置里，下次启动自动重新注册。

于是用户就能在文字对话框的字体列表里看到并使用导入的字体，
而不用往系统里安装字体（不需要管理员权限，也不污染系统）。

导入的字体只对本软件生效；文档里记录字体名与来源文件名，
换台机器打开时若字体缺失，会退回默认字体（见 :func:`ensure_font_available`）。
"""
from __future__ import annotations

import os
import shutil

from PySide6.QtGui import QFontDatabase

from core import paths

FONT_DIR_NAME = "fonts"
# QFontDatabase 支持的字体文件后缀（FreeType 能读的）
SUPPORTED_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".pfb", ".woff", ".woff2")
FONT_FILE_FILTER = ("字体文件 (*.ttf *.otf *.ttc *.otc);;"
                    "所有文件 (*)")
SETTINGS_KEY = "fonts/imported"


def fonts_directory(create: bool = True) -> str:
    """导入字体的存放目录（软件目录下的 fonts/）。"""
    return os.path.join(paths.data_directory(create=create), FONT_DIR_NAME)


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_SUFFIXES


def register_font_file(path: str) -> list:
    """把字体文件注册到当前进程，返回它提供的字体族名列表。"""
    if not path or not os.path.isfile(path):
        return []
    font_id = QFontDatabase.addApplicationFont(path)
    if font_id < 0:
        return []
    return list(QFontDatabase.applicationFontFamilies(font_id))


def installed_families() -> list:
    """当前可用的全部字体族（含已注册的导入字体）。"""
    try:
        return sorted(QFontDatabase.families())
    except Exception:  # noqa: BLE001 —— 没有 QGuiApplication 时不让它炸
        return []


def family_available(family: str) -> bool:
    if not family:
        return False
    try:
        return family in QFontDatabase.families()
    except Exception:  # noqa: BLE001
        return False


def ensure_font_available(family: str, font_file: str = "") -> bool:
    """确保 ``family`` 可用；不可用但记录了 ``font_file`` 时尝试现场注册。

    返回字体是否可用（不可用时调用方应退回默认字体）。
    """
    if family_available(family):
        return True
    if font_file:
        for candidate in (font_file, os.path.join(fonts_directory(False), os.path.basename(font_file))):
            if candidate and os.path.isfile(candidate):
                if family in register_font_file(candidate):
                    return True
    return False


def stored_paths(settings) -> list:
    """设置里记录的已导入字体文件（按加入顺序）。"""
    value = settings.raw_value(SETTINGS_KEY, [])
    if isinstance(value, str):
        value = [value] if value else []
    return [str(v) for v in (value or []) if str(v).strip()]


def set_stored_paths(settings, paths_list) -> None:
    settings.set_raw_value(SETTINGS_KEY, [str(p) for p in paths_list])


def load_imported_fonts(settings) -> list:
    """启动时重新注册之前导入的字体，返回成功注册的文件列表。

    文件被手工删除时会自动从设置里摘掉（否则每次启动都白试一遍）。
    """
    keep, missing, families = [], [], []
    for path in stored_paths(settings):
        names = register_font_file(path)
        if names:
            keep.append(path)
            families.extend(names)
        else:
            missing.append(path)
    if missing:
        set_stored_paths(settings, keep)
        settings.sync()
    return families


def import_font(source_path: str, settings=None) -> tuple:
    """导入一个字体文件。

    返回 ``(family, stored_path, families)``：
    ``family`` 是主字体族名（注册失败时为 ``None``），``stored_path`` 是复制后的路径。
    若该文件已经导入过（同名同内容），不会重复复制。
    """
    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError(f"找不到字体文件：{source_path}")

    target_dir = fonts_directory()
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(source_path))
    source_abs = os.path.abspath(source_path)

    if os.path.normcase(os.path.abspath(target)) != os.path.normcase(source_abs):
        # 已经复制过同一个文件就不再覆盖（避免把用户正在用的文件写到一半）
        if not os.path.exists(target) or os.path.getsize(target) != os.path.getsize(source_abs):
            shutil.copy2(source_abs, target)
    target = os.path.abspath(target)

    families = register_font_file(target)
    if settings is not None:
        known = stored_paths(settings)
        if target not in known:
            known.append(target)
            set_stored_paths(settings, known)
            settings.sync()
    return (families[0] if families else None), target, families
