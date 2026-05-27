from __future__ import annotations

from datetime import datetime
from threading import Event
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
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
from poi_auto.device.controller import DeviceController
from poi_auto.device.window import WindowFinder
from poi_auto.tasks.sortie.task import SortieTask
from poi_auto.vision.recognizer import Recognizer


class GuiEvents(QObject):
    log_message = Signal(str)
    screenshot_ready = Signal(object)
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
        converted: list[str] = []
        aliases = {
            "ctrl": "<ctrl>",
            "control": "<ctrl>",
            "alt": "<alt>",
            "shift": "<shift>",
            "win": "<cmd>",
            "cmd": "<cmd>",
            "meta": "<cmd>",
        }
        for part in parts:
            converted.append(aliases.get(part, part))
        return "+".join(converted)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KC Automation Controller")
        self.paths = default_paths()
        self.config = load_app_config(self.paths)
        self.events = GuiEvents()
        self.events.log_message.connect(self.append_log)
        self.events.screenshot_ready.connect(self.show_screenshot)
        self.events.state_message.connect(self.state_label_set)
        self.events.stop_requested.connect(self.stop_task)
        self.runner = TaskRunner(self.build_context)
        self.hotkey = GlobalHotkey(lambda: self.events.stop_requested.emit())

        self.state_label = QLabel("状态：空闲")
        self.image_label = QLabel("点击“刷新截图”预览 Poi 左半侧游戏画面")
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

        self.setCentralWidget(self.build_layout())
        self.refresh_window_list()
        self.start_hotkey()
        self.append_log("程序已启动。请先在左侧选择 Poi 目标窗口并保存配置。")

    def _build_config_widgets(self) -> None:
        window = self.config.get("window", {})
        game = self.config.get("game", {})
        input_config = self.config.get("input", {})
        vision = self.config.get("vision", {})
        sortie = self.config.get("sortie", {})
        hotkeys = self.config.get("hotkeys", {})

        self.title_keyword_edit = QLineEdit(str(window.get("title_keyword", "poi")))
        self.target_window_combo = QComboBox()
        self.refresh_windows_button = QPushButton("刷新窗口")
        self.refresh_windows_button.clicked.connect(self.refresh_window_list)
        self.exclude_own_check = QCheckBox("排除本软件窗口")
        self.exclude_own_check.setChecked(bool(window.get("exclude_own_process", True)))

        self.crop_mode_combo = QComboBox()
        self.crop_mode_combo.addItems(["left_half", "full"])
        self.crop_mode_combo.setCurrentText(str(game.get("crop_mode", "left_half")))
        self.logical_width_spin = QSpinBox()
        self.logical_width_spin.setRange(100, 5000)
        self.logical_width_spin.setValue(int(game.get("logical_width", 1200)))
        self.logical_height_spin = QSpinBox()
        self.logical_height_spin.setRange(100, 5000)
        self.logical_height_spin.setValue(int(game.get("logical_height", 720)))

        self.click_delay_spin = QSpinBox()
        self.click_delay_spin.setRange(0, 10000)
        self.click_delay_spin.setValue(int(input_config.get("click_delay_ms", 300)))
        self.move_duration_spin = QSpinBox()
        self.move_duration_spin.setRange(0, 5000)
        self.move_duration_spin.setValue(int(input_config.get("move_duration_ms", 0)))

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.1, 1.0)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setValue(float(vision.get("match_threshold", 0.86)))

        self.formation_combo = QComboBox()
        self.formation_combo.addItems(["line_ahead", "double_line", "diamond", "echelon", "line_abreast"])
        self.formation_combo.setCurrentText(str(sortie.get("formation", "line_ahead")))
        self.max_runs_spin = QSpinBox()
        self.max_runs_spin.setRange(1, 999)
        self.max_runs_spin.setValue(int(sortie.get("max_runs", 10)))
        self.stop_heavy_check = QCheckBox("大破停止")
        self.stop_heavy_check.setChecked(bool(sortie.get("stop_on_heavy_damage", True)))
        self.retreat_medium_check = QCheckBox("中破撤退")
        self.retreat_medium_check.setChecked(bool(sortie.get("retreat_on_medium_damage", False)))

        self.stop_hotkey_edit = QLineEdit(str(hotkeys.get("stop", "Ctrl+Shift+S")))
        self.save_config_button = QPushButton("保存配置")
        self.save_config_button.clicked.connect(self.save_config_from_gui)

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
        splitter.setSizes([440, 740])
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
        game_form.addRow("点击延迟 ms", self.click_delay_spin)
        game_form.addRow("移动耗时 ms", self.move_duration_spin)
        game_form.addRow("匹配阈值", self.threshold_spin)

        sortie_group = QGroupBox("出击")
        sortie_form = QFormLayout(sortie_group)
        sortie_form.addRow("阵型", self.formation_combo)
        sortie_form.addRow("最大轮次", self.max_runs_spin)
        sortie_form.addRow("", self.stop_heavy_check)
        sortie_form.addRow("", self.retreat_medium_check)
        sortie_form.addRow("停止快捷键", self.stop_hotkey_edit)

        layout.addWidget(window_group)
        layout.addWidget(game_group)
        layout.addWidget(sortie_group)
        layout.addWidget(self.save_config_button)
        return panel

    def build_context(self, stop_event: Event) -> RuntimeContext:
        self.config = load_app_config(self.paths)
        device = DeviceController(self.config)
        recognizer = Recognizer(
            templates_root=self.paths.templates,
            default_threshold=float(self.config.get("vision", {}).get("match_threshold", 0.86)),
        )
        return RuntimeContext(
            config=self.config,
            paths=self.paths,
            device=device,
            recognizer=recognizer,
            stop_event=stop_event,
            logger=self.thread_log,
        )

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
        selected_title = self.target_window_combo.currentData() or ""
        self.config.setdefault("window", {})
        self.config.setdefault("game", {})
        self.config.setdefault("input", {})
        self.config.setdefault("vision", {})
        self.config.setdefault("sortie", {})
        self.config.setdefault("hotkeys", {})

        self.config["window"].update(
            {
                "title_keyword": self.title_keyword_edit.text().strip(),
                "selected_title": selected_title,
                "exclude_own_process": self.exclude_own_check.isChecked(),
            }
        )
        self.config["game"].update(
            {
                "logical_width": self.logical_width_spin.value(),
                "logical_height": self.logical_height_spin.value(),
                "crop_mode": self.crop_mode_combo.currentText(),
            }
        )
        self.config["input"].update(
            {
                "click_delay_ms": self.click_delay_spin.value(),
                "move_duration_ms": self.move_duration_spin.value(),
            }
        )
        self.config["vision"]["match_threshold"] = self.threshold_spin.value()
        self.config["sortie"].update(
            {
                "formation": self.formation_combo.currentText(),
                "max_runs": self.max_runs_spin.value(),
                "stop_on_heavy_damage": self.stop_heavy_check.isChecked(),
                "retreat_on_medium_damage": self.retreat_medium_check.isChecked(),
            }
        )
        self.config["hotkeys"]["stop"] = self.stop_hotkey_edit.text().strip()
        save_yaml(self.paths.config, self.config)
        self.append_log(f"配置已保存：目标窗口={selected_title or '按关键字自动匹配'}")
        self.start_hotkey()

    def start_hotkey(self) -> None:
        hotkey = str(self.config.get("hotkeys", {}).get("stop", "Ctrl+Shift+S"))
        ok, message = self.hotkey.start(hotkey)
        self.append_log(message)
        if not ok:
            self.append_log("仍可使用界面上的“停止”按钮停止任务。")

    def refresh_screenshot(self) -> None:
        try:
            self.save_config_from_gui()
            context = self.build_context(Event())
            screenshot = context.device.capture_game()
            self.show_screenshot(screenshot.image)
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
                self.show_screenshot(context.device.last_screenshot.image)
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

    def show_screenshot(self, image: object) -> None:
        if not isinstance(image, np.ndarray):
            return
        height, width, channels = image.shape
        bytes_per_line = channels * width
        qimage = QImage(image.data, width, height, bytes_per_line, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimage)
        self.image_label.setPixmap(
            pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

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
        self.hotkey.stop()
        self.runner.stop()
        super().closeEvent(event)
