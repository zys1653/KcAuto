from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from poi_auto.device.window import Rect


@dataclass
class Screenshot:
    image: np.ndarray
    source_region: Rect
    scale_x: float
    scale_y: float


class ScreenCapture:
    def grab(self, region: Rect) -> np.ndarray:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError("需要安装 mss 才能截图。") from exc

        monitor = {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }
        with mss.mss() as sct:
            raw = np.array(sct.grab(monitor))
        return raw[:, :, :3][:, :, ::-1].copy()


def resize_to_logical(image: np.ndarray, width: int, height: int) -> np.ndarray:
    if image.shape[1] == width and image.shape[0] == height:
        return image
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("截图尺寸需要缩放时必须安装 opencv-python。") from exc
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)

