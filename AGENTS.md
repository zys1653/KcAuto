# 项目开发交接说明

这份文件面向后续接手本项目的 AI/开发者，用来快速理解项目结构、设计约束和已踩过的坑。面向普通用户的安装、配置、模板摆放和启动说明请继续维护 `docs/使用说明.md`；不要把复杂开发细节塞进用户说明书。

## 项目目标

本项目是运行在 Windows 上的 Poi/舰娘 Collection 图像识别自动化框架。首要目标是通过固定截图区域、手动 YAML 规则、OpenCV 模板匹配和鼠标点击来完成页面识别与出击流程自动化。

核心约束：

- 不使用 AI、OCR 或自动学习，所有判断都由模板图和 YAML 规则手动配置。
- 游戏画面逻辑坐标以 `1200x720` 为基准；所有点击坐标、模板搜索区域都按这个坐标系填写。
- Poi 浏览器右侧数据面板不参与识别；当前截图策略是从 Poi 客户区左侧开始，垂直居中裁出固定游戏区域。
- 不复制 AzurLaneAutoScript/Alas 源码，只参考其配置、任务、GUI 和调度分层思想。

## 目录和职责

- `pyproject.toml`：Python 项目定义、依赖和启动入口，控制台入口为 `poi-auto = poi_auto.app.main:main`。
- `config/default.yaml`：全局运行配置，包含窗口定位、截图区域、输入、出击、热键和预览频率等。GUI 会写回这个文件。
- `config/pages.yaml`：通用页面识别规则。页面模板默认从 `assets/templates/pages/` 下读取，也定义当前页面可用的测试点击动作。
- `config/tasks/sortie.yaml`：出击任务规则。包含地图配置、阵型坐标、进击/撤退/回母港坐标和大破检测模板。
- `assets/templates/`：用户手动放置模板图的位置。不要清理或覆盖这里的图片。
- `docs/使用说明.md`：用户手册，只写用户如何安装、启动、配置和采集模板。
- `src/poi_auto/app/`：应用入口。
- `src/poi_auto/core/`：路径、配置读写、运行上下文和任务线程 runner。
- `src/poi_auto/device/`：Windows 窗口枚举、截图和鼠标点击。这里不应该包含游戏流程逻辑。
- `src/poi_auto/vision/`：模板匹配和页面识别。这里不应该点击鼠标或修改任务状态。
- `src/poi_auto/tasks/`：任务状态机和流程解释。目前主要实现 `SortieTask`。
- `src/poi_auto/gui/`：PySide6 GUI。负责展示、保存配置、预览截图、日志和发起任务。

## 配置协议

### `config/default.yaml`

关键字段：

- `window.title_keyword`：用于匹配 Poi 窗口标题的关键字。
- `window.selected_title`：GUI 选择的目标窗口标题；优先于关键字匹配结果。
- `window.exclude_own_process`：默认排除本程序窗口，避免截到自己。
- `game.crop_mode`：当前应使用 `left_center_fixed`。
- `game.capture_width` / `game.capture_height`：真实裁剪尺寸，当前按 `1200x720`。
- `game.logical_width` / `game.logical_height`：逻辑坐标尺寸。正常应保持 `1200x720`，如果用户手动改过，先确认意图再回改。
- `game.offset_x` / `game.offset_y`：Poi 布局有偏差时的手动微调。
- `preview.interval_ms`：截图预览间隔。
- `preview.page_match_interval_ms`：页面模板匹配间隔。页面匹配比截图更重，不要强行和截图同频。
- `hotkeys.stop`：运行中停止快捷键。
- `sortie.map`、`sortie.formation`、`sortie.max_battles`、`sortie.stop_on_heavy_damage`：出击任务配置。

GUI 会从控件生成配置并写回 `default.yaml`。开发时不要盲目覆盖用户已经保存的值。

### `config/pages.yaml`

页面识别采用多模板命中：

```yaml
pages:
  home:
    name: "母港"
    min_matches: 2
    templates:
      - path: "home/sortie_button.png"
        threshold: 0.86
      - path: "home/resource_bar.png"
        threshold: 0.82
    actions:
      sortie:
        name: "出击"
        x: 327
        y: 401
```

规则说明：

- 模板实际路径是 `assets/templates/pages/<path>`。
- `templates` 下可以放多个模板，表示同一个页面的多个识别点。
- `min_matches` 表示至少命中几个模板才认为页面成立。
- 多个页面同时成立时，选择平均命中分数最高的页面。
- `actions` 是当前页面的测试点击入口，GUI 会按当前识别页面动态生成按钮。
- 模板可以配置 `region: {x, y, width, height}` 限定搜索范围，坐标仍是游戏逻辑坐标。

### `config/tasks/sortie.yaml`

出击规则由任务解释器调用：

- `maps`：地图配置。每个地图包含检测模板、地图选择坐标和开始出击坐标。
- `formation_points`：阵型按钮坐标。每次识别到 `formation` 页面，任务都会重新点击当前配置阵型并递增战斗计数。
- `points.return_home` / `points.proceed` / `points.retreat` / `points.sortie_entry`：通用点击点。
- `damage_rules.heavy_damage`：大破检测模板。命中且 `stop_on_heavy_damage=true` 时执行撤退策略。

当前出击任务依赖 `pages.yaml` 中至少能识别这些页面 key：`home`、`sortie_menu`、`formation`、`battle`、`battle_result`、`advance_or_retreat`。新增页面时先在 `pages.yaml` 配好识别，再在任务里决定是否纳入流程。

## 坐标和截图模型

当前截图模式是 `left_center_fixed`：

