# 我的白板 (Whiteboard) — PySide6

基于 **PySide6 / Qt Graphics View Framework** 的 Windows 个人白板应用。
自由画笔（平滑曲线）、橡皮擦（擦断笔迹）、11 种形状（含圆角矩形与虚线线型）、
文字（可选字体/字号/颜色/自动换行，支持导入字体）、选择/移动（含框选）、
拖动画布、多页面、撤销重做、`.wbd` 文件保存、PNG 导出、导入图片、
自动保存与深浅主题。

![浅色主题](docs/screenshot_light.png)

<details>
<summary>深色主题预览</summary>

![深色主题](docs/screenshot_dark.png)

</details>

---

## 1. 环境与安装

要求 **Python 3.9+**（开发验证环境为 Python 3.10 + PySide6 6.11.2）。

```bash
# 1) 创建虚拟环境（当前目录构建，不污染系统环境）
python -m venv .venv

# 2) 安装依赖
python -m pip install -r requirements.txt
```

## 2. 运行

```bash
python main.py                 # 启动空白白板
python main.py 我的笔记.wbd     # 启动并打开文件
```

## 3. 项目结构

```
whiteboard/
├── main.py                  # 程序入口（QApplication、命令行打开文件）
├── main_window.py           # 主窗口：菜单/工具栏/状态栏/多页面/自动保存/主题
├── build_exe.py             # 打包脚本（PyInstaller 或 Nuitka）
├── canvas/                  # 画布
│   ├── scene.py             # WhiteboardScene：点阵背景、选中框、命中查询、图形项生命周期
│   ├── view.py              # WhiteboardView：事件路由、滚轮缩放、中键/空格平移
│   └── items.py             # 可持久化图形项（笔画/矩形·圆角矩形/椭圆/三角·菱形·五角星/直线箭头/文字/图片）
├── tools/                   # 工具系统（策略模式，统一事件接口）
│   ├── base_tool.py         # 工具基类
│   ├── pan_tool.py          # 拖动画布（左键拖拽平移，等价于空格/中键）
│   ├── pen_tool.py          # 画笔（中点二次贝塞尔平滑，按屏幕像素采样）
│   ├── eraser_tool.py       # 橡皮擦（一次拖拽 = 一个撤销步骤）
│   ├── shape_tool.py        # 11 种形状（矩形/圆角矩形/椭圆/三角形/菱形/五角星/直线/虚线/箭头/虚线箭头/双向箭头）
│   ├── text_tool.py         # 文字（弹窗输入，带字体/字号/颜色/换行设置）
│   └── selector_tool.py     # 选择/移动（框选、Ctrl 多选、整体拖动、双击编辑文字）
├── core/                    # 核心数据与逻辑
│   ├── stroke.py            # 笔画模型 + 平滑曲线 + 颜色序列化工具
│   ├── page.py              # 页面（一页 = 一个独立场景 + 独立撤销栈）
│   ├── history.py           # QUndoCommand / QUndoStack 撤销重做
│   ├── paths.py             # 程序/数据文件位置（便携模式、只读目录兜底）
│   ├── settings.py          # 偏好设置（whiteboard.ini，不写注册表）
│   ├── text_format.py       # 文字排版参数（字体/字号/颜色/粗斜体/对齐/自动换行）
│   ├── fonts.py             # 导入字体：复制到 fonts/ 并注册（不装进系统）
│   └── version.py           # 版本号唯一来源（窗口标题/关于/产物名/标签）
├── persistence/
│   ├── serializer.py        # 文档 <-> JSON dict（.wbd 格式）
│   └── file_handler.py      # 原子写入、自动保存路径
├── widgets/
│   ├── icons.py             # 运行时绘制的矢量图标（随主题重着色）+ 应用标志
│   ├── text_dialog.py       # 文字对话框：字体（含导入）/字号/颜色/粗斜体/对齐/自动换行 + 实时预览
│   ├── line_style_picker.py # 线型下拉框（实线/虚线/点线/点划线，带预览线）
│   ├── color_picker.py      # 取色按钮
│   ├── thickness_slider.py  # 粗细滑块
│   └── page_navigator.py    # 页面切换器
├── resources/
│   ├── icons/README.md      # 自定义图标的替换说明
│   ├── icons/whiteboard.ico # exe 图标（由 scripts/make_icon.py 生成）
│   └── styles/              # 浅色/深色 QSS
├── scripts/
│   ├── install_wheels.py    # 离线/受限网络下的依赖安装
│   ├── make_icon.py         # 生成多尺寸 whiteboard.ico（打包时写进 exe 资源）
│   ├── release.py           # 打包 + 生成发布资产 + 创建 GitHub Release
│   ├── cleanup.py           # 清理配置/自动备份/导入的字体（卸载、搬家时用）
│   ├── screenshot.py        # 把主窗口渲染成 PNG（文档配图 / 视觉检查）
│   ├── check_icons.py       # 图标自检：贴边裁切/空白/被拉伸 + 生成总览图
│   └── icon_ascii.py        # 在终端用 ASCII 点阵“看”图标（排查线条断裂）
├── tests/
│   ├── test_core.py         # 29 项核心逻辑单元测试（无需 pytest）
│   └── smoke_test.py        # 88 项端到端冒烟测试（offscreen，无需显示器）
└── docs/                    # 截图与图标总览图
```

