from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import partial
import time
from threading import Event
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from poi_auto.core.config import default_paths, load_app_config, save_yaml
from poi_auto.core.context import RuntimeContext
from poi_auto.core.runner import TaskRunner
from poi_auto.device.capture import Screenshot
from poi_auto.device.controller import DeviceController
from poi_auto.device.window import Rect, WindowFinder
from poi_auto.tasks.sortie.task import SortieTask
from poi_auto.vision.pages import PageMatch, PageMatcher
from poi_auto.vision.recognizer import Recognizer


TASKS = [
    ("sortie", "出击"),
    ("practice", "演习"),
    ("expedition", "远征"),
    ("supply", "补给"),
    ("repair", "入渠"),
]

SECTIONS = TASKS + [("debug", "软件调试")]

FORMATIONS = [
    ("line_ahead", "单纵阵"),
    ("double_line", "复纵阵"),
    ("diamond", "轮形阵"),
    ("echelon", "梯形阵"),
    ("line_abreast", "单横阵"),
]

MAPS = [f"{world}-{area}" for world in range(1, 4) for area in range(1, 6)]


@dataclass(frozen=True)
class PreviewResult:
    screenshot: Screenshot
    page: PageMatch | None


class GuiEvents(QObject):
    log_message = Signal(str)
    state_message = Signal(str)
    stop_requested = Signal()
    preview_result = Signal(object)
    preview_error = Signal(str)


