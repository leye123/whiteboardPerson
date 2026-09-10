"""页面与图形项的 JSON 序列化 / 反序列化（persistence/serializer.py）。

文件格式 (.wbd)：
{
  "app": "whiteboard-pyside",
  "version": 1,
  "current_page": 0,
  "pages": [
     {"name": "页面1", "items": [ <item dict>, ... ]},
     ...
  ]
}
"""
from __future__ import annotations

import json
from typing import List

from canvas.items import is_preview, item_from_dict
from core.page import BoardPage

APP_TAG = "whiteboard-pyside"
FORMAT_VERSION = 1

# ---------------------------------------------------------------- 序列化


def scene_items_to_dicts(scene) -> List[dict]:
    """把场景内容按“自底向上”的顺序序列化为 dict 列表。"""
    # QGraphicsScene.items() 返回的是自上而下（顶层在前）
    bottom_up = reversed(scene.items())
    result = []
    for item in bottom_up:
        to_dict = getattr(item, "to_dict", None)
        if to_dict is None or item.parentItem() is not None or is_preview(item):
            continue
        try:
            result.append(to_dict())
        except Exception:  # 单个坏项不阻塞整个保存
            continue
    return result


def page_to_dict(page: BoardPage) -> dict:
    return {"name": page.name, "items": scene_items_to_dicts(page.scene)}


def document_to_dict(pages: List[BoardPage], current_page: int = 0) -> dict:
    return {
        "app": APP_TAG,
        "version": FORMAT_VERSION,
        "current_page": int(current_page),
        "pages": [page_to_dict(p) for p in pages],
    }


def serialize_document(pages: List[BoardPage], current_page: int = 0) -> str:
    return json.dumps(document_to_dict(pages, current_page),
                      ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 反序列化


def page_from_dict(data: dict) -> BoardPage:
    page = BoardPage(name=data.get("name", "页面"))
    for item_data in data.get("items", []):
        item = item_from_dict(item_data)
        if item is not None:
            page.scene.addItem(item)
    return page


def document_from_dict(data: dict):
    """返回 (pages, current_page)；非法数据抛 ValueError。"""
    if not isinstance(data, dict) or data.get("app") != APP_TAG:
        raise ValueError("不是有效的白板文件")
    pages = [page_from_dict(p) for p in data.get("pages", [])]
    if not pages:
        pages = [BoardPage("页面 1")]
    current = int(data.get("current_page", 0))
    current = max(0, min(current, len(pages) - 1))
    return pages, current


def deserialize_document(text: str):
    return document_from_dict(json.loads(text))
