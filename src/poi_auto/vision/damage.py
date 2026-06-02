from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class DamageStateResult:
    state: str
    count: int
    score: float
    template_path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class DamageReport:
    states: dict[str, DamageStateResult] = field(default_factory=dict)
    hp_log: str = ""
    ocr_error: str = ""

    def count(self, state: str) -> int:
        result = self.states.get(state)
        return result.count if result else 0

    def has_any(self, states: list[str]) -> bool:
        return any(self.count(state) > 0 for state in states)

    def summary(self) -> str:
        parts = [f"{state}={result.count}" for state, result in self.states.items()]
        if self.hp_log:
            parts.append(f"hp={self.hp_log}")
        if self.ocr_error:
            parts.append(f"ocr={self.ocr_error}")
        return ", ".join(parts) if parts else "no damage rules"


class DamageDetector:
    def __init__(self, templates_root: Path) -> None:
        self.templates_root = templates_root

    def detect(
        self,
        image: np.ndarray,
        rules: dict[str, Any],
        *,
        hp_ocr_enabled: bool = False,
    ) -> DamageReport:
        states: dict[str, DamageStateResult] = {}
        for state, rule in (rules.get("states", {}) or {}).items():
            if isinstance(rule, dict):
                states[str(state)] = self._detect_state(str(state), image, rule)

        hp_log = ""
        ocr_error = ""
        if hp_ocr_enabled:
            hp_rule = rules.get("hp_ocr", {}) or {}
            hp_log, ocr_error = self._ocr_hp(image, hp_rule)

        return DamageReport(states=states, hp_log=hp_log, ocr_error=ocr_error)

    def _detect_state(self, state: str, image: np.ndarray, rule: dict[str, Any]) -> DamageStateResult:
        try:
            import cv2
        except ImportError:
            return DamageStateResult(state, 0, 0.0, error="opencv_not_installed")

        template_path = self.templates_root / Path(str(rule.get("template", "")))
        if not template_path.exists():
            return DamageStateResult(state, 0, 0.0, template_path=template_path, error="missing_template")

        source, offset_x, offset_y = self._crop(image, rule.get("region"))
        if source.size == 0:
            return DamageStateResult(state, 0, 0.0, template_path=template_path, error="invalid_region")

        source_bgr = cv2.cvtColor(source, cv2.COLOR_RGB2BGR)
        tpl_bytes = np.fromfile(template_path, dtype=np.uint8)
        template = cv2.imdecode(tpl_bytes, cv2.IMREAD_COLOR)
        if template is None:
            return DamageStateResult(state, 0, 0.0, template_path=template_path, error="unreadable_template")
        if source_bgr.shape[0] < template.shape[0] or source_bgr.shape[1] < template.shape[1]:
            return DamageStateResult(state, 0, 0.0, template_path=template_path, error="template_larger_than_source")

        threshold = float(rule.get("threshold", 0.86))
        result = cv2.matchTemplate(source_bgr, template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= threshold)
        points = [(int(x) + offset_x, int(y) + offset_y, float(result[y, x])) for y, x in zip(*locations)]
        kept = self._suppress_overlaps(points, template.shape[1], template.shape[0])
        best = max((score for _x, _y, score in kept), default=float(result.max()) if result.size else 0.0)
        return DamageStateResult(state, len(kept), best, template_path=template_path)

    def _crop(self, image: np.ndarray, region: dict[str, Any] | None) -> tuple[np.ndarray, int, int]:
        if not region:
            return image, 0, 0
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        width = int(region.get("width", image.shape[1] - x))
        height = int(region.get("height", image.shape[0] - y))
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            return image[0:0, 0:0], x, y
        return image[y : y + height, x : x + width], x, y

    def _suppress_overlaps(
        self,
        points: list[tuple[int, int, float]],
        width: int,
        height: int,
    ) -> list[tuple[int, int, float]]:
        kept: list[tuple[int, int, float]] = []
        for point in sorted(points, key=lambda item: item[2], reverse=True):
            x, y, _score = point
            if any(abs(x - other_x) < width // 2 and abs(y - other_y) < height // 2 for other_x, other_y, _ in kept):
                continue
            kept.append(point)
        return kept

    def _ocr_hp(self, image: np.ndarray, rule: dict[str, Any]) -> tuple[str, str]:
        try:
            import cv2
            import pytesseract
        except ImportError as exc:
            return "", f"unavailable:{exc.name}"

        source, _offset_x, _offset_y = self._crop(image, rule.get("region"))
        if source.size == 0:
            return "", "invalid_region"
        gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
        _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        try:
            text = pytesseract.image_to_string(binary, config="--psm 6 -c tessedit_char_whitelist=0123456789/")
        except Exception as exc:
            return "", str(exc)
        return " ".join(text.split()), ""
