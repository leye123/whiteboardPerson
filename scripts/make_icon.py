"""生成 Windows 图标文件 resources/icons/whiteboard.ico（scripts/make_icon.py）。

    python scripts/make_icon.py            # 生成/覆盖 .ico
    python scripts/make_icon.py --check    # 只检查是否存在且尺寸齐全

为什么需要这个脚本：

* exe 的图标**不是**运行时画出来的，必须在打包时把 ``.ico`` 交给打包器
  （PyInstaller ``--icon`` / Nuitka ``--windows-icon-from-ico``），
  否则资源管理器、任务栏、快捷方式上显示的都是默认图标；
* 图标要与程序里 ``widgets.icons.app_icon()`` 画出来的窗口图标一致，
  所以这里直接复用 :func:`widgets.icons.draw_app_mark` 绘制，而不是另画一套。

ICO 格式：文件头 + 目录表 + 每个尺寸的位图数据。这里写的是**经典
32 位 BMP(DIB) 记录**（带 AND 掩码），而不是 PNG 压缩记录 ——
PNG 记录虽然 Vista 之后也支持，但部分打包/图标提取工具解析不完整，
BMP 记录兼容性最好，且体积很小。
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

from widgets import icons  # noqa: E402

# 打包器与 Windows 需要的尺寸（16 是任务栏/资源管理器小图标，256 是超大图标）
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
DEFAULT_PATH = os.path.join(ROOT, "resources", "icons", "whiteboard.ico")


def render_image(size: int, theme: str = "light") -> QImage:
    """把应用标志渲染成 size x size 的 ARGB 位图（与窗口图标同一份代码）。"""
    return icons.app_image(size, theme)


def _dib_record(image: QImage) -> bytes:
    """把一个尺寸的位图编码成 ICO 里的 BMP(DIB) 记录。"""
    width, height = image.width(), image.height()

    # BITMAPINFOHEADER：高度要写成 2 倍（XOR 图像 + AND 掩码）
    header = struct.pack(
        "<IiiHHIIiiII",
        40,                    # biSize
        width,                 # biWidth
        height * 2,            # biHeight
        1,                     # biPlanes
        32,                    # biBitCount
        0,                     # biCompression = BI_RGB
        width * height * 4,    # biSizeImage
        0, 0, 0, 0)            # 分辨率与调色板

    # XOR 数据：BGRA、自下而上
    rows = []
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(width):
            pixel = image.pixelColor(x, y)
            alpha = pixel.alpha()
            # 预乘 alpha：Windows 的 32bpp 图标按预乘解释，不预乘会出现白边
            row += bytes((
                (pixel.blue() * alpha + 127) // 255,
                (pixel.green() * alpha + 127) // 255,
                (pixel.red() * alpha + 127) // 255,
                alpha,
            ))
        rows.append(bytes(row))
    xor_data = b"".join(rows)

    # AND 掩码：1bpp，每行按 4 字节对齐；alpha=0 的位置置 1（透明）
    stride = ((width + 31) // 32) * 4
    mask_rows = []
    for y in range(height - 1, -1, -1):
        bits = bytearray(stride)
        for x in range(width):
            if image.pixelColor(x, y).alpha() == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask_rows.append(bytes(bits))
    mask_data = b"".join(mask_rows)

    return header + xor_data + mask_data


def build_ico(path: str = DEFAULT_PATH, sizes=SIZES, theme: str = "light") -> str:
    """生成多尺寸 .ico 文件，返回文件路径。"""
    records = [(size, _dib_record(render_image(size, theme))) for size in sizes]

    header = struct.pack("<HHH", 0, 1, len(records))       # reserved, type=icon, count
    directory = bytearray()
    offset = 6 + 16 * len(records)
    for size, data in records:
        directory += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,     # 256 在目录里写 0
            size if size < 256 else 0,
            0,                             # 调色板颜色数
            0,                             # reserved
            1,                             # planes
            32,                            # bit count
            len(data),
            offset)
        offset += len(data)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(bytes(directory))
        for _size, data in records:
            handle.write(data)
    return path


def read_ico_sizes(path: str) -> list:
    """读回 ICO 里记录的尺寸（校验用）。"""
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 6:
        return []
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1:
        return []
    sizes = []
    for index in range(count):
        entry = data[6 + 16 * index:6 + 16 * (index + 1)]
        width, height = entry[0], entry[1]
        sizes.append((width or 256, height or 256))
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Windows 图标 (.ico)")
    parser.add_argument("--out", default=DEFAULT_PATH, help="输出路径")
    parser.add_argument("--theme", default="light", choices=("light", "dark"))
    parser.add_argument("--check", action="store_true",
                        help="只检查现有 .ico（不重新生成）")
    args = parser.parse_args()

    app = QGuiApplication.instance() or QGuiApplication([])   # noqa: F841

    if args.check:
        if not os.path.exists(args.out):
            print(f"缺少图标文件：{args.out}")
            return 1
        sizes = read_ico_sizes(args.out)
        missing = [s for s in SIZES if (s, s) not in sizes]
        print(f"图标文件：{args.out}（{os.path.getsize(args.out)} 字节）")
        print(f"包含尺寸：{', '.join(str(w) for w, _h in sizes)}")
        if missing:
            print(f"缺少尺寸：{missing}")
            return 1
        print("尺寸齐全 [OK]")
        return 0

    path = build_ico(args.out, theme=args.theme)
    sizes = read_ico_sizes(path)
    print(f"已生成：{path}")
    print(f"  文件大小：{os.path.getsize(path)} 字节")
    print(f"  包含尺寸：{', '.join(str(w) for w, _h in sizes)}")

    # 顺便导出窗口图标用的 PNG 方便肉眼核对（docs/ 里，不进版本库也无妨）
    preview = os.path.join(ROOT, "docs", "app_icon.png")
    try:
        os.makedirs(os.path.dirname(preview), exist_ok=True)
        render_image(256, args.theme).save(preview, "PNG")
        print(f"  预览图：{preview}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