## 4. 操作指南


| 操作         | 方式                                                                                                                                         |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 工具切换     | 工具栏按钮或「编辑」菜单                                                                                                                     |
| 拖动画布     | ① 工具栏「拖动」工具（四向箭头图标）：选中后左键拖拽即可；② 按住空格 + 左键拖拽；③ 中键拖拽                                               |
| 形状         | 点工具栏「形状」下拉按钮选图形（11 种），在画布上按住左键拖拽绘制；**线型**由工具栏右侧的下拉框决定（实线/虚线/点线/点划线），任何形状都能画成虚线 |
| 文字         | 选文字工具后点击画布，在对话框里输入内容并设置字体（可导入 .ttf/.otf）、字号、颜色、粗体/斜体、对齐与自动换行；编辑区实时预览效果            |
| 编辑文字     | 选择工具双击文字对象（内容与排版可一起改，且可撤销）                                                                                         |
| 线宽/颜色    | 工具栏「颜色」按钮与「粗细」滑块（作用范围随工具而定；文字字号在文字对话框里单独设置）                                                       |
| 橡皮擦       | 拖动擦掉经过的**笔迹片段**（擦断，不是整条删除）；形状/文字/图片被碰到时整体删除；光标处会显示作用范围圆圈，大小随「粗细」滑块调整           |
| 选择/移动    | 选择工具：点击选中、Ctrl 多选、空白处拖拽框选；拖动整体移动                                                                                  |
| 缩放         | 鼠标滚轮（以光标为中心）；`+`/`-`、`Ctrl+0` 适应窗口、`Ctrl+1` 实际大小                                                                      |
| 撤销/重做    | `Ctrl+Z` / `Ctrl+Y`（或 `Ctrl+Shift+Z`）；一次擦除拖拽 = 一个撤销步骤                                                                        |
| 删除         | 选择工具选中后按`Delete`                                                                                                                     |
| 页面         | `Ctrl+T` 新建；`PgUp`/`PgDn` 切换；`Ctrl+Shift+D` 删除当前页                                                                                 |
| 文件         | `Ctrl+S` 保存、`Ctrl+Shift+S` 另存为、`Ctrl+O` 打开、`Ctrl+E` 导出 PNG、`Ctrl+I` 导入图片                                                    |
| 恢复默认设置 | 「视图 → 恢复默认设置…」（清掉颜色/粗细/线型/形状/工具/主题等偏好，画布内容与导入的字体不动）                                                |

## 5. 实现要点

