from __future__ import annotations

import builtins
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from poi_auto.vision.damage import DamageDetector, DamageReport


class DamageDetectorTest(unittest.TestCase):
    def test_parse_hp_text_extracts_hp_pairs(self) -> None:
        values = DamageDetector.parse_hp_text("33/33\n18/18\n17/35")

        self.assertEqual(values, [(33, 33), (18, 18), (17, 35)])

    def test_parse_hp_text_tolerates_spaces_and_extra_text(self) -> None:
        values = DamageDetector.parse_hp_text("HP 33 / 33\n 18/18\nx\n17 /35")

        self.assertEqual(values, [(33, 33), (18, 18), (17, 35)])

    def test_parse_hp_text_limits_to_six_ships(self) -> None:
        values = DamageDetector.parse_hp_text("1/1\n2/2\n3/3\n4/4\n5/5\n6/6\n7/7")

        self.assertEqual(values, [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)])

    def test_summary_includes_structured_hp(self) -> None:
        report = DamageReport(hp_values=[(33, 33), (18, 18), (17, 35)])

        self.assertEqual(report.hp_summary(), "1:33/33, 2:18/18, 3:17/35")
        self.assertIn("hp=[1:33/33, 2:18/18, 3:17/35]", report.summary())

    def test_ocr_crop_uses_padding_without_exceeding_image(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        cropped = detector._crop_with_padding(
            image,
            {"x": 5, "y": 5, "width": 5, "height": 5},
            {"top": 3, "right": 2, "bottom": 1, "left": 4},
        )

        self.assertEqual(cropped.shape[:2], (9, 11))

    def test_ocr_unavailable_returns_error_without_raising(self) -> None:
        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "pytesseract":
                raise ImportError("blocked")
            return real_import(name, *args, **kwargs)

        detector = DamageDetector(Path("assets/templates"))
        with patch("builtins.__import__", fake_import):
            report = detector.detect(
                np.zeros((20, 20, 3), dtype=np.uint8),
                {"hp_ocr": {"region": {"x": 0, "y": 0, "width": 10, "height": 10}}},
                hp_ocr_enabled=True,
            )

        self.assertEqual(report.hp_values, [])
        self.assertTrue(report.ocr_error)


if __name__ == "__main__":
    unittest.main()
