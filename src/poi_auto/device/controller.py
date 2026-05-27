from __future__ import annotations

from poi_auto.device.capture import ScreenCapture, Screenshot, resize_to_logical
from poi_auto.device.input import MouseController
from poi_auto.device.window import WindowFinder, game_region_from_client


class DeviceController:
    def __init__(self, config: dict) -> None:
        self.config = config
        window_config = config.get("window", {})
        input_config = config.get("input", {})
        self.window_finder = WindowFinder(
            title_keyword=window_config.get("title_keyword", "Poi"),
            selected_title=window_config.get("selected_title", ""),
            exclude_own_process=bool(window_config.get("exclude_own_process", True)),
        )
        self.capture = ScreenCapture()
        self.mouse = MouseController(input_config.get("move_duration_ms", 0))
        self.last_screenshot: Screenshot | None = None

    @property
    def logical_width(self) -> int:
        return int(self.config.get("game", {}).get("logical_width", 1200))

    @property
    def logical_height(self) -> int:
        return int(self.config.get("game", {}).get("logical_height", 720))

    def capture_game(self) -> Screenshot:
        game_config = self.config.get("game", {})
        crop_mode = game_config.get("crop_mode", "left_half")
        client = self.window_finder.find_client_rect()
        region = game_region_from_client(client, crop_mode)
        raw = self.capture.grab(region)
        image = resize_to_logical(raw, self.logical_width, self.logical_height)
        screenshot = Screenshot(
            image=image,
            source_region=region,
            scale_x=region.width / self.logical_width,
            scale_y=region.height / self.logical_height,
        )
        self.last_screenshot = screenshot
        return screenshot

    def click(self, x: int, y: int) -> tuple[int, int]:
        if self.last_screenshot is None:
            self.capture_game()
        assert self.last_screenshot is not None
        delay = int(self.config.get("input", {}).get("click_delay_ms", 300))
        return self.mouse.click_logical(
            x=x,
            y=y,
            logical_width=self.logical_width,
            logical_height=self.logical_height,
            source_region=self.last_screenshot.source_region,
            delay_ms=delay,
        )