- **工具策略模式**：每个工具只需实现 `mousePressEvent/mouseMoveEvent/mouseReleaseEvent`
  等回调，`WhiteboardView` 负责把事件转发给当前工具；工具不持有场景引用，
  因此多页面切换时无需重建工具。平移手势只实现一份（视图的
  `begin_pan/do_pan/end_pan`），空格+左键、中键、以及「拖动」工具都复用它 ——
  「拖动」工具只需要在按下时调用 `begin_pan`，后续移动与收尾由视图接管。
- **橡皮擦（擦断算法）**：`core.stroke.split_by_eraser()` 把一条笔迹的采样点按
  「是否落在橡皮圆内」切成若干段 —— 擦中间就断成两条，擦两端就变短，全擦到才消失；
  剩余的段会重新生成 `StrokeItem`。整段拖拽通过
  `core.history.ReplaceItemsCommand` 合并成**一个**撤销步骤，
  且中途生成的碎片再次被擦到时不会污染撤销记录（见
  `test_eraser_gesture_bookkeeping`）。橡皮直径 = `粗细*2+8`（上限 48），
  以前的 `粗细*4+8` 在粗细拉满时会得到 64 像素的橡皮，点一下就能把整条笔迹吃掉。
- **撤销栈**：所有内容变更都封装成 `QUndoCommand`（新增/删除/批量删除/清空/移动/替换），
  一次拖拽擦除多个对象会合并成一个撤销步骤（`core.history.push_commands`）。
- **平滑笔迹**：采样点由 `core.stroke.build_path` 用「中点二次贝塞尔」拟合，
  采样阈值按屏幕像素计算，缩放后手感一致；序列化保存的是原始采样点，
  因此曲线拟合不会造成保存后丢点。
- **图形项生命周期**：`WhiteboardScene` 持有顶层图形项的 Python 引用。
  否则 `QUndoStack.clear()` 删掉撤销命令后，图形项会因引用计数归零被回收，
  表现为「删除页面后其它页内容也消失」。
- **形状与线型**：形状种类定义在 `tools/shape_tool.py` 的 `SHAPE_SPECS`
  （几何类型 + 强制线型 + 箭头形态），几何生成在 `canvas/items.py`
  （`rounded_rect_path`、`polygon_shape_path`）。圆角矩形与三角形/菱形/五角星
  用 `QGraphicsPathItem` 实现（`QGraphicsRectItem` 画不出圆角），
  `RectItem` 的 `TYPE` 仍是 `"rect"`，旧文件照样能读。
  线型（solid/dash/dot/dash_dot）存在每一项的 `line_style` 字段里，
  所以虚线是**任意形状都支持**的属性，而不是靠多画几种图形来凑。
  自定义图形项自己实现 `boundingRect()`（`pen_bounds`）：
  Qt 6.11 的 `QGraphicsPathItem` 会在路径外多留约 1.5 倍线宽，
  用它会让导出的 PNG 四周留白比设定边距多几像素。
- **文字与字体**：排版参数集中在 `core/text_format.py` 的 `TextFormat`
  （字体/字号/颜色/粗斜体/对齐/自动换行宽度），对话框、图形项、设置文件
  三处共用同一份定义。自动换行 = `QGraphicsTextItem.setTextWidth(w)`，
  折行宽度一起序列化，重开文件排版不变。
  「导入字体」会先把 .ttf/.otf **复制到数据目录的 `fonts/`**，
  再用 `QFontDatabase.addApplicationFont()` 注册到本进程，并把文件清单
  记进 `whiteboard.ini`（下次启动自动重新注册）——
  不往系统字体目录写文件、不需要管理员权限，删掉 `fonts/` 就清理干净。
  注意 `QFontComboBox` **不会**在 `addApplicationFont()` 之后刷新列表，
  所以对话框用的是自己填充的 `QComboBox`（每项按自身字体预览）。
- **网格自适应**：点阵网格步长随缩放自动切换档位，并限制单次绘制点数，
  避免缩到很小时一次重绘画几十万个点。
