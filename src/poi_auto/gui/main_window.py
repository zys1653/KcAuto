from __future__ import annotations

from datetime import datetime
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
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
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
from poi_auto.vision.recognizer import Recognizer


class GuiEvents(QObject):
    log_message = Signal(str)
    state_message = Signal(str)
    stop_requested = Signal()


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
        self.runner = TaskRunner(self.build_context)
        self.hotkey = GlobalHotkey(lambda: self.events.stop_requested.emit())
        self.last_preview_error_at = 0.0
        self.last_screenshot: Screenshot | None = None

        self.state_label = QLabel("状态：空闲")
        self.image_label = QLabel("实时预览会显示 Poi 游戏画面")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(720, 432)
        self.image_label.setStyleSheet("QLabel { background: #15171a; color: #d6d8dc; border: 1px solid #30343b; }")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self._build_config_widgets()

        self.refresh_button = QPushButton("刷新截图")
        self.start_button = QPushButton("启动出击")
        self.step_button = QPushButton("单步执行")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)

        self.refresh_button.clicked.connect(self.refresh_screenshot)
        self.start_button.clicked.connect(self.start_sortie)
        self.step_button.clicked.connect(self.step_sortie)
        self.stop_button.clicked.connect(self.stop_task)
        self.preview_enabled_check.toggled.connect(self.update_preview_timer)
        self.preview_interval_spin.valueChanged.connect(self.update_preview_timer)

        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self.refresh_preview_tick)

        self.setCentralWidget(self.build_layout())
        self.refresh_window_list()
        self.start_hotkey()
        self.update_preview_timer()
        self.append_log("程序已启动。请选择 Poi 目标窗口；实时预览会裁出左侧 1200x720 游戏画面。")

    def _build_config_widgets(self) -> None:
        window = self.config.get("window", {})
        game = self.config.get("game", {})
        input_config = self.config.get("input", {})
        vision = self.config.get("vision", {})
        sortie = self.config.get("sortie", {})
        hotkeys = self.config.get("hotkeys", {})
        preview = self.config.get("preview", {})

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

        self.formation_combo = QComboBox()
        self.formation_combo.addItems(["line_ahead", "double_line", "diamond", "echelon", "line_abreast"])
        self.formation_combo.setCurrentText(str(sortie.get("formation", "line_ahead")))
        self.max_runs_spin = self._spin(1, 999, int(sortie.get("max_runs", 10)))
        self.stop_heavy_check = QCheckBox("大破停止")
        self.stop_heavy_check.setChecked(bool(sortie.get("stop_on_heavy_damage", True)))
        self.retreat_medium_check = QCheckBox("中破撤退")
        self.retreat_medium_check.setChecked(bool(sortie.get("retreat_on_medium_damage", False)))

        self.stop_hotkey_edit = QLineEdit(str(hotkeys.get("stop", "Ctrl+Shift+S")))
        self.preview_enabled_check = QCheckBox("实时预览")
        self.preview_enabled_check.setChecked(bool(preview.get("enabled", True)))
        self.preview_show_mouse_check = QCheckBox("突出鼠标位置")
        self.preview_show_mouse_check.setChecked(bool(preview.get("show_mouse", True)))
        self.preview_interval_spin = self._spin(50, 5000, int(preview.get("interval_ms", 200)))
        self.save_config_button = QPushButton("保存配置")
        self.save_config_button.clicked.connect(self.save_config_from_gui)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def build_layout(self) -> QWidget:
        root = QWidget()
        root_layout = QVBoxLayout(root)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.step_button)
        toolbar.addWidget(self.stop_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.state_label)
        root_layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.build_config_panel())
        left_layout.addWidget(QLabel("运行日志"))
        left_layout.addWidget(self.log_view, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("游戏截图预览"))
        right_layout.addWidget(self.image_label, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([460, 720])
        root_layout.addWidget(splitter, 1)
        return root

    def build_config_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        window_group = QGroupBox("目标窗口")
        window_form = QFormLayout(window_group)
        window_form.addRow("标题关键字", self.title_keyword_edit)
        window_form.addRow("候选窗口", self.target_window_combo)
        window_form.addRow("", self.refresh_windows_button)
        window_form.addRow("", self.exclude_own_check)

        game_group = QGroupBox("画面与输入")
        game_form = QFormLayout(game_group)
        game_form.addRow("截图模式", self.crop_mode_combo)
        game_form.addRow("逻辑宽度", self.logical_width_spin)
        game_form.addRow("逻辑高度", self.logical_height_spin)
        game_form.addRow("截图宽度", self.capture_width_spin)
        game_form.addRow("截图高度", self.capture_height_spin)
        game_form.addRow("水平偏移", self.offset_x_spin)
        game_form.addRow("垂直偏移", self.offset_y_spin)
        game_form.addRow("点击延迟 ms", self.click_delay_spin)
        game_form.addRow("移动耗时 ms", self.move_duration_spin)
        game_form.addRow("匹配阈值", self.threshold_spin)

        preview_group = QGroupBox("预览")
        preview_form = QFormLayout(preview_group)
        preview_form.addRow("", self.preview_enabled_check)
        preview_form.addRow("", self.preview_show_mouse_check)
        preview_form.addRow("刷新间隔 ms", self.preview_interval_spin)

        sortie_group = QGroupBox("出击")
        sortie_form = QFormLayout(sortie_group)
        sortie_form.addRow("阵型", self.formation_combo)
        sortie_form.addRow("最大轮次", self.max_runs_spin)
        sortie_form.addRow("", self.stop_heavy_check)
        sortie_form.addRow("", self.retreat_medium_check)
        sortie_form.addRow("停止快捷键", self.stop_hotkey_edit)

        layout.addWidget(window_group)
        layout.addWidget(game_group)
        layout.addWidget(preview_group)
        layout.addWidget(sortie_group)
        layout.addWidget(self.save_config_button)
        return panel

    def build_context(self, stop_event: Event, config: dict[str, Any] | None = None) -> RuntimeContext:
        if config is None:
            self.config = load_app_config(self.paths)
            config = self.config
        device = DeviceController(config)
        recognizer = Recognizer(
            templates_root=self.paths.templates,
            default_threshold=float(config.get("vision", {}).get("match_threshold", 0.86)),
        )
        return RuntimeContext(
            config=config,
            paths=self.paths,
            device=device,
            recognizer=recognizer,
            stop_event=stop_event,
            logger=self.thread_log,
        )

    def config_from_gui(self) -> dict[str, Any]:
        selected_title = self.target_window_combo.currentData() or ""
        config = dict(self.config)
        config.setdefault("window", {})
        config.setdefault("game", {})
        config.setdefault("input", {})
        config.setdefault("vision", {})
        config.setdefault("sortie", {})
        config.setdefault("hotkeys", {})
        config.setdefault("preview", {})
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
        config["vision"] = {
            **config["vision"],
            "match_threshold": self.threshold_spin.value(),
        }
        config["sortie"] = {
            **config["sortie"],
            "formation": self.formation_combo.currentText(),
            "max_runs": self.max_runs_spin.value(),
            "stop_on_heavy_damage": self.stop_heavy_check.isChecked(),
            "retreat_on_medium_damage": self.retreat_medium_check.isChecked(),
        }
        config["hotkeys"] = {
            **config["hotkeys"],
            "stop": self.stop_hotkey_edit.text().strip(),
        }
        config["preview"] = {
            **config["preview"],
            "enabled": self.preview_enabled_check.isChecked(),
            "interval_ms": self.preview_interval_spin.value(),
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
        selected_title = self.config.get("window", {}).get("selected_title", "")
        self.append_log(f"配置已保存：目标窗口={selected_title or '按关键字自动匹配'}")
        self.start_hotkey()
        self.update_preview_timer()

    def start_hotkey(self) -> None:
        hotkey = str(self.config.get("hotkeys", {}).get("stop", "Ctrl+Shift+S"))
        ok, message = self.hotkey.start(hotkey)
        self.append_log(message)
        if not ok:
            self.append_log("仍可使用界面上的“停止”按钮停止任务。")

    def update_preview_timer(self) -> None:
        if self.preview_enabled_check.isChecked():
            self.preview_timer.start(self.preview_interval_spin.value())
        else:
            self.preview_timer.stop()

    def refresh_preview_tick(self) -> None:
        try:
            context = self.build_context(Event(), self.config_from_gui())
            screenshot = context.device.capture_game()
            self.last_screenshot = screenshot
            self.show_screenshot(screenshot)
            self.last_preview_error_at = 0.0
        except Exception as exc:
            now = time.monotonic()
            if now - self.last_preview_error_at > 3:
                self.append_log(f"实时预览失败：{exc}")
                self.last_preview_error_at = now

    def refresh_screenshot(self) -> None:
        try:
            self.save_config_from_gui()
            context = self.build_context(Event())
            screenshot = context.device.capture_game()
            self.last_screenshot = screenshot
            self.show_screenshot(screenshot)
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

    def start_sortie(self) -> None:
        self.save_config_from_gui()
        task = SortieTask(self.paths.sortie_rules)
        if not self.runner.start(task):
            self.append_log("任务已在运行。")
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.events.state_message.emit("状态：出击运行中")

    def step_sortie(self) -> None:
        try:
            self.save_config_from_gui()
            context = self.build_context(Event())
            task = SortieTask(self.paths.sortie_rules)
            self.events.state_message.emit("状态：单步执行")
            task.step(context)
            if context.device.last_screenshot is not None:
                self.last_screenshot = context.device.last_screenshot
                self.show_screenshot(context.device.last_screenshot)
            self.events.state_message.emit("状态：空闲")
        except Exception as exc:
            self.append_log(f"单步执行失败：{exc}")
            QMessageBox.warning(self, "单步执行失败", str(exc))

    def stop_task(self) -> None:
        if self.runner.is_running:
            self.runner.stop()
            self.append_log("已请求停止任务。")
        else:
            self.append_log("当前没有运行中的任务。")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.events.state_message.emit("状态：停止中")

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
        pen = QPen(QColor(255, 64, 64), 2)
        painter.setPen(pen)
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
        if "结束" in message or "空闲" in message:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def closeEvent(self, event: Any) -> None:
        self.preview_timer.stop()
        self.hotkey.stop()
        self.runner.stop()
        super().closeEvent(event)
