# 图标目录（resources/icons）

本项目的工具栏/菜单图标**不依赖这里的图片文件**：它们由
`widgets/icons.py` 在运行时用 QPainter 绘制，好处是

- 打包（PyInstaller / Nuitka）时不需要额外处理资源路径；
- 切换浅色/深色主题时图标会按新配色重新绘制；
- 选中态自动使用强调色，不需要维护两套图。

如果你希望替换成自己的图标，可以：

1. 把 `*.svg` / `*.png` 放到本目录；
2. 在 `widgets/icons.py` 的 `tool_icon()` 里优先尝试从本目录加载：

```python
path = os.path.join(RESOURCE_DIR, "icons", f"{name}.svg")
if os.path.exists(path):
    return QIcon(path)
```

图标命名与 `main_window.TOOL_META` 的 key 一致：
`selector / pan / pen / eraser / rect / ellipse / line / arrow / text`，
菜单图标为 `new / open / save / export / image / undo / redo / trash / page / fit / zoom_in / zoom_out / moon`。
另有 `hand`（张开的手型平移图标）作为 `pan` 的备选：把
`widgets/icons.py` 里 `_DRAW["pan"]` 的值换成 `_hand` 即可。
