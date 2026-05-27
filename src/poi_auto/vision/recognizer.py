from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    score: float
    location: tuple[int, int] | None = None
    template_path: Path | None = None
    error: str = ""


class Recognizer:
    def __init__(self, templates_root: Path, default_threshold: float = 0.86) -> None:
        self.templates_root = templates_root
        self.default_threshold = default_threshold

    def detect(self, image: np.ndarray, rule: dict[str, Any], flags: dict[str, bool] | None = None) -> MatchResult:
        rule_type = rule.get("type", "always")
        if rule_type == "always":
            return MatchResult(True, 1.0)
        if rule_type == "manual_flag":
            name = str(rule.get("name", ""))
            return MatchResult(bool((flags or {}).get(name)), 1.0 if (flags or {}).get(name) else 0.0)
        if rule_type == "template":
            return self.match_template(
                image=image,
                template=Path(str(rule.get("template", ""))),
                threshold=float(rule.get("threshold", self.default_threshold)),
                region=rule.get("region"),
            )
        return MatchResult(False, 0.0)

    def match_template(
        self,
        image: np.ndarray,
        template: Path,
        threshold: float | None = None,
        region: dict[str, Any] | None = None,
    ) -> MatchResult:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("需要安装 opencv-python 才能执行模板匹配。") from exc

        template_path = self.templates_root / template
        if not template_path.exists():
            return MatchResult(False, 0.0, template_path=template_path, error="missing_template")

        source = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        offset_x = 0
        offset_y = 0
        if region:
            offset_x = int(region.get("x", 0))
            offset_y = int(region.get("y", 0))
            width = int(region.get("width", image.shape[1] - offset_x))
            height = int(region.get("height", image.shape[0] - offset_y))
            if offset_x < 0 or offset_y < 0 or width <= 0 or height <= 0:
                return MatchResult(False, 0.0, template_path=template_path, error="invalid_region")
            source = source[offset_y : offset_y + height, offset_x : offset_x + width]

        tpl = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if tpl is None:
            return MatchResult(False, 0.0, template_path=template_path, error="unreadable_template")
        if source.shape[0] < tpl.shape[0] or source.shape[1] < tpl.shape[1]:
            return MatchResult(False, 0.0, template_path=template_path, error="template_larger_than_source")

        result = cv2.matchTemplate(source, tpl, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        limit = self.default_threshold if threshold is None else threshold
        location = (max_loc[0] + offset_x, max_loc[1] + offset_y)
        return MatchResult(max_val >= limit, float(max_val), location, template_path)
