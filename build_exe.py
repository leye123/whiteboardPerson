"""打包为独立 .exe（build_exe.py）。

默认使用 PyInstaller；加 ``--nuitka`` 改用 Nuitka（生成 C 代码，体积更小、启动更快）。

    python build_exe.py              # PyInstaller -> dist/Whiteboard-1.0.0.exe
    python build_exe.py --nuitka     # Nuitka      -> build/nuitka/main.dist/
    python build_exe.py --onedir     # 目录形式（启动更快，便于排查）

版本号来自 ``core/version.py``，会同时写进：

* 可执行文件的 Windows 版本资源（右键属性→详细信息里能看到）；
* 产物文件名（``Whiteboard-1.0.0.exe`` / ``Whiteboard-1.0.0-win64.zip``）；
* ``scripts/release.py`` 创建的 Release 标签与资产名。

注意：``resources/`` 里的 QSS 是运行时读取的，必须一起打包；
图标是运行时用 QPainter 画的，不需要额外资源。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.version import (  # noqa: E402
    APP_TITLE_EN,
    AUTHOR,
    PACKAGE_BASENAME,
    __version__,
    windows_version_tuple,
)

ENTRY = os.path.join(ROOT, "main.py")
NAME = PACKAGE_BASENAME                       # Whiteboard
VERSION = __version__
OUTPUT_EXE = f"{NAME}-{VERSION}.exe"          # 带上版本号，便于区分产物
BUILD_DIR = os.path.join(ROOT, "build")
SEP = ";" if os.name == "nt" else ":"


def _run(cmd) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True


def _check_tool(module: str, executable: str, package: str) -> bool:
    """确认打包工具装在**当前解释器**里。

    这里不能用 ``shutil.which()`` 当唯一判据：PATH 上可能存在别的 Python
    （例如系统里的 anaconda）安装的 nuitka.exe / pyinstaller.exe，
    检查能通过、但真正的调用是 ``sys.executable -m nuitka``，
    于是报 "No module named nuitka"；就算能跑起来，那个工具属于另一个
    解释器，打包结果也不可信（它看不到当前环境的 PySide6）。
    """
    if _module_available(module):
        return True
    print(f"当前解释器里没有 {module}：")
    print(f"  解释器   = {sys.executable}")
    print(f"  Python   = {sys.version.split()[0]}")
    print(f"  安装命令 = \"{sys.executable}\" -m pip install {package}")
    other = shutil.which(executable)
    if other:
        print(f"  注意：PATH 上找到的是别的环境里的 {executable}：{other}")
        print("        激活环境后直接敲 pip install 可能会装到那个环境去，"
              "请务必用上面的 python -m pip。")
    return False


def _pyinstaller_version_file() -> str:
    """生成 PyInstaller 的版本资源文件（写进 exe 的「详细信息」）。"""
    os.makedirs(BUILD_DIR, exist_ok=True)
    path = os.path.join(BUILD_DIR, "version_info.txt")
    four = windows_version_tuple()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"""# 由 build_exe.py 自动生成，勿手工修改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({four.replace('.', ', ')}),
    prodvers=({four.replace('.', ', ')}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404b0', [
        StringStruct('CompanyName', '{AUTHOR}'),
        StringStruct('FileDescription', '{APP_TITLE_EN} - PySide6 白板应用'),
        StringStruct('FileVersion', '{four}'),
        StringStruct('InternalName', '{NAME}'),
        StringStruct('OriginalFilename', '{OUTPUT_EXE}'),
        StringStruct('ProductName', '{NAME}'),
        StringStruct('ProductVersion', '{four}'),
        StringStruct('LegalCopyright', '{AUTHOR}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""")
    return path


def build_pyinstaller(onedir: bool, clean: bool) -> int:
    if not _check_tool("PyInstaller", "pyinstaller", "pyinstaller"):
        return 1
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--windowed",
        "--name", f"{NAME}-{VERSION}",
        "--version-file", _pyinstaller_version_file(),
        "--add-data", f"resources{SEP}resources",
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
    ]
    if not onedir:
        cmd.append("--onefile")
    if clean:
        cmd.append("--clean")
    cmd.append(ENTRY)
    return _run(cmd)


def build_nuitka(onedir: bool, clean: bool, no_lto: bool = False) -> int:
    if not _check_tool("nuitka", "nuitka", "nuitka"):
        return 1
    four = windows_version_tuple()
    cmd = [
        sys.executable, "-m", "nuitka",
        "--enable-plugin=pyside6",
        f"--include-data-dir=resources=resources",
        f"--output-dir={os.path.join(BUILD_DIR, 'nuitka')}",
        f"--output-filename={NAME}.exe",
        "--windows-console-mode=disable",
        "--assume-yes-for-downloads",
        # 写进 exe 的版本资源：右键属性 → 详细信息 里能看到
        f"--company-name={AUTHOR}",
        f"--product-name={NAME}",
        f"--file-description={APP_TITLE_EN} - PySide6 whiteboard application",
        f"--file-version={four}",
        f"--product-version={four}",
        f"--copyright={AUTHOR}",
    ]
    if no_lto:
        # LTO 是打包里最慢的一环（PySide6 工程可能要几十分钟），
        # 调试/日常打包时关掉能快很多，代价是生成代码略大略慢
        cmd.append("--lto=no")
    if not onedir:
        cmd.append("--onefile")
    else:
        cmd.append("--standalone")
    if clean:
        cmd.append("--remove-output")
    cmd.append(ENTRY)
    return _run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="把白板打包成 .exe")
    parser.add_argument("--nuitka", action="store_true", help="使用 Nuitka 而不是 PyInstaller")
    parser.add_argument("--onedir", action="store_true", help="目录形式（非单文件）")
    parser.add_argument("--clean", action="store_true", help="打包前清理缓存")
    parser.add_argument("--no-lto", action="store_true",
                        help="Nuitka 跳过 LTO（打包快很多，产物略大）")
    args = parser.parse_args()

    print(f"解释器: {sys.executable}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"版本号: {NAME} {VERSION}（资源版本 {windows_version_tuple()}）")
    try:
        import PySide6

        print(f"PySide6: {PySide6.__version__}")
    except ImportError:
        print("PySide6: 未安装（当前解释器）")

    if args.nuitka:
        code = build_nuitka(args.onedir, args.clean, args.no_lto)
    else:
        code = build_pyinstaller(args.onedir, args.clean)

    if code == 0:
        print("\n打包完成：")
        print(f"  PyInstaller: dist/{NAME}-{VERSION}/{NAME}.exe")
        print(f"  Nuitka:      build/nuitka/main.dist/{NAME}.exe")
        print("\n建议先跑一次路径自检，确认配置会写在 exe 旁边而不是临时目录：")
        print(f"  {NAME}.exe --paths")
        print("\n再打开发布包（zip + GitHub Release）：")
        print("  python scripts/release.py --build")
    return code


if __name__ == "__main__":
    sys.exit(main())
