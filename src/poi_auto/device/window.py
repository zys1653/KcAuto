from __future__ import annotations

from dataclasses import dataclass
import os


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


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int


class WindowFinder:
    def __init__(
        self,
        title_keyword: str,
        selected_title: str = "",
        exclude_own_process: bool = True,
    ) -> None:
        self.title_keyword = title_keyword
        self.selected_title = selected_title
        self.exclude_own_process = exclude_own_process
        self.last_title = ""

    def list_windows(self) -> list[WindowInfo]:
        try:
            import win32gui
            import win32process
        except ImportError as exc:
            raise RuntimeError("需要安装 pywin32 才能定位 Windows 窗口。") from exc

        keyword = self.title_keyword.strip().lower()
        own_pid = os.getpid()
        matches: list[WindowInfo] = []

        def enum_handler(hwnd: int, _extra: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
            if self.exclude_own_process and pid == own_pid:
                return
            if keyword and keyword not in title.lower():
                return
            matches.append(WindowInfo(hwnd=hwnd, title=title, pid=pid))

        win32gui.EnumWindows(enum_handler, None)
        return matches

    def find_client_rect(self) -> Rect:
        try:
            import win32gui
        except ImportError as exc:
            raise RuntimeError("需要安装 pywin32 才能定位 Windows 窗口。") from exc

        matches = self.list_windows()
        if not matches:
            raise RuntimeError(f"未找到标题包含“{self.title_keyword}”的外部窗口。")

        selected_title = self.selected_title.strip()
        selected = next((item for item in matches if item.title == selected_title), None)
        target = selected or matches[0]

        hwnd = target.hwnd
        title = target.title
        left_top = win32gui.ClientToScreen(hwnd, (0, 0))
        right, bottom = win32gui.GetClientRect(hwnd)[2:]
        if right <= 0 or bottom <= 0:
            raise RuntimeError(f"窗口客户区大小异常：{title}")
        self.last_title = title
        return Rect(left=left_top[0], top=left_top[1], width=right, height=bottom)


def game_region_from_client(client: Rect, crop_mode: str) -> Rect:
    if crop_mode == "left_half":
        return Rect(client.left, client.top, client.width // 2, client.height)
    if crop_mode == "full":
        return client
    raise ValueError(f"不支持的 crop_mode: {crop_mode}")
