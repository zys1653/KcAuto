from __future__ import annotations

from datetime import datetime
from threading import Event

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from poi_auto.core.config import default_paths, load_app_config
from poi_auto.core.context import RuntimeContext
from poi_auto.core.runner import TaskRunner
from poi_auto.device.controller import DeviceController
from poi_auto.tasks.sortie.task import SortieTask
from poi_auto.vision.recognizer import Recognizer


class GuiEvents(QObject):
    log_message = Signal(str)
    screenshot_ready = Signal(object)
    state_message = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Poi 图像识别自动化")
        self.paths = default_paths()
        self.config = load_app_config(self.paths)
        self.events = GuiEvents()
        self.events.log_message.connect(self.append_log)
        self.events.screenshot_ready.connect(self.show_screenshot)
        self.events.state_message.connect(self.state_label_set)
        self.runner = TaskRunner(self.build_context)

        self.state_label = QLabel("状态：空闲")
        self.image_label = QLabel("点击“刷新截图”预览 Poi 左半侧游戏画面")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(720, 432)
        self.image_label.setStyleSheet("QLabel { background: #15171a; color: #d6d8dc; border: 1px solid #30343b; }")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.config_view = QPlainTextEdit()
        self.config_view.setReadOnly(True)
        self.config_view.setPlainText(self.config_summary())

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
        self.append_log("程序已启动。请先确认 config/default.yaml 中的窗口标题关键字。")

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
        left_layout.addWidget(QLabel("配置摘要"))
        left_layout.addWidget(self.config_view)
        left_layout.addWidget(QLabel("运行日志"))
        left_layout.addWidget(self.log_view)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("游戏截图预览"))
        right_layout.addWidget(self.image_label, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([430, 750])
        root_layout.addWidget(splitter, 1)
        return root

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

    def config_summary(self) -> str:
        window = self.config.get("window", {})
        game = self.config.get("game", {})
        sortie = self.config.get("sortie", {})
        return (
            f"窗口标题关键字：{window.get('title_keyword')}\n"
            f"截图模式：{game.get('crop_mode')}\n"
            f"逻辑分辨率：{game.get('logical_width')}x{game.get('logical_height')}\n"
            f"阵型：{sortie.get('formation')}\n"
            f"最大出击轮次：{sortie.get('max_runs')}\n"
            f"大破停止：{sortie.get('stop_on_heavy_damage')}\n"
            f"中破撤退：{sortie.get('retreat_on_medium_damage')}\n"
        )

    def refresh_screenshot(self) -> None:
        try:
            context = self.build_context(Event())
            screenshot = context.device.capture_game()
            self.show_screenshot(screenshot.image)
            self.append_log(
                "截图完成："
                f"region=({screenshot.source_region.left},{screenshot.source_region.top},"
                f"{screenshot.source_region.width}x{screenshot.source_region.height}), "
                f"scale=({screenshot.scale_x:.3f},{screenshot.scale_y:.3f})"
            )
        except Exception as exc:
            self.append_log(f"截图失败：{exc}")
            QMessageBox.warning(self, "截图失败", str(exc))

    def start_sortie(self) -> None:
        task = SortieTask(self.paths.sortie_rules)
        if not self.runner.start(task):
            self.append_log("任务已在运行。")
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.events.state_message.emit("状态：出击运行中")

    def step_sortie(self) -> None:
        try:
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
        self.runner.stop()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.events.state_message.emit("状态：停止中")
        self.append_log("已请求停止任务。")

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

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)

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
