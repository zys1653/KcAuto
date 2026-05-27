from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


class WindowFinder:
    def __init__(self, title_keyword: str) -> None:
        self.title_keyword = title_keyword

    def find_client_rect(self) -> Rect:
        try:
            import win32gui
        except ImportError as exc:
            raise RuntimeError("需要安装 pywin32 才能定位 Windows 窗口。") from exc

        matches: list[tuple[int, str]] = []

        def enum_handler(hwnd: int, _extra: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if self.title_keyword.lower() in title.lower():
                matches.append((hwnd, title))

        win32gui.EnumWindows(enum_handler, None)
        if not matches:
            raise RuntimeError(f"未找到标题包含“{self.title_keyword}”的窗口。")

        hwnd, title = matches[0]
        left_top = win32gui.ClientToScreen(hwnd, (0, 0))
        right, bottom = win32gui.GetClientRect(hwnd)[2:]
        if right <= 0 or bottom <= 0:
            raise RuntimeError(f"窗口客户区大小异常：{title}")
        return Rect(left=left_top[0], top=left_top[1], width=right, height=bottom)


def game_region_from_client(client: Rect, crop_mode: str) -> Rect:
    if crop_mode == "left_half":
        return Rect(client.left, client.top, client.width // 2, client.height)
    if crop_mode == "full":
        return client
    raise ValueError(f"不支持的 crop_mode: {crop_mode}")