- 先通过 `pywin32` 获取 Poi 窗口客户区屏幕坐标。
- 从客户区左侧 `client.left + offset_x` 开始取图。
- 纵向用 `client.top + (client.height - capture_height) / 2 + offset_y` 居中裁剪。
- 裁剪区域尺寸由 `capture_width` 和 `capture_height` 决定，默认 `1200x720`。
- 如果 Poi 客户区小于固定裁剪区域，截图会报错，不会缩放凑合，以免点击坐标失真。

点击换算在 `DeviceController.click()` 中完成：逻辑坐标按最近一次截图的 `source_region` 映射到真实屏幕坐标，然后用 `pyautogui` 点击。

调试 GUI 的实时预览会叠加鼠标在游戏区域内的逻辑坐标，这是采集 YAML 坐标的主要工具。

## 图像识别注意事项

- `Recognizer.match_template()` 使用 OpenCV `cv2.matchTemplate`。
- Windows 中文路径下不要直接用 `cv2.imread(str(path))`，容易出现路径乱码或读取失败。当前代码使用 `np.fromfile(template_path, dtype=np.uint8)` 加 `cv2.imdecode(...)` 读取模板，后续不要退回 `cv2.imread`。
- 模板缺失时应返回 `missing_template` 并在 GUI/日志显示，不能让程序崩溃。
- 页面模板和任务模板目录不同：
  - 页面模板：`assets/templates/pages/...`
  - 出击/大破等任务模板：`assets/templates/sortie/...`
- PowerShell 终端可能把中文显示成乱码，但这不一定代表文件本身编码错误。需要确认编码时，用 Python 按 UTF-8 读取，或直接看 GUI/编辑器显示。

## GUI 结构

GUI 是 PySide6 三栏布局：

- 顶部：任务下拉、启动任务、停止任务。
- 左侧：一级功能导航，当前有 `出击`、`演习`、`远征`、`补给`、`入渠`、`软件调试`。
- 中间：随左侧功能切换的设置页。当前 `出击` 和 `软件调试` 内容较完整，其余是预留页。
- 右侧：始终显示实时游戏截图和日志。
- 软件调试页：目标窗口、截图裁剪、预览频率、停止快捷键、页面识别详情和当前页面动作测试按钮。

性能约束：

- 不要在 Qt 主线程里直接做截图、窗口枚举、OpenCV 模板匹配或长等待。
- 当前 GUI 使用两个单线程 `ThreadPoolExecutor`：
  - `poi-preview`：只负责截图预览。
  - `poi-page`：只负责页面匹配。
- 页面匹配从最新截图复制图像后异步执行，避免模板匹配拖慢预览帧率。
- `ScreenCapture` 会复用 `mss.mss()` 实例，关闭窗口时要调用 `capture.close()`。
- `WindowFinder` 会缓存目标 `hwnd`，减少每帧枚举窗口。
- 截图刷新频率和页面匹配频率要分开调；页面匹配过密会导致 UI 卡顿。

## 出击任务逻辑

`SortieTask` 是页面驱动的多战斗循环，大致流程：

1. 识别 `home`：点击母港出击入口。
2. 识别 `sortie_menu`：读取 `sortie.map` 对应的 `maps` 规则，检测地图模板，点击选择地图和开始出击。
3. 识别 `formation`：每次进入阵型页面都点击 `sortie.formation` 对应阵型，并递增 `battle_count`。
4. 识别 `battle` / `battle_result`：等待下一页面。
5. 识别 `advance_or_retreat`：检测大破和最大战斗次数；需要结束时撤退并回母港，否则点击进击。
6. 回到 `home` 且已经出击过时，任务结束。

遇到未知页面时任务会停止，避免盲点。新增特殊分支时优先通过 `pages.yaml` 增加页面识别，再扩展 `SortieTask.step()` 的页面分支。

## 开发和验证命令

常用检查：

```powershell
python -c "import pathlib; files=list(pathlib.Path('src').rglob('*.py')); [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in files]; print(f'checked {len(files)} files')"
git diff --check
```

运行 GUI：

```powershell
python -m poi_auto.app.main
```

如果未安装为包，通常需要先设置：

```powershell
$env:PYTHONPATH = "src"
python -m poi_auto.app.main
```

开发时优先使用 `rg` 查找文件和文本，不要把 `.venv/`、`__pycache__/`、模板图片和用户日志当成源码一起处理。

## 修改守则

- 修改文件前先看现有结构，保持模块边界，不要把点击逻辑写进识别层，也不要把游戏流程写进设备层。
- 不要删除或覆盖用户自己的模板图、坐标配置和已经保存的 YAML 值。
- `docs/使用说明.md` 保持用户友好；复杂架构说明、性能细节和交接信息写在本文件。
- 手动编辑代码时使用 `apply_patch`。
- 涉及 GUI 性能时，先确认是否有主线程阻塞、截图和页面匹配是否串行、是否重复创建 `mss` 或反复枚举窗口。
- 涉及中文路径或中文文案时，统一按 UTF-8 读写。PowerShell 显示乱码时不要直接判断为文件损坏。
- 新增任务时建议沿用现有分层：先配置页面识别，再写任务解释器，再在 GUI 加设置项和启动入口。

## 当前已知限制

- 真实自动出击是否稳定取决于用户提供的模板图和坐标；仓库里的很多地图、战斗、撤退坐标仍可能是占位值。
- 页面识别没有 OCR，也不会自动适配 UI 变化；模板失效时需要用户重新截图替换。
- 页面匹配阈值需要根据实际截图调试，过高会识别不到，过低可能误判。
- 当前只完整预留了出击任务框架，演习、远征、补给、入渠仍是 GUI 占位页。