- **PNG 导出**：导出范围取「所有图形项外接矩形 + 24 边距」（而不是 20000×20000 的
  场景矩形），输出尺寸 = 该范围 × 2（2 倍超采样）。两个容易写错的地方：

  * `QGraphicsScene.render()` **自己**会把 `source` 映射到 `target`，
    目标矩形已经是 source 的 2 倍就等于做了超采样 —— 不能再
    `painter.scale(2, 2)`，否则缩放两次，只会导出左上角那一块
    （表现为「导出图片被裁掉一半」）；
  * 导出前要**临时取消选中**：`QGraphicsTextItem` 等对象在选中态会用高亮色绘制，
    不清掉的话导出图里会带上选中高亮；点阵网格也要临时隐藏。
    测试用「选中状态导出的图 == 无选中状态导出的图」逐像素比对来卡这一点。

## 6. `.wbd` 文件格式

UTF-8 JSON 文本，可读、可手工编辑、可版本管理：

```json
{
  "app": "whiteboard-pyside",
  "version": 1,
  "current_page": 0,
  "pages": [
    {"name": "页面 1", "items": [
      {"type": "stroke", "color": [0,0,0,255], "thickness": 2.0,
       "points": [[100.0, 200.0], [104.0, 208.0]], "pos": [0.0, 0.0]},
      {"type": "rect", "kind": "round_rect", "x": 40, "y": 60, "w": 200, "h": 120,
       "radius": 21.6, "line_style": "dash", "outline": [0,0,0,255],
       "outline_width": 2.0, "fill": null, "pos": [0.0, 0.0]},
      {"type": "polygon", "kind": "star", "x": 300, "y": 60, "w": 120, "h": 120,
       "line_style": "solid", "outline": [200,60,40,255], "outline_width": 3.0},
      {"type": "line", "x1": 0, "y1": 0, "x2": 120, "y2": 0,
       "line_style": "dash", "arrow": "both", "outline": [0,0,0,255]},
      {"type": "text", "text": "你好", "font_family": "Microsoft YaHei",
       "pixel_size": 24, "bold": false, "italic": false,
       "wrap": true, "text_width": 300, "align": "left",
       "color": [0,0,0,255], "pos": [400.0, 500.0]}
    ]}
  ]
}
```

各字段含义：`line_style` ∈ `solid|dash|dot|dash_dot`；
`arrow` ∈ `none|end|both`（旧文件里的布尔值 `true/false` 也兼容）；
`kind` 对矩形是 `rect|round_rect`、对多边形是 `triangle|diamond|star`。
新增字段都带默认值，所以**旧版本的 .wbd 文件可以直接打开**。

写入采用「临时文件 + `os.replace`」的原子方式，中途断电不会损坏原文件。
自动保存默认每 60 秒写入软件目录下的 `autosave.wbd`，下次启动会询问是否恢复。

### 配置存放位置（便携模式）

偏好设置默认写在**软件目录**下的 `whiteboard.ini`（源码运行 = 项目根目录，
打包后 = exe 所在目录），**不写注册表** —— 删掉目录就彻底干净了：

```ini
[style]
color=#ff000000
thickness=9

[tools]
current=pen

[window]
geometry=@ByteArray(...)
state=@ByteArray(...)

[app]
theme=light
```

* 软件目录**不可写**时（例如装在 `Program Files`、只读 U 盘），自动退回系统的
  应用数据目录，`core.paths.is_portable()` 可以查询当前落在哪种情况；
* 可用环境变量覆盖：`WHITEBOARD_CONFIG`（配置文件路径）、
  `WHITEBOARD_DATA_DIR`（数据目录，自动备份与导入的字体放这里）；
* 导入的字体记在 `[fonts] imported=...` 下，文件本体在数据目录的 `fonts/` 里；
* 旧版本写在注册表里的偏好会在**首次运行新版时一次性迁移**到 ini 并清掉旧键
  （只搬一次：否则删掉 ini 想「恢复默认」时旧值会从注册表复活）。
  想回到出厂状态用「视图 → 恢复默认设置…」（不会删掉导入的字体）。

### 卸载 / 清理痕迹

