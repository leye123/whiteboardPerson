"""校验 exe 里真的带上了我们的图标（scripts/check_exe_icon.py）。

    python scripts/check_exe_icon.py                       # 检查 dist 下最新的 exe
    python scripts/check_exe_icon.py path\to\Whiteboard.exe

为什么需要它：忘了给打包器传 ``--icon``（或 .ico 生成失败）时程序一切正常，
只是资源管理器/任务栏上显示一个空白默认图标 —— 光看代码发现不了。

检查方式（不依赖任何 Windows API，纯读文件字节）：

1. 解析 ``resources/icons/whiteboard.ico``，取出每个尺寸的图像数据块；
2. 在 exe 文件里查找这些数据块 —— PyInstaller / Nuitka 会把 ICO 里的
   每张图**原样**写进 PE 的 RT_ICON 资源，所以找到全部数据块
   就等于图标已经被嵌进去（且尺寸一张不少）；
3. 附带确认 exe 里存在 PE 版本资源里我们写的产品名（粗筛，防止拿错文件）。

退出码 0 = 通过，1 = 有问题。
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ICON_PATH = os.path.join(ROOT, "resources", "icons", "whiteboard.ico")
DIST_DIR = os.path.join(ROOT, "dist")


def read_ico_records(path: str) -> list:
    """返回 [(宽, 高, 数据块), ...]。"""
    with open(path, "rb") as handle:
        data = handle.read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1:
        raise ValueError(f"{path} 不是有效的 .ico 文件")
    records = []
    for index in range(count):
        entry = data[6 + 16 * index:6 + 16 * (index + 1)]
        width, height, _colors, _res, _planes, _bits, size, offset = struct.unpack(
            "<BBBBHHII", entry)
        records.append((width or 256, height or 256, data[offset:offset + size]))
    return records


def newest_exe() -> str:
    if not os.path.isdir(DIST_DIR):
        return ""
    candidates = [os.path.join(DIST_DIR, name) for name in os.listdir(DIST_DIR)
                  if name.lower().endswith(".exe")]
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 exe 是否嵌入了我方图标")
    parser.add_argument("exe", nargs="?", default=None, help="要检查的 exe 路径")
    parser.add_argument("--icon", default=ICON_PATH, help="参照的 .ico 文件")
    args = parser.parse_args()

    exe = args.exe or newest_exe()
    if not exe or not os.path.isfile(exe):
        print("找不到要检查的 exe（先运行 python build_exe.py）")
        return 1
    if not os.path.isfile(args.icon):
        print(f"找不到图标文件：{args.icon}（先运行 python scripts/make_icon.py）")
        return 1

    records = read_ico_records(args.icon)
    with open(exe, "rb") as handle:
        blob = handle.read()

    print(f"检查对象：{exe}（{len(blob) / 1024 / 1024:.1f} MB）")
    print(f"参照图标：{args.icon}（{len(records)} 个尺寸）")

    missing = []
    for width, height, payload in records:
        found = blob.find(payload) >= 0
        print(f"  {width:3d}x{height:<3d} 数据块 {len(payload):>7d} 字节  "
              f"{'已嵌入' if found else '缺失!'}")
        if not found:
            missing.append(f"{width}x{height}")

    # 版本资源里的产品名（顺带确认没检查错文件）
    # 注意 PE 的版本资源是 UTF-16LE 字符串，按 ASCII 搜会搜不到
    name_ok = "Whiteboard".encode("utf-16-le") in blob
    print(f"  版本资源里包含产品名 'Whiteboard'：{'是' if name_ok else '否'}")

    print()
    if missing:
        print(f"失败：exe 里缺少 {len(missing)} 个尺寸的图标数据 {missing}")
        print("      请确认 build_exe.py 传了 --icon / --windows-icon-from-ico，"
              "并重新打包。")
        return 1
    print("通过：exe 已嵌入完整的多尺寸图标（资源管理器/任务栏会显示它）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
