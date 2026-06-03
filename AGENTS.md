# 项目开发交接说明

这份文件面向后续接手本项目的 AI/开发者，用来快速理解项目结构、设计约束和已踩过的坑。面向普通用户的安装、配置、模板摆放、OCR 调试和启动说明请维护 `docs/使用说明.md`；不要把复杂开发细节塞进用户说明书。

## 项目目标

本项目是运行在 Windows 上的 Poi/舰娘 Collection 图像识别自动化框架。首要目标是通过固定截图区域、手动 YAML 规则、OpenCV 模板匹配和鼠标点击来完成页面识别与出击流程自动化。

核心约束：

- 不使用 AI 或自动学习，所有页面和流程判断都由模板图、固定区域和 YAML 规则手动配置。
- HP OCR 是可选日志能力，只用于输出 `当前HP/最大HP` 和做舰娘数量提示，不参与大破撤退安全策略。
- 游戏画面逻辑坐标以 `1200x720` 为基准；所有点击坐标、模板搜索区域、OCR 区域都按这个坐标系填写。
- Poi 浏览器右侧数据面板不参与识别；当前截图策略是从 Poi 客户区左侧开始，垂直居中裁出固定游戏区域。
- 不复制 AzurLaneAutoScript/Alas 或 kcauto_custom 源码和素材，只参考其配置、任务、GUI、调度和状态机分层思想。

## 目录和职责

- `pyproject.toml`：Python 项目定义、依赖和启动入口，控制台入口为 `poi-auto = poi_auto.app.main:main`。
- `config/default.yaml`：全局运行配置，包含窗口定位、截图区域、输入、出击、OCR、热键和预览频率等。GUI 会写回这个文件。
- `config/pages.yaml`：全局页面样例库。页面模板默认从 `assets/templates/pages/` 下读取，也定义当前页面可用的动作入口。
- `config/tasks/sortie.yaml`：出击流程解释表。引用 `pages.yaml` 的页面 key 和 action key，描述入口流程、地图动作、循环页面规则、损伤检测和默认点击点。
- `assets/templates/`：模板图位置。不要清理或覆盖用户自己的图片。
- `docs/使用说明.md`：用户手册，只写用户如何安装、启动、配置、采集模板和调试 OCR。
- `src/poi_auto/app/`：应用入口。
- `src/poi_auto/core/`：路径、配置读写、运行上下文和任务线程 runner。
- `src/poi_auto/device/`：Windows 窗口枚举、截图和鼠标点击。这里不应该包含游戏流程逻辑。
- `src/poi_auto/vision/`：模板匹配、页面识别和损伤/HP OCR 采集。这里不应该点击鼠标或修改任务状态。
- `src/poi_auto/tasks/`：任务状态机和流程解释。目前主要实现 `SortieTask`。
- `src/poi_auto/gui/`：PySide6 GUI。负责展示、保存配置、预览截图、彩色日志和发起任务。
- `tests/`：无真实 Poi 的单元测试，覆盖页面 required 模板、出击状态机和损伤/OCR 解析。

## 配置协议

### `config/default.yaml`

关键字段：

- `window.title_keyword`：用于匹配 Poi 窗口标题的关键字。
- `window.selected_title`：GUI 选择的目标窗口标题；优先于关键字匹配结果。
- `window.exclude_own_process`：默认排除本程序窗口，避免截到自己。
- `game.crop_mode`：当前应使用 `left_center_fixed`。
- `game.capture_width` / `game.capture_height`：真实裁剪尺寸，理想值为 `1200x720`。如果用户为适配窗口手动改小，先确认意图再回改。
- `game.logical_width` / `game.logical_height`：逻辑坐标尺寸。坐标采集和配置应以 `1200x720` 为设计基准。
- `game.offset_x` / `game.offset_y`：Poi 布局有偏差时的手动微调。
- `preview.interval_ms`：截图预览间隔。
- `preview.page_match_interval_ms`：页面模板匹配间隔。页面匹配比截图更重，不要强行和截图同频。
- `hotkeys.stop`：运行中停止快捷键。
- `sortie.map`、`sortie.formation`、`sortie.max_battles`、`sortie.stop_on_heavy_damage`：出击任务主配置。
- `sortie.hp_ocr_enabled`：是否在战斗中记录 HP OCR，结果只写日志。
- `sortie.ship_count`：本次出击舰娘数量，`1-6`。OCR 识别数量不一致时只输出 warning。
- `ocr.hp_region`：HP OCR 区域，格式 `{x, y, width, height}`。
- `ocr.hp_padding`：OCR 裁剪前对区域四周扩展的边距。
- `ocr.hp_scale`：OCR 前二值图放大倍数。
- `ocr.hp_rate_per_sec`：战斗中 HP OCR 每秒最多执行次数，不控制页面识别或截图速度。

GUI 会从控件生成配置并写回 `default.yaml`。开发时不要盲目覆盖用户已经保存的值。

