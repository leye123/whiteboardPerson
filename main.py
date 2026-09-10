"""程序入口（main.py）。

用法：
    python main.py                 # 启动空白白板
    python main.py path/to/a.wbd   # 启动并打开指定文件
    python main.py --paths         # 只打印「配置/数据文件放在哪」然后退出
"""
from __future__ import annotations

import os
import sys

# 直接运行 main.py 时，把项目根目录放进 sys.path，
# 这样 canvas/ core/ tools/ 等包都能用绝对导入。
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import paths  # noqa: E402
from core.paths import APP_NAME, ORG_NAME
from main_window import MainWindow  # noqa: E402
from widgets import icons  # noqa: E402


def _print_paths() -> int:
    """打印路径诊断信息（打包后排查「配置没保存」用）。

    用法：``Whiteboard.exe --paths``
    """
    from persistence import file_handler as fh

    print("=== 白板 路径诊断 ===")
    print(f"是否打包运行   : {paths.is_packaged()}"
          f"（sys.frozen={getattr(sys, 'frozen', None)}, "
          f"__compiled__={'__compiled__' in globals()}）")
    print(f"sys.executable : {sys.executable}")
    print(f"sys.argv[0]    : {sys.argv[0] if sys.argv else '(空)'}")
    print(f"软件所在目录   : {paths.app_directory()}")
    print(f"该目录可写     : {paths.is_writable(paths.app_directory())}")
    print(f"数据目录       : {paths.data_directory()}")
    print(f"便携模式       : {paths.is_portable()}")
    print(f"配置文件       : {paths.settings_file()}")
    print(f"自动备份       : {fh.autosave_path()}")
    settings_path = paths.settings_file()
    print(f"配置文件存在   : {os.path.exists(settings_path)}")
    return 0


def _set_app_user_model_id() -> None:
    """在 Windows 上声明本进程的 AppUserModelID。

    Windows 默认按进程路径给任务栏分组：打包/源码运行时的宿主是 python.exe，
    于是任务栏上可能显示 Python 的图标、并把多个程序混在一起。
    显式设置 ID 之后，任务栏、Alt+Tab 都会用窗口自己的图标（见 setWindowIcon）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{ORG_NAME}.{APP_NAME}")
    except Exception:  # noqa: BLE001 —— 拿不到就算了，不影响使用
        pass


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--paths" in argv:
        return _print_paths()

    _set_app_user_model_id()
    app = QApplication(argv)
    app.setOrganizationName(ORG_NAME)          # QSettings / QStandardPaths 用
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName("我的白板")
    app.setWindowIcon(icons.app_icon())

    window = MainWindow()

    # 命令行传入 .wbd 文件时直接打开
    if len(argv) > 1 and os.path.isfile(argv[1]):
        window.open_file(argv[1])

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
