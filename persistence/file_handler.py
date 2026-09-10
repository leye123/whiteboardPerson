"""文件读写（persistence/file_handler.py）：.wbd 自定义格式的原子写入与读取。"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List

from PySide6.QtCore import QStandardPaths

from core import paths
from core.page import BoardPage
from persistence import serializer

FILE_EXT = ".wbd"
_FILE_FILTER = "白板文件 (*.wbd);;所有文件 (*)"


def default_directory() -> str:
    """默认打开/保存目录（文档目录优先，否则家目录）。"""
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    if docs and os.path.isdir(docs):
        return docs
    return os.path.expanduser("~")


def save_text_atomic(path: str, text: str) -> None:
    """先写临时文件再替换，避免中途断电损坏原文件。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wb_save_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- 文档级 API


def save_document(path: str, pages: List[BoardPage], current_page: int = 0) -> None:
    text = serializer.serialize_document(pages, current_page)
    save_text_atomic(path, text)


def load_document(path: str):
    """从文件加载，返回 (pages: List[BoardPage], current_page: int)。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return serializer.document_from_dict(data)


def autosave_path() -> str:
    """自动备份文件路径。

    默认跟配置文件放在一起（软件目录，便携模式）；软件目录不可写时
    由 :func:`core.paths.autosave_file` 自动退回系统应用数据目录。
    """
    return paths.autosave_file()


def autosave_exists() -> bool:
    path = autosave_path()
    return os.path.exists(path) and os.path.getsize(path) > 0


def save_autosave(pages: List[BoardPage], current_page: int = 0) -> str:
    path = autosave_path()
    save_document(path, pages, current_page)
    return path


def clear_autosave() -> None:
    try:
        os.remove(autosave_path())
    except OSError:
        pass
