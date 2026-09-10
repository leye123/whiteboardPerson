"""一键发布到 GitHub Release（scripts/release.py）。

流程：

1. 调用 ``build_exe.py`` 打包（``--no-build`` 可跳过，复用已有产物）；
2. 把单文件产物复制成 ``dist/Whiteboard-<版本>-win64.exe``；
3. 本地打上 ``v<版本>`` 标签；
4. 用 GitHub CLI（``gh release create``）创建 Release 并上传。

版本号取自 ``core/version.py``，所以产物名、标签、Release 标题三者永远一致。

**发布只用单文件（onefile）**：目录版（``--onedir``）必须在 exe 旁边带着
``_internal`` 目录才能启动，一旦这两者分家（例如把 exe 单独放进 zip 根目录、
其余文件塞进子目录）运行时就报 DLL 缺失。单文件版没这个问题，所以发布资产
只有 ``.exe`` 一个；目录版只在本地排查时用，压缩脚本
:func:`make_debug_zip` 会保证「所有文件都在同一个顶层目录里」。

用法：
    python scripts/release.py                 # 构建 + 生成资产 + 打标签 + 上传
    python scripts/release.py --no-build      # 复用已有产物
    python scripts/release.py --dry-run       # 只打印步骤，不执行
    python scripts/release.py --draft         # 创建草稿 Release
    python scripts/release.py --source dist\\Whiteboard-1.2.0.exe   # 指定产物
    python scripts/release.py --notes-file RELEASE_NOTES.md

前提：安装 GitHub CLI 并登录（``gh auth login``）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import paths  # noqa: E402
from core.version import APP_TITLE, AUTHOR, PACKAGE_BASENAME, __version__, asset_name, version_tag  # noqa: E402

DIST_DIR = os.path.join(ROOT, "dist")
# 单文件产物（发布资产）：带版本号的优先，其次是打包器默认名
CANDIDATE_OUTPUTS = (
    os.path.join(ROOT, "dist", f"{PACKAGE_BASENAME}-{__version__}.exe"),
    os.path.join(ROOT, "dist", f"{PACKAGE_BASENAME}.exe"),
)
EXE_NAME = f"{PACKAGE_BASENAME}-{__version__}.exe"
EXE_NAME_PLAIN = f"{PACKAGE_BASENAME}.exe"


def run(cmd: list, dry_run: bool = False, check: bool = True, capture: bool = False) -> int:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    if dry_run:
        return 0
    if capture:
        return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    return subprocess.call(cmd, cwd=ROOT)


def find_exe(directory: str) -> str:
    """在产物目录里找主程序（PyInstaller/Nuitka 会命名成 <name>.exe）。"""
    for candidate in (EXE_NAME, EXE_NAME_PLAIN):
        path = os.path.join(directory, candidate)
        if os.path.isfile(path):
            return path
    for name in os.listdir(directory):
        lower = name.lower()
        if lower.startswith(PACKAGE_BASENAME.lower()) and lower.endswith(".exe"):
            return os.path.join(directory, name)
    return ""


def find_artifact(source: str = None) -> str:
    """找到待发布的**单文件** exe。"""
    if source:
        if not os.path.exists(source):
            raise SystemExit(f"指定的产物不存在：{source}")
        return os.path.abspath(source)
    for candidate in CANDIDATE_OUTPUTS:
        if os.path.isfile(candidate):
            return candidate
    onedir = os.path.join(ROOT, "dist", f"{PACKAGE_BASENAME}-{__version__}")
    hint = ""
    if os.path.isdir(onedir):
        hint = ("\n（检测到目录版产物，但发布只用单文件：目录版缺 _internal 会报 DLL 缺失。"
                f"\n  它只用于本地调试，压缩请用 --source \"{onedir}\"）")
    raise SystemExit(
        "没找到单文件产物，请先运行：python build_exe.py"
        f"\n（已检查：{', '.join(CANDIDATE_OUTPUTS)}）{hint}")


def make_asset(source: str, dry_run: bool = False) -> str:
    """把单文件产物变成发布资产：``dist/Whiteboard-<版本>-win64.exe``。

    传进来的是目录（``--onedir`` 的产物）时，只做「本地排查用的 zip」，
    不会当成发布资产 —— 发布一律用单文件版。
    """
    os.makedirs(DIST_DIR, exist_ok=True)

    if os.path.isdir(source):
        print("  注意：目录版不作为发布资产（发布用单文件 python build_exe.py）")
        return make_debug_zip(source, dry_run)

    target = os.path.join(DIST_DIR, asset_name("win64.exe"))
    if dry_run:
        print(f"  将复制 {source} -> {target}")
        return target
    shutil.copy2(source, target)
    print(f"  已生成 {target}（{os.path.getsize(target) / 1048576:.1f} MB）")
    return target


def make_debug_zip(source: str, dry_run: bool = False) -> str:
    """把目录版压成 zip，**仅供本地排查**。

    关键：所有文件必须放在**同一个顶层目录**里。PyInstaller 的目录版 exe 会到
    自己旁边找 ``_internal``；如果把 exe 单独放到 zip 根目录、其余文件塞进子目录，
    解压后运行就会报 DLL 缺失（发布过的旧包正是这么坏的）。
    """
    target = os.path.join(DIST_DIR, f"{PACKAGE_BASENAME}-{__version__}-debug.zip")
    if dry_run:
        print(f"  将压缩 {source} -> {target}")
        return target
    top = os.path.basename(os.path.normpath(source))
    # 运行时产生的文件不该进压缩包（验证产物时程序会在 exe 旁边写配置）
    excluded = {paths.SETTINGS_FILENAME.lower(), paths.AUTOSAVE_FILENAME.lower()}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for base, _dirs, files in os.walk(source):
            for name in files:
                if name.lower() in excluded:
                    continue
                full = os.path.join(base, name)
                archive.write(full, os.path.join(top, os.path.relpath(full, source)))
    print(f"  已生成 {target}（{os.path.getsize(target) / 1048576:.1f} MB，"
          f"解压后所有文件都在 {top}/ 里）")
    return target


def verify_icon(source: str, dry_run: bool = False) -> bool:
    """发布前确认 exe 里真的嵌了图标。

    只打包不传 ``--icon`` 时程序照常运行，只有资源管理器/任务栏上显示默认图标，
    很容易带着发布出去（实际就发生过）。这里在压缩之前顺手验一遍。
    """
    if dry_run:
        print("  将核对 exe 图标")
        return True
    exe = find_exe(source) if os.path.isdir(source) else source
    script = os.path.join(ROOT, "scripts", "check_exe_icon.py")
    if not exe or not os.path.exists(script):
        return True
    if subprocess.call([sys.executable, script, exe], cwd=ROOT) != 0:
        print("  警告：exe 图标检查未通过，发布包里的程序会显示默认图标")
        return False
    return True


def git(*args: str, dry_run: bool = False) -> str:
    if dry_run:
        print("$ git", " ".join(args))
        return ""
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def has_gh() -> bool:
    return shutil.which("gh") is not None


def gh_authenticated() -> bool:
    if not has_gh():
        return False
    result = subprocess.run(["gh", "auth", "status"], text=True, capture_output=True)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"发布 {APP_TITLE} {__version__}")
    parser.add_argument("--no-build", action="store_true", help="跳过打包，复用已有产物")
    parser.add_argument("--dry-run", action="store_true", help="只打印步骤")
    parser.add_argument("--draft", action="store_true", help="创建草稿 Release")
    parser.add_argument("--zip-only", action="store_true",
                        help="只打包成 zip 并打标签，不碰 GitHub")
    parser.add_argument("--source", default=None, help="待打包的产物目录")
    parser.add_argument("--notes-file", default=None, help="Release 说明文件（markdown）")
    args = parser.parse_args()

    tag = version_tag()
    print(f"=== 发布 {APP_TITLE} {__version__} ===")
    print(f"标签   : {tag}")
    print(f"资产名 : {asset_name('win64.exe')}（发布只用单文件版）")
    print(f"仓库   : {git('remote', 'get-url', 'origin', dry_run=args.dry_run) or '(未设置 origin)'}")
    print()

    if not args.no_build:
        print("[1/4] 打包（单文件）")
        code = run([sys.executable, os.path.join(ROOT, "build_exe.py")],
                   dry_run=args.dry_run)
        if code != 0:
            return code
    else:
        print("[1/4] 跳过打包（--no-build）")

    print("[2/4] 生成发布资产")
    source = args.source or (find_artifact() if not args.dry_run else CANDIDATE_OUTPUTS[0])
    verify_icon(source, dry_run=args.dry_run)
    archive = make_asset(source, dry_run=args.dry_run)

    print("[3/4] 打标签")
    existing = git("tag", "--list", tag, dry_run=args.dry_run)
    if existing:
        print(f"  标签 {tag} 已存在，跳过")
    else:
        git("tag", "-a", tag, "-m", f"{APP_TITLE} {tag}", dry_run=args.dry_run)
        print(f"  已创建标签 {tag}（推送：git push origin {tag}）")

    if args.zip_only:
        print("\n--zip-only：已生成发布资产，未创建 GitHub Release。")
        print(f"  发布资产：{archive}")
        print(f"  之后可执行：gh release create {tag} \"{archive}\" --title \"{tag}\"")
        return 0

    print("[4/4] 创建 GitHub Release 并上传")
    if not has_gh():
        print("  未找到 GitHub CLI（gh），请先安装并登录：https://cli.github.com/")
        print("  然后手动执行：")
        print(f'    gh release create {tag} "{archive}" --title "{tag}" '
              f'--notes "发布 {APP_TITLE} {__version__}"')
        return 1
    if not args.dry_run and not gh_authenticated():
        print("  gh 未登录，请先运行：gh auth login")
        return 1

    notes = f"{APP_TITLE} {__version__}\n\n由 {AUTHOR} 构建。"
    if args.notes_file and os.path.exists(args.notes_file):
        with open(args.notes_file, encoding="utf-8") as handle:
            notes = handle.read()

    cmd = ["gh", "release", "create", tag, archive, "--title", tag, "--notes", notes]
    if args.draft:
        cmd.append("--draft")
    code = run(cmd, dry_run=args.dry_run)
    if code == 0:
        print(f"\n发布完成：{git('remote', 'get-url', 'origin', dry_run=args.dry_run)}"
              f"/releases/tag/{tag}")
    return code


if __name__ == "__main__":
    sys.exit(main())