便携模式下，**删掉软件目录就干净了**（`whiteboard.ini`、`autosave.wbd`
与 `fonts/` 都在里面）。随附脚本用于处理历史遗留（旧版注册表项、旧版 AppData 目录）：

```powershell
python scripts/cleanup.py --dry-run        # 先看看会删什么
python scripts/cleanup.py                  # 确认后清理（含导入的字体）
python scripts/cleanup.py --settings-only  # 只删配置，保留自动备份与字体
python scripts/cleanup.py --legacy-only    # 只清旧版遗留（注册表 + 旧数据目录）
```


| 内容                     | 位置                                                |
| ------------------------ | --------------------------------------------------- |
| 配置（当前版本）         | 软件目录`whiteboard.ini`                            |
| 自动备份                 | 软件目录`autosave.wbd`                              |
| 导入的字体               | 软件目录`fonts/`                                    |
| 旧版注册表偏好（若存在） | `HKCU\Software\WhiteboardPyside\Whiteboard`         |
| 旧版数据目录（若存在）   | `%APPDATA%\WhiteboardPyside\{Whiteboard, 我的白板}` |

手动清理：`reg delete "HKCU\Software\WhiteboardPyside" /f`，
以及删掉 `%APPDATA%\WhiteboardPyside`。

## 7. 测试

```bash
# 核心逻辑单元测试（笔画/擦除切分/形状与线型/文字排版/字体导入/序列化/撤销命令）
python tests/test_core.py            # 29/29

# 端到端冒烟测试：画笔→撤销→形状（11 种）→虚线→橡皮擦断→框选→拖动→文字排版
#                 →导入图片→多页面→存取→导出 PNG→主题
python tests/smoke_test.py           # 88/88
```

两个脚本都会自动使用 `QT_QPA_PLATFORM=offscreen`，无需真实显示器，
退出码 0 表示全部通过。生成截图：

```bash
python scripts/screenshot.py --theme light
python scripts/screenshot.py --theme dark
```

### 图标自检

```bash
python scripts/check_icons.py            # 逐图标体检 + 工具栏实测 + 生成 docs/icons.png
set QT_SCALE_FACTOR=1.25 && python scripts/check_icons.py   # 模拟 125% 缩放屏
python scripts/icon_ascii.py 16 pen eraser                  # 终端里 ASCII 看图标
python scripts/icon_ascii.py 32 app                         # 应用图标（exe/窗口图标）
python scripts/make_icon.py                                 # 重新生成 resources/icons/whiteboard.ico
```

`check_icons.py` 会检查：图标是否空白、墨迹是否贴到画布边缘（贴边=被裁切）、
覆盖率是否过高（小尺寸糊成一团）、工具栏是否溢出（按钮被收进 “>>”）、
工具栏控件是否被异常拉伸。在 100%/125%/150% 缩放下都应退出码为 0。

应用图标（窗口图标与 exe 图标用的是同一份绘制代码）：

![应用图标](docs/app_icon.png)

## 8. 已知限制与后续可做

- 形状（矩形/椭圆/多边形/直线/箭头）/ 文字 / 图片无法「擦断」，被橡皮碰到时整体删除；
  笔迹已经支持按橡皮圆擦断（见第 5 节的切分算法）。
- 未填充图形（矩形/椭圆/多边形）的命中判定只认**描边**：点图形正中不会选中它，
  需要点在边线上（框选不受影响）。
- 选择工具支持移动，暂未实现缩放/旋转控制点；导入的图片也不能拖角缩放。
- 文字暂不支持部分加粗/部分改色（整块文字共用一套排版参数）。
- 导入的字体只在本机生效：文档里记录字体名与来源文件，换台机器打开时若字体缺失
  会退回默认字体（不会报错）。
- 数位板压感可变线宽尚未实现（可在 `PenTool` 里读取 `QTabletEvent.pressure`，
  用「变宽填充多边形」渲染）。
- 场景范围固定为 20000×20000（近似无限画布），超出该范围的内容会被裁掉。
