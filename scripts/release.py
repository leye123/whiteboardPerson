"""一键发布到 GitHub Release（scripts/release.py）。

流程：

1. 调用 ``build_exe.py`` 打包（``--no-build`` 可跳过，复用已有产物）；
2. 把构建产物目录压缩成 ``dist/Whiteboard-<版本>-win64.zip``；
3. 本地打上 ``v<版本>`` 标签；
4. 用 GitHub CLI（``gh release create``）创建 Release 并上传压缩包。

版本号取自 ``core/version.py``，所以产物名、标签、Release 标题三者永远一致。

用法：
    python scripts/release.py                 # 构建 + 打包 + 建标签 + 上传
    python scripts/release.py --no-build      # 复用已有产物
    python scripts/release.py --dry-run       # 只打印步骤，不执行
    python scripts/release.py --draft         # 创建草稿 Release
    python scripts/release.py --source dist   # 指定待压缩的产物目录
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

from core.version import APP_TITLE, AUTHOR, PACKAGE_BASENAME, __version__, asset_name, version_tag  # noqa: E402

DIST_DIR = os.path.join(ROOT, "dist")
# 打包产物的候选目录（按顺序找）：Nuitka --onedir / PyInstaller --onedir / 单文件
CANDIDATE_OUTPUTS = (
    os.path.join(ROOT, "build", "nuitka", "main.dist"),
    os.path.join(ROOT, "dist", f"{PACKAGE_BASENAME}-{__version__}"),
    os.path.join(ROOT, "build", "nuitka"),
    os.path.join(ROOT, "dist"),
)
EXE_NAME = f"{PACKAGE_BASENAME}.exe"


def run(cmd: list, dry_run: bool = False, check: bool = True, capture: bool = False) -> int:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    if dry_run:
        return 0
    if capture:
        return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    return subprocess.call(cmd, cwd=ROOT)


def find_output(source: str = None) -> str:
    """找到包含 exe 的产物目录。"""
    if source:
        if not os.path.isdir(source):
            raise SystemExit(f"指定的产物目录不存在：{source}")
        return os.path.abspath(source)
    for candidate in CANDIDATE_OUTPUTS:
        if os.path.isdir(candidate) and find_exe(candidate):
            return candidate
    raise SystemExit(
        "没找到打包产物，请先运行：python build_exe.py --nuitka --onedir\n"
        f"（已检查：{', '.join(CANDIDATE_OUTPUTS)}）")


def find_exe(directory: str) -> str:
    direct = os.path.join(directory, EXE_NAME)
    if os.path.isfile(direct):
        return direct
    for name in os.listdir(directory):
        if name.lower() == EXE_NAME.lower():
            return os.path.join(directory, name)
    return ""


def make_archive(source_dir: str, dry_run: bool = False) -> str:
    """把产物目录打成 zip（保持顶层目录，解压即得一个可直接运行的文件夹）。"""
    os.makedirs(DIST_DIR, exist_ok=True)
    target = os.path.join(DIST_DIR, asset_name("win64.zip"))
    if dry_run:
        print(f"  将压缩 {source_dir} -> {target}")
        return target
    top = os.path.basename(os.path.normpath(source_dir))
    root_exe = find_exe(source_dir)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for base, _dirs, files in os.walk(source_dir):
            for name in files:
                full = os.path.join(base, name)
                # 顶层 exe 放进 zip 根目录，其余保持原结构
                if os.path.normcase(full) == os.path.normcase(root_exe):
                    arcname = os.path.relpath(full, source_dir)
                else:
                    arcname = os.path.join(top, os.path.relpath(full, source_dir))
                archive.write(full, arcname)
    size_mb = os.path.getsize(target) / 1048576
    print(f"  已生成 {target}（{size_mb:.1f} MB）")
    return target


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
    parser.add_argument("--source", default=None, help="待打包的产物目录")
    parser.add_argument("--notes-file", default=None, help="Release 说明文件（markdown）")
    args = parser.parse_args()

    tag = version_tag()
    print(f"=== 发布 {APP_TITLE} {__version__} ===")
    print(f"标签   : {tag}")
    print(f"资产名 : {asset_name('win64.zip')}")
    print(f"仓库   : {git('remote', 'get-url', 'origin', dry_run=args.dry_run) or '(未设置 origin)'}")
    print()

    if not args.no_build:
        print("[1/4] 打包")
        code = run([sys.executable, os.path.join(ROOT, "build_exe.py"), "--nuitka", "--onedir"],
                   dry_run=args.dry_run)
        if code != 0:
            return code
    else:
        print("[1/4] 跳过打包（--no-build）")

    print("[2/4] 压缩发布包")
    source_dir = find_output(args.source) if not args.dry_run else (args.source or CANDIDATE_OUTPUTS[0])
    archive = make_archive(source_dir, dry_run=args.dry_run)

    print("[3/4] 打标签")
    existing = git("tag", "--list", tag, dry_run=args.dry_run)
    if existing:
        print(f"  标签 {tag} 已存在，跳过")
    else:
        git("tag", "-a", tag, "-m", f"{APP_TITLE} {tag}", dry_run=args.dry_run)
        print(f"  已创建标签 {tag}（推送：git push origin {tag}）")

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