### `config/pages.yaml`

页面识别采用多模板命中：

```yaml
pages:
  map_select_1:
    name: "地图1普通选关"
    min_matches: 3
    templates:
      - path: "sortie/fight_menu_1_1.png"
        threshold: 0.86
        required: true
      - path: "sortie/fight_menu_1_2.png"
        threshold: 0.82
      - path: "sortie/fight_menu.png"
        threshold: 0.82
    actions:
      map_1_1:
        name: "1-1"
        x: 405
        y: 298
```

规则说明：

- 模板实际路径是 `assets/templates/pages/<path>`。
- `templates` 下可以放多个模板，表示同一个页面的多个识别点。
- `min_matches` 表示至少命中几个模板才认为页面成立。
- 模板可选 `required: true`。所有 required 模板必须命中，同时还要满足 `min_matches`。
- 不写 `required` 时保持默认的 N 选 M 行为，例如 3 个模板任选 2 个。
- 多个页面同时成立时，选择平均命中分数最高的页面；低于 `monitor.unknown_threshold` 时仍视为 unknown。
- `actions` 是当前页面可用入口，GUI 会按当前识别页面动态生成按钮，任务也只能通过这些 action 点击。
- 模板可以配置 `region: {x, y, width, height}` 限定搜索范围，坐标仍是游戏逻辑坐标。

当前页面 key 至少包括：

```text
home, sortie_menu, map_select_1, sortie_confirm, sortie_start,
compass, formation, battle, night_battle, battle_result,
exp_gain, new_ship, advance_or_retreat, resource_node, return_home
```

### `config/tasks/sortie.yaml`

出击规则由 `SortieTask` 解释器调用。任务配置引用 `pages.yaml` 的页面 key 和 action key，不直接硬编码页面按钮坐标。

主要字段：

- `entry_flow`：母港到正式出击的固定入口流程，按 `page` + `action` 执行。
- `map_actions`：按 `sortie.map` 映射到页面 action，例如 `"1-2": {page: map_select_1, action: map_1_2}`。
- `loop_pages`：出击后循环页面的处理规则。
- `damage_detection`：损伤模板统计和撤退依据。撤退仍只看图像模板结果。
- `hp_ocr`：HP OCR 默认规则，会被 `config/default.yaml` 的 `ocr.*` 覆盖。
- `recovery`：可选容错配置，默认 unknown 最多重试 3 次、首次默认等待 500ms。
- `default_click`：页面规则没有显式 action 时的兜底继续点击点。

支持的 `action_type`：

```text
wait, click_action, click_once, click_anywhere, click_until_next_page,
collect_damage, choose_formation, advance_or_retreat, finish
```

## 坐标和截图模型

当前截图模式是 `left_center_fixed`：

- 先通过 `pywin32` 获取 Poi 窗口客户区屏幕坐标。
- 从客户区左侧 `client.left + offset_x` 开始取图。
- 纵向用 `client.top + (client.height - capture_height) / 2 + offset_y` 居中裁剪。
- 裁剪区域尺寸由 `capture_width` 和 `capture_height` 决定。
- 如果 Poi 客户区小于固定裁剪区域，截图会报错，不会缩放凑合，以免点击坐标失真。

点击换算在 `DeviceController.click()` 中完成：逻辑坐标按最近一次截图的 `source_region` 映射到真实屏幕坐标，然后用 `pyautogui` 点击。

调试 GUI 的实时预览会叠加鼠标在游戏区域内的逻辑坐标，这是采集 YAML 坐标的主要工具。

## 图像识别和 OCR 注意事项

- `Recognizer.match_template()` 使用 OpenCV `cv2.matchTemplate`。
- Windows 中文路径下不要直接用 `cv2.imread(str(path))`，容易出现路径乱码或读取失败。当前代码使用 `np.fromfile(template_path, dtype=np.uint8)` 加 `cv2.imdecode(...)` 读取模板，后续不要退回 `cv2.imread`。
- 模板缺失时应返回 `missing_template` 并在 GUI/日志显示，不能让程序崩溃。
- 页面模板和任务模板目录不同：
  - 页面模板：`assets/templates/pages/...`
  - 出击/大破等任务模板：`assets/templates/sortie/...`
- `DamageDetector` 会对损伤模板做区域化匹配，并对 HP OCR 区域做 padding、灰度、Otsu 二值化和 scale 放大。
- OCR 依赖 `pytesseract` 和本机 Tesseract 可执行程序，但它不是项目必需依赖。不可用时只记录错误，不影响自动出击。
- PowerShell 终端可能把中文显示成乱码，但这不一定代表文件本身编码错误。需要确认编码时，用 Python 按 UTF-8 读取，或直接看 GUI/编辑器显示。

## GUI 结构

GUI 是 PySide6 三栏布局：

