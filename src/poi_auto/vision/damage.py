from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
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
class HpLineResult:
    row_index: int
    value: tuple[int, int] | None = None
    raw_line_text: str = ""
    fallback_text: str = ""
    status: str = "unreadable"
    image: np.ndarray | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class DamageReport:
    states: dict[str, DamageStateResult] = field(default_factory=dict)
    hp_log: str = ""
    hp_values: list[tuple[int, int]] = field(default_factory=list)
    ocr_error: str = ""
    hp_debug_dir: Path | None = None
    hp_unreadable_rows: list[int] = field(default_factory=list)

    def count(self, state: str) -> int:
        result = self.states.get(state)
        return result.count if result else 0

    def has_any(self, states: list[str]) -> bool:
        return any(self.count(state) > 0 for state in states)

    def summary(self) -> str:
        parts = [f"{state}={result.count}" for state, result in self.states.items()]
        if self.hp_values:
            parts.append(f"hp=[{self.hp_summary()}]")
        elif self.hp_log:
            parts.append(f"hp_raw={self.hp_log}")
        if self.ocr_error:
            parts.append(f"ocr={self.ocr_error}")
        if self.hp_unreadable_rows:
            rows = ",".join(str(row) for row in self.hp_unreadable_rows)
            parts.append(f"hp_unreadable_rows=[{rows}]")
        return ", ".join(parts) if parts else "no damage rules"

    def hp_summary(self) -> str:
        return ", ".join(f"{index}:{current}/{maximum}" for index, (current, maximum) in enumerate(self.hp_values, 1))


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
        hp_values: list[tuple[int, int]] = []
        ocr_error = ""
        hp_debug_dir: Path | None = None
        hp_unreadable_rows: list[int] = []
        if hp_ocr_enabled:
            hp_rule = rules.get("hp_ocr", {}) or {}
            hp_values, hp_log, ocr_error, hp_debug_dir, hp_unreadable_rows = self._ocr_hp(image, hp_rule)

        return DamageReport(
            states=states,
            hp_log=hp_log,
            hp_values=hp_values,
            ocr_error=ocr_error,
            hp_debug_dir=hp_debug_dir,
            hp_unreadable_rows=hp_unreadable_rows,
        )

    def _detect_state(self, state: str, image: np.ndarray, rule: dict[str, Any]) -> DamageStateResult:
        try:
            import cv2
        except ImportError:
            return DamageStateResult(state, 0, 0.0, error="opencv_not_installed")
        except Exception as exc:
            return DamageStateResult(state, 0, 0.0, error=f"opencv_unavailable:{exc}")

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

    def _crop_with_padding(self, image: np.ndarray, region: dict[str, Any] | None, padding: dict[str, Any] | None) -> np.ndarray:
        if not region:
            return image
        pad = padding or {}
        region_x = int(region.get("x", 0))
        region_y = int(region.get("y", 0))
        region_width = int(region.get("width", image.shape[1] - region_x))
        region_height = int(region.get("height", image.shape[0] - region_y))
        if region_x < 0 or region_y < 0 or region_width <= 0 or region_height <= 0:
            return image[0:0, 0:0]
        x = region_x - int(pad.get("left", 0))
        y = region_y - int(pad.get("top", 0))
        width = region_width + int(pad.get("left", 0)) + int(pad.get("right", 0))
        height = region_height + int(pad.get("top", 0)) + int(pad.get("bottom", 0))
        left = max(x, 0)
        top = max(y, 0)
        right = min(left + max(width, 0), image.shape[1])
        bottom = min(top + max(height, 0), image.shape[0])
        if right <= left or bottom <= top:
            return image[0:0, 0:0]
        return image[top:bottom, left:right]

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

    def _ocr_hp(self, image: np.ndarray, rule: dict[str, Any]) -> tuple[list[tuple[int, int]], str, str, Path | None, list[int]]:
        try:
            import cv2
            import pytesseract
        except ImportError as exc:
            return [], "", f"unavailable:{exc.name}", None, []
        except Exception as exc:
            return [], "", f"unavailable:{exc}", None, []

        source = self._crop_with_padding(image, rule.get("region"), rule.get("padding"))
        if source.size == 0:
            return [], "", "invalid_region", None, []
        gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
        _threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        scale = max(int(rule.get("scale", 1)), 1)
        scaled = binary
        if scale > 1:
            scaled = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        try:
            text = pytesseract.image_to_string(scaled, config="--psm 6 -c tessedit_char_whitelist=0123456789/")
        except Exception as exc:
            text = ""
            ocr_error = str(exc)
        else:
            ocr_error = ""
        line_results = self._ocr_hp_lines(cv2, pytesseract, scaled, max_rows=int(rule.get("max_rows", 6)))
        block_values = self._valid_hp_values(self.parse_hp_text(text, max_rows=int(rule.get("max_rows", 6))))
        if line_results:
            hp_values = [result.value for result in line_results if result.value is not None]
        else:
            hp_values = block_values
        unreadable_rows = [result.row_index + 1 for result in line_results if result.status == "unreadable"]
        hp_log = self._hp_line_log(line_results) if line_results else " ".join(text.split())

        debug_dir = None
        if bool(rule.get("debug_output_enabled", False)):
            debug_dir, debug_error = self._write_ocr_debug_outputs(source, gray, binary, scaled, text, hp_log, hp_values, line_results, rule)
            ocr_error = self._join_errors(ocr_error, debug_error)
        return hp_values, hp_log, ocr_error, debug_dir, unreadable_rows

    def _write_ocr_debug_outputs(
        self,
        source: np.ndarray,
        gray: np.ndarray,
        binary: np.ndarray,
        scaled: np.ndarray,
        text: str,
        hp_log: str,
        hp_values: list[tuple[int, int]],
        line_results: list[HpLineResult],
        rule: dict[str, Any],
    ) -> tuple[Path | None, str]:
        try:
            import cv2
        except ImportError as exc:
            return None, f"debug_output_failed:unavailable:{exc.name}"
        except Exception as exc:
            return None, f"debug_output_failed:unavailable:{exc}"

        try:
            base_dir = Path(str(rule.get("debug_output_dir", "debug_screenshots/ocr")))
            run_dir = self._next_debug_dir(base_dir)
            run_dir.mkdir(parents=True, exist_ok=False)
            self._write_png(cv2, run_dir / "01_source_rgb.png", cv2.cvtColor(source, cv2.COLOR_RGB2BGR))
            self._write_png(cv2, run_dir / "02_gray.png", gray)
            self._write_png(cv2, run_dir / "03_binary_otsu.png", binary)
            self._write_png(cv2, run_dir / "04_scaled.png", scaled)
            rows_dir = run_dir / "rows"
            rows_dir.mkdir(exist_ok=True)
            for result in line_results:
                if result.image is not None:
                    self._write_png(cv2, rows_dir / f"row_{result.row_index + 1:02d}.png", result.image)
            (run_dir / "ocr.txt").write_text(
                "\n".join(
                    [
                        "raw_text:",
                        text,
                        "",
                        f"hp_log: {hp_log}",
                        f"hp_values: {self._format_hp_values(hp_values)}",
                        f"region: {rule.get('region')}",
                        f"padding: {rule.get('padding')}",
                        f"scale: {max(int(rule.get('scale', 1)), 1)}",
                        f"max_rows: {int(rule.get('max_rows', 6))}",
                        "",
                        "line_results:",
                        *self._format_line_results(line_results),
                    ]
                ),
                encoding="utf-8",
            )
            return run_dir, ""
        except Exception as exc:
            return None, f"debug_output_failed:{exc}"

    def _write_png(self, cv2: Any, path: Path, image: np.ndarray) -> None:
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError(f"failed_to_encode_png:{path.name}")
        path.write_bytes(encoded.tobytes())

    def _ocr_hp_lines(self, cv2: Any, pytesseract: Any, image: np.ndarray, max_rows: int = 6) -> list[HpLineResult]:
        results: list[HpLineResult] = []
        for row_index, (top, bottom, left, right) in enumerate(self._hp_line_boxes(cv2, image)[:max_rows]):
            row = image[top:bottom, left:right]
            if row.size == 0:
                continue
            row = cv2.copyMakeBorder(row, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=0)
            raw_text = self._tesseract_text(pytesseract, row, "--psm 7 -c tessedit_char_whitelist=0123456789/")
            values = self._valid_hp_values(self.parse_hp_text(raw_text, max_rows=1))
            if values:
                results.append(HpLineResult(row_index=row_index, value=values[0], raw_line_text=raw_text, status="ok", image=row))
                continue

            fallback_text = self._ocr_hp_line_by_parts(cv2, pytesseract, row)
            fallback_values = self._valid_hp_values(self.parse_hp_text(fallback_text, max_rows=1))
            if fallback_values:
                results.append(
                    HpLineResult(
                        row_index=row_index,
                        value=fallback_values[0],
                        raw_line_text=raw_text,
                        fallback_text=fallback_text,
                        status="fallback_ok",
                        image=row,
                    )
                )
                continue

            results.append(
                HpLineResult(
                    row_index=row_index,
                    raw_line_text=raw_text,
                    fallback_text=fallback_text,
                    status="unreadable",
                    image=row,
                )
            )
        return results

    def _ocr_hp_line_by_parts(self, cv2: Any, pytesseract: Any, row: np.ndarray) -> str:
        bounds = self._foreground_bounds(cv2, row)
        if bounds is None:
            return ""
        left, _top, right, _bottom = bounds
        width = right - left
        if width < 60:
            return ""

        split_left = left + int(width * 0.42)
        split_right = left + int(width * 0.58)
        left_part = row[:, left:split_left]
        right_part = row[:, split_right:right]
        left_digits = self._ocr_digit_part(pytesseract, left_part)
        right_digits = self._ocr_digit_part(pytesseract, right_part)
        if not left_digits or not right_digits:
            return ""
        return f"{left_digits}/{right_digits}"

    def _ocr_digit_part(self, pytesseract: Any, image: np.ndarray) -> str:
        candidates: list[str] = []
        for psm in (8, 10, 13, 7):
            text = self._tesseract_text(pytesseract, image, f"--psm {psm} -c tessedit_char_whitelist=0123456789")
            digits = re.sub(r"\D", "", text)
            if 1 <= len(digits) <= 3:
                candidates.append(digits)
        if not candidates:
            return ""
        return max(candidates, key=lambda item: (candidates.count(item), len(item)))

    def _valid_hp_values(self, values: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return [(current, maximum) for current, maximum in values if 0 <= current <= maximum <= 999 and maximum > 0]

    def _tesseract_text(self, pytesseract: Any, image: np.ndarray, config: str) -> str:
        try:
            return str(pytesseract.image_to_string(image, config=config))
        except Exception:
            return ""

    def _hp_line_boxes(self, cv2: Any, image: np.ndarray) -> list[tuple[int, int, int, int]]:
        contours, _hierarchy = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_x = max(8, int(image.shape[1] * 0.08))
        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if x < min_x or width * height < 20 or width < 3 or height < 5:
                continue
            boxes.append((x, y, width, height))
        if not boxes:
            return []

        groups: list[list[tuple[int, int, int, int]]] = []
        for box in sorted(boxes, key=lambda item: item[1] + item[3] / 2):
            x, y, width, height = box
            if not groups:
                groups.append([box])
                continue
            current_top = min(item[1] for item in groups[-1])
            current_bottom = max(item[1] + item[3] for item in groups[-1])
            if y <= current_bottom + 8 and y + height >= current_top - 8:
                groups[-1].append(box)
            else:
                groups.append([box])

        line_boxes: list[tuple[int, int, int, int]] = []
        for group in groups:
            left = max(min(item[0] for item in group) - 8, 0)
            top = max(min(item[1] for item in group) - 8, 0)
            right = min(max(item[0] + item[2] for item in group) + 8, image.shape[1])
            bottom = min(max(item[1] + item[3] for item in group) + 8, image.shape[0])
            if right - left >= 30 and bottom - top >= 12:
                line_boxes.append((top, bottom, left, right))
        return sorted(line_boxes, key=lambda item: item[0])

    def _foreground_bounds(self, cv2: Any, image: np.ndarray) -> tuple[int, int, int, int] | None:
        contours, _hierarchy = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        bounds: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width * height >= 20 and width >= 3 and height >= 5:
                bounds.append((x, y, width, height))
        if not bounds:
            return None
        left = min(x for x, _y, _width, _height in bounds)
        top = min(y for _x, y, _width, _height in bounds)
        right = max(x + width for x, _y, width, _height in bounds)
        bottom = max(y + height for _x, y, _width, height in bounds)
        return left, top, right, bottom

    def _hp_line_log(self, line_results: list[HpLineResult]) -> str:
        parts: list[str] = []
        for result in line_results:
            if result.value is None:
                parts.append(f"{result.row_index + 1}:unreadable")
            else:
                current, maximum = result.value
                prefix = "fallback:" if result.status == "fallback_ok" else ""
                parts.append(f"{result.row_index + 1}:{prefix}{current}/{maximum}")
        return " ".join(parts)

    def _format_line_results(self, line_results: list[HpLineResult]) -> list[str]:
        lines: list[str] = []
        for result in line_results:
            value = "none" if result.value is None else f"{result.value[0]}/{result.value[1]}"
            raw = " ".join(result.raw_line_text.split())
            fallback = " ".join(result.fallback_text.split())
            lines.append(
                f"row_{result.row_index + 1:02d}: status={result.status}, value={value}, "
                f"raw_line_text={raw!r}, fallback_text={fallback!r}"
            )
        return lines

    def _next_debug_dir(self, base_dir: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        candidate = base_dir / stamp
        index = 1
        while candidate.exists():
            candidate = base_dir / f"{stamp}-{index}"
            index += 1
        return candidate

    def _join_errors(self, first: str, second: str) -> str:
        if first and second:
            return f"{first}; {second}"
        return first or second

    def _format_hp_values(self, hp_values: list[tuple[int, int]]) -> str:
        if not hp_values:
            return "[]"
        return "[" + ", ".join(f"{current}/{maximum}" for current, maximum in hp_values) + "]"

    @staticmethod
    def parse_hp_text(text: str, max_rows: int = 6) -> list[tuple[int, int]]:
        normalized = re.sub(r"\s*/\s*", "/", text)
        pairs = re.findall(r"(\d{1,3})/(\d{1,3})", normalized)
        return [(int(current), int(maximum)) for current, maximum in pairs[:max_rows]]
