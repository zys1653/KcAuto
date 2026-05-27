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
            )
        return MatchResult(False, 0.0)

    def match_template(self, image: np.ndarray, template: Path, threshold: float | None = None) -> MatchResult:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("需要安装 opencv-python 才能执行模板匹配。") from exc

        template_path = self.templates_root / template
        if not template_path.exists():
            return MatchResult(False, 0.0, template_path=template_path)

        source = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        tpl = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if tpl is None:
            return MatchResult(False, 0.0, template_path=template_path)

        result = cv2.matchTemplate(source, tpl, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        limit = self.default_threshold if threshold is None else threshold
        return MatchResult(max_val >= limit, float(max_val), max_loc, template_path)