class GlobalHotkey:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.listener: Any | None = None
        self.sequence = ""

    def start(self, sequence: str) -> tuple[bool, str]:
        self.stop()
        self.sequence = sequence.strip()
        if not self.sequence:
            return False, "未设置停止快捷键。"
        try:
            from pynput import keyboard
        except ImportError:
            return False, "未安装 pynput，无法启用全局停止快捷键。"

        combo = self._to_pynput_sequence(self.sequence)
        try:
            self.listener = keyboard.GlobalHotKeys({combo: self.callback})
            self.listener.start()
        except Exception as exc:
            self.listener = None
            return False, f"停止快捷键注册失败：{exc}"
        return True, f"停止快捷键已启用：{self.sequence}"

    def stop(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def _to_pynput_sequence(self, sequence: str) -> str:
        parts = [part.strip().lower() for part in sequence.replace(" ", "").split("+") if part.strip()]
        aliases = {
            "ctrl": "<ctrl>",
            "control": "<ctrl>",
            "alt": "<alt>",
            "shift": "<shift>",
            "win": "<cmd>",
            "cmd": "<cmd>",
            "meta": "<cmd>",
        }
        return "+".join(aliases.get(part, part) for part in parts)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KC Automation Controller")
        self.paths = default_paths()
        self.config = load_app_config(self.paths)
        self.events = GuiEvents()
        self.events.log_message.connect(self.append_log)
        self.events.state_message.connect(self.state_label_set)
        self.events.stop_requested.connect(self.stop_task)
        self.events.preview_result.connect(self.apply_preview_result)
        self.events.preview_error.connect(self.handle_preview_error)
        self.runner = TaskRunner(self.build_context)
        self.hotkey = GlobalHotkey(lambda: self.events.stop_requested.emit())
        self.preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="poi-preview")
        self.preview_future: Future | None = None
        self.preview_context: RuntimeContext | None = None
        self.preview_context_key = ""
        self.last_preview_error_at = 0.0
        self.last_page_match_at = 0.0
        self.last_screenshot: Screenshot | None = None
        self.current_page: PageMatch | None = None

        self._build_controls()
        self.setCentralWidget(self.build_layout())
        self.refresh_window_list()
        self.start_hotkey()
        self.select_initial_section()
        self.update_preview_timer()
        self.append_log("程序已启动。实时截图已改为后台线程，界面操作不会再等待截图和模板匹配。")

    def _build_controls(self) -> None:
        window = self.config.get("window", {})
        game = self.config.get("game", {})
        input_config = self.config.get("input", {})
        vision = self.config.get("vision", {})
        sortie = self.config.get("sortie", {})
        hotkeys = self.config.get("hotkeys", {})
        preview = self.config.get("preview", {})

        self.task_combo = QComboBox()
        for task_id, label in TASKS:
            self.task_combo.addItem(label, task_id)
        self._set_combo_data(self.task_combo, self.config.get("task", {}).get("selected", "sortie"))

        self.start_button = QPushButton("启动任务")
        self.stop_button = QPushButton("停止任务")
        self.stop_button.setEnabled(False)
        self.state_label = QLabel("状态：空闲")
        self.start_button.clicked.connect(self.start_selected_task)
        self.stop_button.clicked.connect(self.stop_task)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(140)
        for section_id, label in SECTIONS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, section_id)
            self.nav_list.addItem(item)
        self.nav_list.currentRowChanged.connect(self.switch_section)
        self.stack = QStackedWidget()

        self.image_label = QLabel("实时预览会显示 Poi 游戏画面")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(640, 384)
        self.image_label.setStyleSheet("QLabel { background: #15171a; color: #d6d8dc; border: 1px solid #30343b; }")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self.sortie_map_combo = QComboBox()
        self.sortie_map_combo.addItems(MAPS)
        self.sortie_map_combo.setCurrentText(str(sortie.get("map", "1-1")))
        self.formation_combo = QComboBox()
        for key, label in FORMATIONS:
            self.formation_combo.addItem(label, key)
        self._set_combo_data(self.formation_combo, sortie.get("formation", "line_ahead"))
        self.stop_heavy_check = QCheckBox("大破撤退")
        self.stop_heavy_check.setChecked(bool(sortie.get("stop_on_heavy_damage", True)))
        self.max_battles_spin = self._spin(1, 20, int(sortie.get("max_battles", 6)))
        self.save_sortie_button = QPushButton("保存出击设置")
        self.save_sortie_button.clicked.connect(self.save_config_from_gui)

        self.title_keyword_edit = QLineEdit(str(window.get("title_keyword", "poi")))
        self.target_window_combo = QComboBox()
        self.refresh_windows_button = QPushButton("刷新窗口")
        self.refresh_windows_button.clicked.connect(self.refresh_window_list)
        self.exclude_own_check = QCheckBox("排除本软件窗口")
        self.exclude_own_check.setChecked(bool(window.get("exclude_own_process", True)))

        self.crop_mode_combo = QComboBox()
        self.crop_mode_combo.addItems(["left_center_fixed", "left_half", "full"])
        self.crop_mode_combo.setCurrentText(str(game.get("crop_mode", "left_center_fixed")))
        self.logical_width_spin = self._spin(100, 5000, int(game.get("logical_width", 1200)))
        self.logical_height_spin = self._spin(100, 5000, int(game.get("logical_height", 720)))
        self.capture_width_spin = self._spin(100, 5000, int(game.get("capture_width", 1200)))
        self.capture_height_spin = self._spin(100, 5000, int(game.get("capture_height", 720)))
        self.offset_x_spin = self._spin(-2000, 2000, int(game.get("offset_x", 0)))
        self.offset_y_spin = self._spin(-2000, 2000, int(game.get("offset_y", 0)))
        self.click_delay_spin = self._spin(0, 10000, int(input_config.get("click_delay_ms", 300)))
        self.move_duration_spin = self._spin(0, 5000, int(input_config.get("move_duration_ms", 0)))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.1, 1.0)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setValue(float(vision.get("match_threshold", 0.86)))

        self.preview_enabled_check = QCheckBox("实时预览")
        self.preview_enabled_check.setChecked(bool(preview.get("enabled", True)))
        self.preview_show_mouse_check = QCheckBox("突出鼠标位置")
        self.preview_show_mouse_check.setChecked(bool(preview.get("show_mouse", True)))
        self.preview_interval_spin = self._spin(100, 5000, int(preview.get("interval_ms", 250)))
        self.page_match_interval_spin = self._spin(250, 10000, int(preview.get("page_match_interval_ms", 1000)))
        self.preview_enabled_check.toggled.connect(self.update_preview_timer)
        self.preview_interval_spin.valueChanged.connect(self.update_preview_timer)

        self.stop_hotkey_edit = QLineEdit(str(hotkeys.get("stop", "Ctrl+Shift+S")))
        self.save_debug_button = QPushButton("保存调试设置")
        self.save_debug_button.clicked.connect(self.save_config_from_gui)
        self.refresh_screenshot_button = QPushButton("立即刷新截图")
        self.refresh_screenshot_button.clicked.connect(self.refresh_screenshot)

        self.page_label = QLabel("当前页面：unknown")
        self.page_detail_label = QLabel("命中：0/0  置信度：0.000")
        self.match_detail_view = QPlainTextEdit()
        self.match_detail_view.setReadOnly(True)
        self.match_detail_view.setMaximumHeight(160)
        self.action_buttons_widget = QWidget()
        self.action_buttons_layout = QVBoxLayout(self.action_buttons_widget)
        self.action_buttons_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self.refresh_preview_tick)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _set_combo_data(self, combo: QComboBox, value: Any) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value or combo.itemText(index) == value:
                combo.setCurrentIndex(index)
                return

    def build_layout(self) -> QWidget:
        root = QWidget()
        root_layout = QVBoxLayout(root)

        task_bar = QHBoxLayout()
        task_bar.addWidget(QLabel("任务"))
        task_bar.addWidget(self.task_combo)
        task_bar.addWidget(self.start_button)
        task_bar.addWidget(self.stop_button)
        task_bar.addStretch(1)
        task_bar.addWidget(self.state_label)
        root_layout.addLayout(task_bar)

        self.stack.addWidget(self.build_sortie_page())
        self.stack.addWidget(self.build_placeholder_page("演习"))
        self.stack.addWidget(self.build_placeholder_page("远征"))
        self.stack.addWidget(self.build_placeholder_page("补给"))
        self.stack.addWidget(self.build_placeholder_page("入渠"))
        self.stack.addWidget(self.build_debug_page())

        center = QSplitter(Qt.Horizontal)
        center.addWidget(self.nav_list)
        center.addWidget(self.stack)
        center.addWidget(self.build_right_panel())
        center.setSizes([140, 430, 680])
        root_layout.addWidget(center, 1)
        return root

    def build_sortie_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("出击设置")
        form = QFormLayout(group)
        form.addRow("出击海域", self.sortie_map_combo)
        form.addRow("阵型选择", self.formation_combo)
        form.addRow("最大战斗次数", self.max_battles_spin)
        form.addRow("", self.stop_heavy_check)
        layout.addWidget(group)
        layout.addWidget(self.save_sortie_button)
        layout.addStretch(1)
        return page

    def build_placeholder_page(self, title: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(f"{title} 设置页预留")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label, 1)
        return page

    def build_debug_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        window_group = QGroupBox("目标窗口")
        window_form = QFormLayout(window_group)
        window_form.addRow("标题关键字", self.title_keyword_edit)
        window_form.addRow("候选窗口", self.target_window_combo)
        window_form.addRow("", self.refresh_windows_button)
        window_form.addRow("", self.exclude_own_check)

        capture_group = QGroupBox("截图与输入")
        capture_form = QFormLayout(capture_group)
        capture_form.addRow("截图模式", self.crop_mode_combo)
        capture_form.addRow("逻辑宽度", self.logical_width_spin)
        capture_form.addRow("逻辑高度", self.logical_height_spin)
        capture_form.addRow("截图宽度", self.capture_width_spin)
        capture_form.addRow("截图高度", self.capture_height_spin)
        capture_form.addRow("水平偏移", self.offset_x_spin)
        capture_form.addRow("垂直偏移", self.offset_y_spin)
        capture_form.addRow("点击延迟 ms", self.click_delay_spin)
        capture_form.addRow("移动耗时 ms", self.move_duration_spin)
        capture_form.addRow("匹配阈值", self.threshold_spin)

        preview_group = QGroupBox("预览与热键")
        preview_form = QFormLayout(preview_group)
        preview_form.addRow("", self.preview_enabled_check)
        preview_form.addRow("", self.preview_show_mouse_check)
        preview_form.addRow("刷新间隔 ms", self.preview_interval_spin)
        preview_form.addRow("页面识别间隔 ms", self.page_match_interval_spin)
        preview_form.addRow("停止快捷键", self.stop_hotkey_edit)

        page_group = QGroupBox("页面识别")
        page_layout = QVBoxLayout(page_group)
        page_layout.addWidget(self.page_label)
        page_layout.addWidget(self.page_detail_label)
        page_layout.addWidget(QLabel("模板详情"))
        page_layout.addWidget(self.match_detail_view)
        page_layout.addWidget(QLabel("当前页面功能入口"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.action_buttons_widget)
        scroll.setMaximumHeight(120)
        page_layout.addWidget(scroll)

        layout.addWidget(window_group)
        layout.addWidget(capture_group)
        layout.addWidget(preview_group)
        layout.addWidget(self.refresh_screenshot_button)
        layout.addWidget(page_group)
        layout.addWidget(self.save_debug_button)
        layout.addStretch(1)
        return page

    def build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("实时截图"))
        layout.addWidget(self.image_label, 3)
        layout.addWidget(QLabel("日志"))
        layout.addWidget(self.log_view, 2)
        return panel

    def select_initial_section(self) -> None:
        active = self.config.get("ui", {}).get("active_section", "sortie")
        for row in range(self.nav_list.count()):
            if self.nav_list.item(row).data(Qt.UserRole) == active:
                self.nav_list.setCurrentRow(row)
                return
        self.nav_list.setCurrentRow(0)

    def switch_section(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        item = self.nav_list.item(row)
        if item is not None:
            self.config.setdefault("ui", {})["active_section"] = item.data(Qt.UserRole)

    def build_context(self, stop_event: Event, config: dict[str, Any] | None = None) -> RuntimeContext:
        if config is None:
            self.config = load_app_config(self.paths)
            config = self.config
        device = DeviceController(config)
        recognizer = Recognizer(
            templates_root=self.paths.templates,
            default_threshold=float(config.get("vision", {}).get("match_threshold", 0.86)),
        )
        page_matcher = PageMatcher.from_file(self.paths.pages, recognizer)
        return RuntimeContext(
            config=config,
            paths=self.paths,
            device=device,
            recognizer=recognizer,
            page_matcher=page_matcher,
            stop_event=stop_event,
            logger=self.thread_log,
        )

    def config_from_gui(self) -> dict[str, Any]:
        selected_title = self.target_window_combo.currentData() or ""
        config = dict(self.config)
        for key in ("window", "game", "input", "vision", "sortie", "hotkeys", "preview", "ui", "task"):
            config.setdefault(key, {})
        config["task"] = {**config["task"], "selected": self.task_combo.currentData()}
        current_item = self.nav_list.currentItem()
        if current_item is not None:
            config["ui"] = {**config["ui"], "active_section": current_item.data(Qt.UserRole)}
        config["window"] = {
            **config["window"],
            "title_keyword": self.title_keyword_edit.text().strip(),
            "selected_title": selected_title,
            "exclude_own_process": self.exclude_own_check.isChecked(),
        }
        config["game"] = {
            **config["game"],
            "logical_width": self.logical_width_spin.value(),
            "logical_height": self.logical_height_spin.value(),
            "capture_width": self.capture_width_spin.value(),
            "capture_height": self.capture_height_spin.value(),
            "offset_x": self.offset_x_spin.value(),
            "offset_y": self.offset_y_spin.value(),
            "crop_mode": self.crop_mode_combo.currentText(),
        }
        config["input"] = {
            **config["input"],
            "click_delay_ms": self.click_delay_spin.value(),
            "move_duration_ms": self.move_duration_spin.value(),
        }
        config["vision"] = {**config["vision"], "match_threshold": self.threshold_spin.value()}
        config["sortie"] = {
            **config["sortie"],
            "map": self.sortie_map_combo.currentText(),
            "formation": self.formation_combo.currentData(),
            "max_battles": self.max_battles_spin.value(),
            "stop_on_heavy_damage": self.stop_heavy_check.isChecked(),
        }
        config["hotkeys"] = {**config["hotkeys"], "stop": self.stop_hotkey_edit.text().strip()}
        config["preview"] = {
            **config["preview"],
            "enabled": self.preview_enabled_check.isChecked(),
            "interval_ms": self.preview_interval_spin.value(),
            "page_match_interval_ms": self.page_match_interval_spin.value(),
            "show_mouse": self.preview_show_mouse_check.isChecked(),
        }
        return config

    def refresh_window_list(self) -> None:
        current = self.config.get("window", {}).get("selected_title", "")
        self.target_window_combo.clear()
        try:
            finder = WindowFinder(
                title_keyword=self.title_keyword_edit.text(),
                selected_title=current,
                exclude_own_process=self.exclude_own_check.isChecked(),
            )
            windows = finder.list_windows()
        except Exception as exc:
            self.append_log(f"刷新窗口失败：{exc}")
            return

        for item in windows:
            self.target_window_combo.addItem(f"{item.title}  [pid:{item.pid}]", item.title)
        if not windows:
            self.append_log("没有找到匹配窗口。可以缩短标题关键字后再刷新。")
            return

        index = next((i for i in range(self.target_window_combo.count()) if self.target_window_combo.itemData(i) == current), 0)
        self.target_window_combo.setCurrentIndex(index)
        self.append_log(f"找到 {len(windows)} 个候选窗口，当前选择：{self.target_window_combo.currentData()}")

    def save_config_from_gui(self) -> None:
        self.config = self.config_from_gui()
        save_yaml(self.paths.config, self.config)
        self.append_log("配置已保存。")
        self.start_hotkey()
        self.update_preview_timer()

    def start_hotkey(self) -> None:
        hotkey = str(self.config.get("hotkeys", {}).get("stop", "Ctrl+Shift+S"))
        ok, message = self.hotkey.start(hotkey)
        self.append_log(message)
        if not ok:
            self.append_log("仍可使用界面上的停止按钮停止任务。")

    def update_preview_timer(self) -> None:
        if self.preview_enabled_check.isChecked():
            self.preview_timer.start(self.preview_interval_spin.value())
        else:
            self.preview_timer.stop()

    def start_selected_task(self) -> None:
        self.save_config_from_gui()
        task_id = self.task_combo.currentData()
        if task_id != "sortie":
            self.append_log(f"任务暂未实现：{self.task_combo.currentText()}")
            return
        if not self.runner.start(SortieTask(self.paths.sortie_rules)):
            self.append_log("任务已在运行。")
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.state_label.setText("状态：出击运行中")

    def stop_task(self) -> None:
        if self.runner.is_running:
            self.runner.stop()
            self.append_log("已请求停止任务。")
        else:
            self.append_log("当前没有运行中的任务。")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.state_label.setText("状态：停止中")

    def refresh_preview_tick(self) -> None:
        if self.preview_future is not None and not self.preview_future.done():
            return
        config = self.config_from_gui()
        should_match = self.should_update_page_match()
        self.preview_future = self.preview_executor.submit(self.capture_preview_job, config, should_match)
        self.preview_future.add_done_callback(self.on_preview_done)

    def should_update_page_match(self) -> bool:
        now = time.monotonic()
        interval = max(self.page_match_interval_spin.value() / 1000, 0.25)
        if now - self.last_page_match_at >= interval:
            self.last_page_match_at = now
            return True
        return False

    def capture_preview_job(self, config: dict[str, Any], should_match: bool) -> PreviewResult:
        context = self.preview_context_for(config)
        screenshot = context.device.capture_game()
        page = context.page_matcher.match(screenshot.image) if should_match and context.page_matcher else None
        return PreviewResult(screenshot=screenshot, page=page)

    def preview_context_for(self, config: dict[str, Any]) -> RuntimeContext:
        key = repr(config)
        if self.preview_context is None or key != self.preview_context_key:
            self.preview_context = self.build_context(Event(), config)
            self.preview_context_key = key
        return self.preview_context

    def on_preview_done(self, future: Future) -> None:
        try:
            self.events.preview_result.emit(future.result())
        except Exception as exc:
            self.events.preview_error.emit(str(exc))

    def apply_preview_result(self, result: object) -> None:
        if not isinstance(result, PreviewResult):
            return
        self.last_screenshot = result.screenshot
        self.show_screenshot(result.screenshot)
        if result.page is not None:
            self.current_page = result.page
            self.update_page_panel(result.page)
        self.last_preview_error_at = 0.0

    def handle_preview_error(self, message: str) -> None:
        now = time.monotonic()
        if now - self.last_preview_error_at > 3:
            self.append_log(f"实时预览失败：{message}")
            self.last_preview_error_at = now

    def refresh_screenshot(self) -> None:
        try:
            self.save_config_from_gui()
            context = self.build_context(Event())
            screenshot = context.device.capture_game()
            self.last_screenshot = screenshot
            self.show_screenshot(screenshot)
            page = context.page_matcher.match(screenshot.image) if context.page_matcher else None
            if page is not None:
                self.current_page = page
                self.update_page_panel(page)
            selected = context.device.window_finder.last_title
            self.append_log(
                "截图完成："
                f"window={selected}, "
                f"region=({screenshot.source_region.left},{screenshot.source_region.top},"
                f"{screenshot.source_region.width}x{screenshot.source_region.height}), "
                f"scale=({screenshot.scale_x:.3f},{screenshot.scale_y:.3f})"
            )
        except Exception as exc:
            self.append_log(f"截图失败：{exc}")
            QMessageBox.warning(self, "截图失败", str(exc))

    def update_page_panel(self, page: PageMatch) -> None:
        self.page_label.setText(f"当前页面：{page.name} ({page.key})")
        self.page_detail_label.setText(
            f"命中：{page.matched_count}/{page.required_count}  置信度：{page.score:.3f}  "
            f"更新时间：{page.updated_at.strftime('%H:%M:%S')}"
        )
        detail_lines = []
        for check in page.checks:
            status = "OK" if check.matched else "MISS"
            error = f" error={check.error}" if check.error else ""
            detail_lines.append(
                f"[{status}] {check.page_key}/{check.template} score={check.score:.3f} "
                f"threshold={check.threshold:.2f}{error}"
            )
        self.match_detail_view.setPlainText("\n".join(detail_lines) or "没有模板详情。")
        self.rebuild_action_buttons(page)

    def rebuild_action_buttons(self, page: PageMatch) -> None:
        while self.action_buttons_layout.count():
            item = self.action_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not page.is_known or not page.actions:
            self.action_buttons_layout.addWidget(QLabel("当前页面没有可用入口。"))
            return
        for action_key, action in page.actions.items():
            name = str(action.get("name", action_key))
            x = int(action.get("x", 0))
            y = int(action.get("y", 0))
            button = QPushButton(f"{name} ({x}, {y})")
            button.clicked.connect(partial(self.click_page_action, action_key, action))
            self.action_buttons_layout.addWidget(button)
        self.action_buttons_layout.addStretch(1)

    def click_page_action(self, action_key: str, action: dict[str, Any]) -> None:
        if self.current_page is None or not self.current_page.is_known:
            self.append_log("当前页面未知，入口点击已取消。")
            return
        try:
            context = self.build_context(Event(), self.config_from_gui())
            if self.last_screenshot is not None:
                context.device.last_screenshot = self.last_screenshot
            x = int(action.get("x", 0))
            y = int(action.get("y", 0))
            screen = context.device.click(x, y)
            self.append_log(f"入口点击：{self.current_page.key}.{action_key} logical=({x},{y}) screen={screen}")
        except Exception as exc:
            self.append_log(f"入口点击失败：{exc}")
            QMessageBox.warning(self, "入口点击失败", str(exc))

    def show_screenshot(self, screenshot_or_image: object) -> None:
        if isinstance(screenshot_or_image, Screenshot):
            image = screenshot_or_image.image
            source_region = screenshot_or_image.source_region
        elif isinstance(screenshot_or_image, np.ndarray):
            image = screenshot_or_image
            source_region = self.last_screenshot.source_region if self.last_screenshot else None
        else:
            return
        height, width, channels = image.shape
        bytes_per_line = channels * width
        qimage = QImage(image.data, width, height, bytes_per_line, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimage)
        if source_region is not None and self.preview_show_mouse_check.isChecked():
            self.draw_mouse_marker(pixmap, source_region, width, height)
        self.image_label.setPixmap(
            pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def draw_mouse_marker(self, pixmap: QPixmap, source_region: Rect, width: int, height: int) -> None:
        mouse = self.current_mouse_position()
        if mouse is None:
            return
        screen_x, screen_y = mouse
        if not source_region.contains(screen_x, screen_y):
            return

        logical_x = round((screen_x - source_region.left) * width / source_region.width)
        logical_y = round((screen_y - source_region.top) * height / source_region.height)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 64, 64), 2))
        painter.drawLine(logical_x - 18, logical_y, logical_x + 18, logical_y)
        painter.drawLine(logical_x, logical_y - 18, logical_x, logical_y + 18)
        painter.drawEllipse(logical_x - 10, logical_y - 10, 20, 20)
        painter.setFont(QFont("Consolas", 12))
        painter.fillRect(logical_x + 12, logical_y + 12, 92, 24, QColor(0, 0, 0, 170))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(logical_x + 18, logical_y + 30, f"{logical_x}, {logical_y}")
        painter.end()

    def current_mouse_position(self) -> tuple[int, int] | None:
        try:
            import pyautogui
        except ImportError:
            return None
        position = pyautogui.position()
        return int(position.x), int(position.y)

    def thread_log(self, message: str) -> None:
        self.events.log_message.emit(message)

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")
        if message.startswith("任务结束"):
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.state_label.setText("状态：空闲")

    def state_label_set(self, message: str) -> None:
        self.state_label.setText(message)

    def closeEvent(self, event: Any) -> None:
        self.preview_timer.stop()
        if self.preview_future is not None:
            self.preview_future.cancel()
        if self.preview_context is not None:
            self.preview_context.device.capture.close()
        self.preview_executor.shutdown(wait=False, cancel_futures=True)
        self.hotkey.stop()
        self.runner.stop()
        super().closeEvent(event)
