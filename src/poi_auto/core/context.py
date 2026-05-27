from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable

from poi_auto.core.config import AppPaths
from poi_auto.device.controller import DeviceController
from poi_auto.vision.recognizer import Recognizer

LogCallback = Callable[[str], None]


@dataclass
class RuntimeContext:
    config: dict[str, Any]
    paths: AppPaths
    device: DeviceController
    recognizer: Recognizer
    stop_event: Event
    logger: LogCallback
    flags: dict[str, bool] = field(default_factory=dict)

    def log(self, message: str) -> None:
        self.logger(message)

