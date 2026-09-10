"""程序位置与数据文件位置（core/paths.py）。

默认是**便携模式**：偏好设置（``whiteboard.ini``）和自动备份（``autosave.wbd``）
都放在软件目录里，删掉目录即彻底清理，不往注册表写任何东西。

优先级：

1. 环境变量 ``WHITEBOARD_CONFIG``：指定配置文件路径（测试隔离用它）；
2. 环境变量 ``WHITEBOARD_DATA_DIR``：指定数据目录（自动备份放这里）；
3. 软件目录（源码运行 = 项目根目录，打包后 = exe 所在目录）—— 可写就用它；
4. 软件目录不可写（装在 Program Files、只读 U 盘等）时退回系统的应用数据目录。

**打包后的坑**：单文件打包时程序会被解包到临时目录再运行（PyInstaller 的
``sys._MEIPASS``、Nuitka onefile 的 ``%TEMP%\\onefile_xxxx``），
Nuitka 还**不设置** ``sys.frozen``（它在模块里注入 ``__compiled__``）。
所以这里同时参考 ``sys.executable``、``sys.argv[0]`` 与 ``__compiled__``，
并把临时解包目录排除掉 —— 否则配置会写进临时目录，退出即丢失，
表现为「打包后设置没保存」。

``is_portable()`` 可以查询当前实际落在哪种情况。
"""
from __future__ import annotations

import os
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QStandardPaths

ORG_NAME = "WhiteboardPyside"
APP_NAME = "Whiteboard"

SETTINGS_FILENAME = "whiteboard.ini"
AUTOSAVE_FILENAME = "autosave.wbd"

CONFIG_ENV = "WHITEBOARD_CONFIG"        # 指向配置文件（.ini）
DATA_DIR_ENV = "WHITEBOARD_DATA_DIR"    # 指向数据目录

# QStandardPaths 只有在设好组织名/应用名之后才会拼出应用专属目录，
# 否则会返回整个 AppData\Roaming（清理脚本曾因此差点删错目录）。
QCoreApplication.setOrganizationName(ORG_NAME)
QCoreApplication.setApplicationName(APP_NAME)


def is_packaged() -> bool:
    """是否运行在打包后的可执行文件里。

    * PyInstaller / cx_Freeze：``sys.frozen`` 为 True；
    * Nuitka：它不设置 ``sys.frozen``，而是在每个编译过的模块里注入
      ``__compiled__``（官方推荐的检测方式）。
    """
    if "__compiled__" in globals():
        return True
    return bool(getattr(sys, "frozen", False))


def is_temporary_directory(directory: str) -> bool:
    """是否为「临时解包目录」。

    打包成单文件时，程序会被解包到临时目录再运行：
    PyInstaller 用 ``sys._MEIPASS``，Nuitka onefile 用 ``%TEMP%\\onefile_xxxx``。
    这些目录退出即被删除，绝不能用来存配置（否则表现为「设置没保存」）。
    """
    if not directory:
        return True
    normalized = os.path.normcase(os.path.abspath(directory))
    temp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    if normalized == temp_root or normalized.startswith(temp_root + os.sep):
        return True
    name = os.path.basename(normalized)
    return name.startswith(("onefile_", "_MEI", "~", "tmp"))


def _program_directories() -> list:
    """程序可能所在的目录，按优先级排列（打包后不含临时解包目录）。"""
    candidates = []
    if is_packaged():
        # Nuitka onefile 会在子进程里放这个环境变量，指向真正被双击的 exe
        onefile_binary = os.environ.get("NUITKA_ONEFILE_BINARY")
        if onefile_binary:
            candidates.append(os.path.dirname(os.path.abspath(onefile_binary)))
        # exe 所在目录优先；onefile 下 sys.executable 可能指向临时解包目录，
        # 此时用 sys.argv[0]（启动时用户点的那个 exe）兜底。
        candidates.append(os.path.dirname(os.path.abspath(sys.executable or "")))
        if sys.argv and sys.argv[0]:
            candidates.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    else:
        # 源码运行：本文件在 <项目>/core/paths.py
        candidates.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    unique = []
    for path in candidates:
        if path and path not in unique:
            unique.append(path)
    return unique


def app_directory() -> str:
    """软件所在目录（打包后 = exe 所在目录；源码运行 = 项目根目录）。"""
    for directory in _program_directories():
        if not is_temporary_directory(directory) and os.path.isdir(directory):
            return directory
    return _program_directories()[0]


def is_writable(directory: str) -> bool:
    """真的写一个临时文件来验证可写性（Windows 上 ``os.access`` 不可靠）。"""
    if not directory or not os.path.isdir(directory):
        return False
    try:
        handle, path = tempfile.mkstemp(prefix=".wb_write_", dir=directory)
    except OSError:
        return False
    try:
        os.close(handle)
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        return False
    return True


def system_data_directory() -> str:
    """系统分配给本应用的数据目录（软件目录不可写时用）。"""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = os.path.join(os.path.expanduser("~"), f".{APP_NAME.lower()}")
    return base


def data_directory(create: bool = True) -> str:
    """数据文件（配置文件、自动备份）放在哪个目录。

    依次尝试：环境变量 → 程序所在目录（可写且不是临时解包目录）→ 系统数据目录。
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        if create:
            os.makedirs(override, mode=0o777, exist_ok=True)
        return os.path.abspath(override)

    for directory in _program_directories():
        if is_temporary_directory(directory):
            continue
        if os.path.isdir(directory) and is_writable(directory):
            return directory

    fallback = system_data_directory()
    if create:
        os.makedirs(fallback, exist_ok=True)
    return fallback


def is_portable() -> bool:
    """数据是否落在软件目录里（便携模式）。"""
    try:
        return os.path.normcase(data_directory(create=False)) == \
            os.path.normcase(app_directory())
    except OSError:
        return False


def settings_file() -> str:
    """配置文件（.ini）的完整路径。"""
    override = os.environ.get(CONFIG_ENV)
    if override:
        parent = os.path.dirname(os.path.abspath(override))
        os.makedirs(parent, exist_ok=True)
        return os.path.abspath(override)
    return os.path.join(data_directory(), SETTINGS_FILENAME)


def autosave_file() -> str:
    """自动备份文件的完整路径。"""
    return os.path.join(data_directory(), AUTOSAVE_FILENAME)
