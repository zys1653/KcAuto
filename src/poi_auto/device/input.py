from __future__ import annotations

import time

from poi_auto.device.window import Rect


class MouseController:
    def __init__(self, move_duration_ms: int = 0) -> None:
        self.move_duration_ms = move_duration_ms

    def click_logical(
        self,
        x: int,
        y: int,
        logical_width: int,
        logical_height: int,
        source_region: Rect,
        delay_ms: int,
    ) -> tuple[int, int]:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("需要安装 pyautogui 才能模拟点击。") from exc

        screen_x = source_region.left + round(x * source_region.width / logical_width)
        screen_y = source_region.top + round(y * source_region.height / logical_height)
        duration = max(self.move_duration_ms, 0) / 1000
        pyautogui.click(screen_x, screen_y, duration=duration)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)
        return screen_x, screen_y

