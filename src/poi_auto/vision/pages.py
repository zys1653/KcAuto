from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from poi_auto.core.config import load_yaml
from poi_auto.vision.recognizer import MatchResult, Recognizer


@dataclass(frozen=True)
class TemplateCheck:
    page_key: str
    template: str
    matched: bool
    score: float
    threshold: float
    required: bool = False
    path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class PageMatch:
    key: str
    name: str
    matched: bool
    score: float
    matched_count: int
    required_count: int
    checks: list[TemplateCheck] = field(default_factory=list)
    actions: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_known(self) -> bool:
        return self.matched and self.key != "unknown"


class PageMatcher:
    def __init__(self, pages_config: dict[str, Any], recognizer: Recognizer) -> None:
        self.config = pages_config
        self.recognizer = recognizer

    @classmethod
    def from_file(cls, path: Path, recognizer: Recognizer) -> "PageMatcher":
        return cls(load_yaml(path), recognizer)

    @property
    def monitor_interval_ms(self) -> int:
        return int(self.config.get("monitor", {}).get("interval_ms", 500))

    def match(self, image: np.ndarray) -> PageMatch:
        pages = self.config.get("pages", {})
        candidates: list[PageMatch] = []
        all_checks: list[TemplateCheck] = []

        for page_key, page in pages.items():
            page_match = self._match_page(str(page_key), page or {}, image)
            all_checks.extend(page_match.checks)
            if page_match.matched:
                candidates.append(page_match)

        if not candidates:
            return self._unknown(all_checks)

        best = max(candidates, key=lambda item: item.score)
        unknown_threshold = float(self.config.get("monitor", {}).get("unknown_threshold", 0.0))
        if best.score < unknown_threshold:
            return self._unknown(all_checks)
        return best

    def _unknown(self, checks: list[TemplateCheck]) -> PageMatch:
        return PageMatch(
            key="unknown",
            name="未知页面",
            matched=False,
            score=0.0,
            matched_count=0,
            required_count=1,
            checks=checks,
        )

    def _match_page(self, page_key: str, page: dict[str, Any], image: np.ndarray) -> PageMatch:
        templates = page.get("templates", []) or []
        min_matches = int(page.get("min_matches", 1))
        checks: list[TemplateCheck] = []

        for template_rule in templates:
            if not isinstance(template_rule, dict):
                continue
            template_path = str(template_rule.get("path", ""))
            threshold = float(template_rule.get("threshold", self.recognizer.default_threshold))
            required = bool(template_rule.get("required", False))
            result = self.recognizer.match_template(
                image=image,
                template=Path("pages") / template_path,
                threshold=threshold,
                region=template_rule.get("region"),
            )
            checks.append(self._check_from_result(page_key, template_path, threshold, required, result))

        matched_checks = [item for item in checks if item.matched]
        required_checks = [item for item in checks if item.required]
        required_matched = all(item.matched for item in required_checks)
        matched = bool(checks) and len(matched_checks) >= min_matches and required_matched
        score = sum(item.score for item in matched_checks) / len(matched_checks) if matched_checks else 0.0
        return PageMatch(
            key=page_key,
            name=str(page.get("name", page_key)),
            matched=matched,
            score=score,
            matched_count=len(matched_checks),
            required_count=min_matches,
            checks=checks,
            actions=page.get("actions", {}) or {},
        )

    def _check_from_result(
        self,
        page_key: str,
        template: str,
        threshold: float,
        required: bool,
        result: MatchResult,
    ) -> TemplateCheck:
        return TemplateCheck(
            page_key=page_key,
            template=template,
            matched=result.matched,
            score=result.score,
            threshold=threshold,
            required=required,
            path=result.template_path,
            error=result.error,
        )
