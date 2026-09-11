"""版本信息（core/version.py）。

窗口标题、关于对话框、打包产物名、git tag 与 GitHub Release 全部以这里为准，
避免出现「程序里写 1.0.0、压缩包叫 0.9」这类不一致。

发新版本时的步骤：
1. 改这里的 ``__version__``；
2. 提交（``git commit -m "release: v1.2.1"``）；
3. 打标签 ``git tag v1.2.1`` 并推送；
4. ``python scripts/release.py``（自动打包 + 创建 GitHub Release 并上传产物）。
"""
from __future__ import annotations

__version__ = "1.2.1"

APP_TITLE = "我的白板"
APP_TITLE_EN = "Whiteboard"
# 打包产物与 Release 资产的前缀（最终形如 Whiteboard-1.0.0-win64.zip）
PACKAGE_BASENAME = "Whiteboard"
AUTHOR = "leye123"


def version_tag() -> str:
    """带 v 前缀的标签名，与 git tag / GitHub Release 一致。"""
    return f"v{__version__}"


def asset_name(suffix: str = "win64.zip") -> str:
    """发布资产文件名，例如 ``Whiteboard-1.0.0-win64.zip``。"""
    return f"{PACKAGE_BASENAME}-{__version__}-{suffix}"


def windows_version_tuple() -> str:
    """Windows 可执行文件资源里的四段式版本号。"""
    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    return ".".join(str(int(p)) if p.isdigit() else "0" for p in parts)
