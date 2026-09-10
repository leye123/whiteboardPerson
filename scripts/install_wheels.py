"""在受限/离线环境下安装 PySide6 轮子（scripts/install_wheels.py）。

为什么需要它：某些受限环境里 ``pip install`` 无法在临时目录创建文件
（Python 的 ``tempfile.mkdtemp`` 目录会被拒绝写入），导致 pip 解包失败。
本脚本只用 ``urllib`` 下载 wheel、用 ``zipfile`` 直接解压到 site-packages，
完全绕开临时目录。

用法：
    python scripts/install_wheels.py                 # 安装到 .venv
    python scripts/install_wheels.py --site-packages <路径>
    python scripts/install_wheels.py --index https://pypi.org/pypi   # 指定索引
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SITE = os.path.join(ROOT, ".venv", "Lib", "site-packages")
WHEEL_DIR = os.path.join(ROOT, "wheels")

PACKAGES = ("shiboken6", "PySide6_Essentials", "PySide6_Addons", "PySide6")
PY_MAJOR, PY_MINOR = 3, 10
PLATFORM_TAG = "win_amd64"

# 依次尝试的 JSON 索引（国内镜像通常更快更稳）
DEFAULT_INDEXES = (
    "https://pypi.tuna.tsinghua.edu.cn/pypi",
    "https://pypi.org/pypi",
)
CHUNK = 1024 * 256
RETRIES = 6
SOCKET_TIMEOUT = 30


def _version_key(text: str):
    parts = []
    for chunk in text.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _python_ok(requires: str) -> bool:
    """极简 requires_python 判断（只处理常见的 >= / < 组合）。"""
    if not requires:
        return True
    ok = True
    for clause in requires.replace(" ", "").split(","):
        for op in (">=", "<=", "==", ">", "<"):
            if clause.startswith(op):
                want = _version_key(clause[len(op):])
                have = (PY_MAJOR, PY_MINOR)
                have = have + (0,) * (len(want) - len(have))
                want = want + (0,) * (len(have) - len(want))
                if op == ">=" and not have >= want:
                    ok = False
                elif op == ">" and not have > want:
                    ok = False
                elif op == "<=" and not have <= want:
                    ok = False
                elif op == "<" and not have < want:
                    ok = False
                elif op == "==" and not have == want:
                    ok = False
                break
    return ok


def _fetch_json(indexes, package: str):
    """依次从各个索引抓取 JSON；返回 [(rank, meta), ...]（失败的索引跳过）。"""
    metas = []
    for rank, base in enumerate(indexes):
        url = f"{base}/{package}/json"
        try:
            with urllib.request.urlopen(url, timeout=SOCKET_TIMEOUT) as resp:
                metas.append((rank, json.load(resp)))
        except Exception as exc:  # noqa: BLE001
            print(f"  索引不可用 {url}: {exc}")
    if not metas:
        raise SystemExit(f"无法获取 {package} 的索引信息")
    return metas


def _pick_wheel(indexes, package: str, pinned: str = None):
    """返回 (version, filename, url)。

    多个索引的结果会**合并**（而不是“第一个成功就返回”）：某些镜像的 JSON
    快照可能落后，合并后仍能取到官方索引里的最新版本；
    同版本同时存在时优先使用排在前面的索引（镜像）的文件地址，下载更快。
    """
    candidates = []
    for rank, meta in _fetch_json(indexes, package):
        for version, files in meta.get("releases", {}).items():
            if pinned and version != pinned:
                continue
            for info in files:
                name = info.get("filename", "")
                if info.get("packagetype") != "bdist_wheel":
                    continue
                if not name.endswith(".whl"):
                    continue
                # 二进制包带平台标签；纯 Python 包是 py3-none-any，两者都要接受
                if PLATFORM_TAG not in name and "none-any" not in name:
                    continue
                if not _python_ok(info.get("requires_python", "")):
                    continue
                candidates.append((_version_key(version), version, name,
                                   info["url"], rank))
    if not candidates:
        raise SystemExit(f"找不到 {package} 可用的 {PLATFORM_TAG} 轮子"
                         + (f"（版本 {pinned}）" if pinned else ""))
    # 先按版本从高到低，再按索引优先级（镜像优先）
    candidates.sort(key=lambda c: (c[0], -c[4]), reverse=True)
    _key, version, name, url, _rank = candidates[0]
    return version, name, url


def _download(url: str, filename: str) -> str:
    """带断点续传与重试的下载（网络抖动时不必从头再来）。"""
    os.makedirs(WHEEL_DIR, exist_ok=True)
    path = os.path.join(WHEEL_DIR, filename)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"  已存在 {filename}")
        return path

    part = path + ".part"
    for attempt in range(1, RETRIES + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        request = urllib.request.Request(url)
        if have:
            request.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(request, timeout=SOCKET_TIMEOUT) as resp:
                resumed = resp.status == 206
                total = int(resp.headers.get("Content-Length", 0)) + (have if resumed else 0)
                mode = "ab" if resumed else "wb"
                if not resumed:
                    have = 0
                print(f"  下载 {filename}（第 {attempt} 次尝试，从 {have/1048576:.1f} MB 继续）…",
                      flush=True)
                with open(part, mode) as out:
                    last_report = time.time()
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        have += len(chunk)
                        if time.time() - last_report > 10:
                            pct = f"{have * 100 / total:.0f}%" if total else "?"
                            print(f"    {have/1048576:.1f} MB ({pct})", flush=True)
                            last_report = time.time()
            os.replace(part, path)
            print(f"  完成 {os.path.getsize(path) / 1048576:.1f} MB")
            return path
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            print(f"  中断：{exc}，5 秒后重试 …", flush=True)
            time.sleep(5)
    raise SystemExit(f"下载失败：{filename}")


def _install(wheel: str, site_packages: str, scripts_dir: str) -> None:
    bases = {
        "purelib": site_packages,
        "platlib": site_packages,
        "scripts": scripts_dir,
        "data": os.path.dirname(os.path.dirname(site_packages)),
        "headers": os.path.join(os.path.dirname(os.path.dirname(site_packages)),
                                "include"),
    }
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            if info.filename.endswith("/"):
                continue
            name = info.filename
            if ".data/" in name:
                _pkg, rest = name.split(".data/", 1)
                scheme, _, rel = rest.partition("/")
                base = bases.get(scheme, site_packages)
                target = os.path.join(base, rel.replace("/", os.sep))
            else:
                target = os.path.join(site_packages, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", default=DEFAULT_SITE)
    parser.add_argument("--packages", nargs="*", default=list(PACKAGES))
    parser.add_argument("--index", action="append", default=None,
                        help="JSON 索引根地址，可重复指定（默认依次尝试镜像与官方）")
    parser.add_argument("--version", default=None,
                        help="把所有包固定到同一个版本（PySide6 与 shiboken6 必须一致）")
    args = parser.parse_args()
    indexes = tuple(args.index) if args.index else DEFAULT_INDEXES

    site_packages = os.path.abspath(args.site_packages)
    # .venv/Lib/site-packages -> .venv/Scripts
    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(site_packages)), "Scripts")
    if not os.path.isdir(site_packages):
        raise SystemExit(f"site-packages 不存在：{site_packages}")
    os.makedirs(scripts_dir, exist_ok=True)

    for package in args.packages:
        version, filename, url = _pick_wheel(indexes, package, args.version)
        print(f"[{package}] {version}")
        wheel = _download(url, filename)
        _install(wheel, site_packages, scripts_dir)
        print(f"  已解压到 {site_packages}")

    print("\n安装完成。验证：")
    print(f'  "{os.path.join(os.path.dirname(scripts_dir), "Scripts", "python.exe")}" '
          f'-c "import PySide6; print(PySide6.__version__)"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
