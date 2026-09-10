"""清理本应用留在系统里的痕迹（scripts/cleanup.py）。

默认是便携模式：配置（``whiteboard.ini``）和自动备份（``autosave.wbd``）都放在
软件目录里，删掉目录就干净了。但下面这些「历史遗留」需要单独处理：

* **旧版注册表偏好**：``HKCU\\Software\\WhiteboardPyside``
  （新版已不再写入；首次运行新版会把里面的值迁移到 ini，注册表项本身保留）
* **旧版应用数据目录**：``%APPDATA%\\WhiteboardPyside\\...``
  （旧版把自动备份放在那里，其中有 ``autosave.wbd``）
* **当前配置文件 / 当前自动备份文件**（在软件目录或 WHITEBOARD_DATA_DIR 下）
* **导入的字体**：``fonts/``（用户通过「导入字体…」复制进来的 .ttf/.otf）

用法：
    python scripts/cleanup.py                 # 列出并询问后清理
    python scripts/cleanup.py --dry-run       # 只列出会删什么
    python scripts/cleanup.py -y              # 不询问
    python scripts/cleanup.py --settings-only # 只清偏好，不动自动备份
    python scripts/cleanup.py --legacy-only   # 只清旧版遗留（注册表 + AppData）
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from PySide6.QtCore import QSettings  # noqa: E402

from core import paths  # noqa: E402
from persistence import file_handler as fh  # noqa: E402

LEGACY_DIR_NAME = "我的白板"      # 早期版本用应用显示名当目录名


def _is_registry_path(location: str) -> bool:
    """Qt 在 Windows 上返回的是 ``\\HKEY_CURRENT_USER\\Software\\...``。"""
    return location.lstrip("\\").upper().startswith("HKEY")


# ------------------------------------------------------------------ 偏好设置


def settings_location() -> str:
    return paths.settings_file()


def legacy_registry_keys() -> list:
    """旧版写在注册表里的偏好键（新版不再使用，仅用于清理）。"""
    legacy = QSettings(paths.ORG_NAME, paths.APP_NAME)
    location = legacy.fileName()
    if not _is_registry_path(location):
        return []               # 非 Windows 或用的是文件，没什么可清的
    return list(legacy.allKeys())


def remove_settings(dry_run: bool = False) -> str:
    """删除配置文件（清空偏好）。"""
    path = settings_location()
    if not os.path.exists(path):
        return f"配置文件不存在，无需清理：{path}"
    if dry_run:
        return f"将删除配置文件：{path}"
    os.remove(path)
    return f"已删除配置文件：{path}"


def remove_legacy_registry(dry_run: bool = False) -> str:
    """清掉旧版写在注册表里的偏好。"""
    keys = legacy_registry_keys()
    if not keys:
        return "旧版注册表项不存在或为空，无需清理"
    location = QSettings(paths.ORG_NAME, paths.APP_NAME).fileName()
    if dry_run:
        return f"将清空旧版注册表项 {location}（{len(keys)} 项）"
    legacy = QSettings(paths.ORG_NAME, paths.APP_NAME)
    legacy.clear()
    legacy.sync()
    if legacy.status() != QSettings.Status.NoError:
        return (f"清空注册表失败（{legacy.status()}）：{location}\n"
                f"    请在普通终端里重跑，或手动删除\n"
                f"    HKEY_CURRENT_USER\\Software\\{paths.ORG_NAME}")
    return f"已清空旧版注册表项：{location}"


# ------------------------------------------------------------------ 数据文件


def legacy_app_data_dirs() -> list:
    """旧版的应用数据目录（只返回最后一段确实属于本应用的目录）。

    没有先设置组织名/应用名时 ``AppDataLocation`` 会算成整个
    ``AppData\\Roaming``，所以这里额外校验目录名，避免误删。
    """
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        return []
    candidates = [base, os.path.join(os.path.dirname(base), LEGACY_DIR_NAME)]
    return [p for p in candidates
            if os.path.isdir(p)
            and os.path.basename(os.path.normpath(p)) in (paths.APP_NAME, LEGACY_DIR_NAME)]


def imported_font_dir() -> str:
    """用户导入的字体存放目录（软件目录/数据目录下的 fonts/）。"""
    from core import fonts

    return fonts.fonts_directory(create=False)


def remove_imported_fonts(dry_run: bool = False) -> str:
    """删除导入的字体目录。

    字体文件是用户自己导入的资源，只有在做「完整清理」时才处理；
    双重保护：只允许删除目录名恰好是 ``fonts`` 的那一层。
    """
    from core import fonts

    directory = imported_font_dir()
    if not os.path.isdir(directory):
        return f"没有导入的字体目录：{directory}"
    if os.path.basename(os.path.normpath(directory)) != fonts.FONT_DIR_NAME:
        return f"跳过（目录名不是 {fonts.FONT_DIR_NAME}）：{directory}"
    count = len(os.listdir(directory))
    if dry_run:
        return f"将删除导入的字体目录（{count} 个文件）：{directory}"
    shutil.rmtree(directory, ignore_errors=True)
    return f"已删除导入的字体目录（{count} 个文件）：{directory}"


def remove_app_data(dry_run: bool = False, autosave: str = None) -> list:
    results = []
    autosave = autosave or fh.autosave_path()
    if os.path.exists(autosave):
        if dry_run:
            results.append(f"将删除自动备份：{autosave}")
        else:
            os.remove(autosave)
            results.append(f"已删除自动备份：{autosave}")
    else:
        results.append(f"没有自动备份文件：{autosave}")

    program_dir = os.path.normcase(paths.app_directory())
    for directory in legacy_app_data_dirs():
        if os.path.normcase(directory) == program_dir:
            continue                    # 便携模式下这个目录就是软件目录，绝不能删
        # 旧版把自动备份放在这里，先删掉它，目录才有可能变成空的
        legacy_autosave = os.path.join(directory, paths.AUTOSAVE_FILENAME)
        if os.path.exists(legacy_autosave):
            if dry_run:
                results.append(f"将删除旧版自动备份：{legacy_autosave}")
            else:
                os.remove(legacy_autosave)
                results.append(f"已删除旧版自动备份：{legacy_autosave}")
        if not os.path.isdir(directory):
            continue
        entries = os.listdir(directory)
        if entries:
            results.append(f"保留非空目录（内有 {len(entries)} 个条目）：{directory}")
            continue
        if dry_run:
            results.append(f"将删除空目录：{directory}")
        else:
            shutil.rmtree(directory, ignore_errors=True)
            results.append(f"已删除空目录：{directory}")
    return results


# ------------------------------------------------------------------ 入口


def main() -> int:
    parser = argparse.ArgumentParser(description="清理白板应用留下的配置与数据文件")
    parser.add_argument("--dry-run", action="store_true", help="只列出会删什么")
    parser.add_argument("-y", "--yes", action="store_true", help="不询问直接清理")
    parser.add_argument("--settings-only", action="store_true", help="只清偏好")
    parser.add_argument("--legacy-only", action="store_true", help="只清旧版遗留（注册表 + AppData）")
    args = parser.parse_args()

    print(f"软件目录：{paths.app_directory()}"
          f"（{'便携模式' if paths.is_portable() else '数据放在系统目录'}）")
    print("将要处理：")
    if not args.legacy_only:
        print(f"  · 配置文件：{settings_location()}")
    if not (args.settings_only or args.legacy_only):
        print(f"  · 自动备份：{fh.autosave_path()}")
        print(f"  · 导入的字体：{imported_font_dir()}")
    legacy_keys = legacy_registry_keys()
    if legacy_keys:
        # 注意：f-string 的表达式里不能出现反斜杠（Python 3.10），先算好
        registry_label = (f"HKEY_CURRENT_USER\\Software\\{paths.ORG_NAME}"
                          f"（{len(legacy_keys)} 项）")
    else:
        registry_label = "无"
    print(f"  · 旧版注册表项：{registry_label}")
    if not (args.settings_only or args.legacy_only):
        for directory in legacy_app_data_dirs():
            print(f"  · 旧版数据目录：{directory}")
    print()

    if not args.dry_run and not args.yes:
        answer = input("确认清理以上内容？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消。")
            return 0

    if not args.legacy_only:
        print(remove_settings(dry_run=args.dry_run))
    if not args.settings_only:
        print(remove_legacy_registry(dry_run=args.dry_run))
        for line in remove_app_data(dry_run=args.dry_run):
            print(line)
        print(remove_imported_fonts(dry_run=args.dry_run))

    if not args.dry_run:
        print("\n完成。软件目录（含 .venv、wheels、.test_tmp）直接删除即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