- 顶部：任务下拉、启动任务、停止任务。
- 左侧：一级功能导航，当前有 `出击`、`演习`、`远征`、`补给`、`入渠`、`软件调试`。
- 中间：随左侧功能切换的设置页。当前 `出击` 和 `软件调试` 内容较完整，其余是预留页。
- 右侧：始终显示实时游戏截图和富文本彩色日志。
- 出击页：地图、阵型、最大战斗数、大破撤退、HP OCR 开关和舰娘数量。
- 软件调试页：目标窗口、截图裁剪、预览频率、停止快捷键、HP OCR 区域/边距/频率、页面识别详情和当前页面动作测试按钮。
- 软件调试页内容放在 `QScrollArea` 中，设置项较多时依赖滚轮滚动。

性能约束：

- 不要在 Qt 主线程里直接做截图、窗口枚举、OpenCV 模板匹配、OCR 或长等待。
- 当前 GUI 使用两个单线程 `ThreadPoolExecutor`：
  - `poi-preview`：只负责截图预览。
  - `poi-page`：只负责页面匹配。
- 页面匹配从最新截图复制图像后异步执行，避免模板匹配拖慢预览帧率。
- `ScreenCapture` 会复用 `mss.mss()` 实例，关闭窗口时要调用 `capture.close()`。
- `WindowFinder` 会缓存目标 `hwnd`，减少每帧枚举窗口。
- 截图刷新频率、页面匹配频率和 OCR 频率要分开调；页面匹配和 OCR 过密会导致 UI 或任务卡顿。

## 出击任务逻辑

`SortieTask` 是页面驱动状态机：

1. 未开始出击时按 `entry_flow` 解释入口步骤。
2. 如果入口流程期望页和当前页不一致，但当前页也在 `entry_flow` 中，会按当前页对应步骤重新点击，并把 `entry_index` 对齐到下一步。
3. 入口流程完成后进入 `loop_pages`。
4. 识别 `compass` 时点击罗盘并按规则等待。
5. 识别 `formation` 时点击当前 GUI 配置阵型，并按 `count_battle` 递增战斗数。
6. 识别 `battle` 时只采集损伤和 HP OCR，不点击，并按 `poll_ms` 短轮询。
7. 当上一轮是 `battle`、当前轮不是 `battle` 时，先等待 `transition_wait_ms`，然后重新截图识别再处理新页面。
8. 识别夜战、战斗评价、经验结算、新船、资源点等页面时按 `loop_pages` 推进。
9. 识别 `advance_or_retreat` 时，根据损伤模板和最大战斗次数选择进击或撤退。
10. 识别 `return_home` 或 `finish` 页面规则时结束任务。

未知页面容错：

- 连续 unknown 不会立刻停止，会按最近一次规则等待时间重试。
- 默认最多重试 3 次，首次没有历史等待时间时使用 500ms。
- 重新识别到已知页面后清零 unknown 计数。
- 第 3 次仍 unknown 时停止并记录 retry 和 last_wait_ms，避免盲点点击。

## 开发和验证命令

常用检查：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -c "import pathlib; files=list(pathlib.Path('src').rglob('*.py')); [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in files]; print(f'checked {len(files)} files')"
git diff --check
```

运行 GUI：

```powershell
$env:PYTHONPATH = "src"
python -m poi_auto.app.main
```

如果已经 `pip install -e .`，也可以运行：

```powershell
poi-auto
```

开发时优先使用 `rg` 查找文件和文本，不要把 `.venv/`、`__pycache__/`、模板图片和用户日志当成源码一起处理。

## 修改守则

- 修改文件前先看现有结构，保持模块边界，不要把点击逻辑写进识别层，也不要把游戏流程写进设备层。
- 不要删除或覆盖用户自己的模板图、坐标配置和已经保存的 YAML 值。
- `docs/使用说明.md` 保持用户友好；复杂架构说明、性能细节和交接信息写在本文件。
- 手动编辑代码时使用 `apply_patch`。
- 涉及 GUI 性能时，先确认是否有主线程阻塞、截图、页面匹配和 OCR 是否串行，是否重复创建 `mss` 或反复枚举窗口。
- 涉及中文路径或中文文案时，统一按 UTF-8 读写。PowerShell 显示乱码时不要直接判断为文件损坏。
- 新增任务时建议沿用现有分层：先配置页面识别，再写任务解释器，再在 GUI 加设置项和启动入口。

## 当前已知限制

- 真实自动出击是否稳定取决于用户提供的模板图和坐标；仓库里的模板和坐标仍可能需要按实际 Poi 画面微调。
- 页面识别不会自动适配 UI 变化；模板失效时需要用户重新截图替换。
- HP OCR 容易受区域、字体边缘、缩放和 Tesseract 安装影响，结果只适合日志和调试，不作为撤退依据。
- 页面匹配阈值需要根据实际截图调试，过高会识别不到，过低可能误判。
- 当前只完整实现了出击任务框架，演习、远征、补给、入渠仍是 GUI 占位页。
