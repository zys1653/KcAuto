from __future__ import annotations

import builtins
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from poi_auto.vision.damage import DamageDetector, DamageReport, HpLineResult


def fake_cv2_module() -> SimpleNamespace:
    def cvt_color(image: np.ndarray, _code: int) -> np.ndarray:
        if image.ndim == 3:
            return image[:, :, 0]
        return image

    def threshold(image: np.ndarray, _thresh: int, _max_value: int, _kind: int) -> tuple[int, np.ndarray]:
        return 0, image.copy()

    def resize(image: np.ndarray, _size: object, fx: int, fy: int, interpolation: int) -> np.ndarray:
        return np.repeat(np.repeat(image, fy, axis=0), fx, axis=1)

    def imencode(_extension: str, _image: np.ndarray) -> tuple[bool, np.ndarray]:
        png_bytes = b"\x89PNG\r\n\x1a\nfake"
        return True, np.frombuffer(png_bytes, dtype=np.uint8)

    def find_contours(_image: np.ndarray, _mode: int, _method: int) -> tuple[list[np.ndarray], None]:
        return [], None

    return SimpleNamespace(
        COLOR_RGB2GRAY=1,
        COLOR_RGB2BGR=2,
        THRESH_BINARY=8,
        THRESH_OTSU=16,
        INTER_CUBIC=4,
        RETR_EXTERNAL=5,
        CHAIN_APPROX_SIMPLE=6,
        BORDER_CONSTANT=7,
        cvtColor=cvt_color,
        threshold=threshold,
        resize=resize,
        imencode=imencode,
        findContours=find_contours,
        boundingRect=lambda _contour: (0, 0, 0, 0),
        copyMakeBorder=lambda image, top, bottom, left, right, _border, value=0: np.pad(
            image, ((top, bottom), (left, right)), constant_values=value
        ),
    )


def debug_test_dir(name: str) -> Path:
    return Path("debug_screenshots") / "test_ocr_debug" / f"{name}_{time.time_ns()}"


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
        with patch.dict(sys.modules, {"cv2": fake_cv2_module()}), patch("builtins.__import__", fake_import):
            report = detector.detect(
                np.zeros((20, 20, 3), dtype=np.uint8),
                {"hp_ocr": {"region": {"x": 0, "y": 0, "width": 10, "height": 10}}},
                hp_ocr_enabled=True,
            )

        self.assertEqual(report.hp_values, [])
        self.assertTrue(report.ocr_error)

    def test_detect_without_hp_ocr_has_no_debug_dir(self) -> None:
        detector = DamageDetector(Path("assets/templates"))

        report = detector.detect(np.zeros((20, 20, 3), dtype=np.uint8), {}, hp_ocr_enabled=False)

        self.assertEqual(report.hp_values, [])
        self.assertIsNone(report.hp_debug_dir)

    def test_ocr_debug_output_writes_processing_images_and_text(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image, config="": "33/33\n18/18\n")

        output_dir = debug_test_dir("enabled")
        with patch.dict(sys.modules, {"cv2": fake_cv2_module(), "pytesseract": fake_tesseract}):
            report = detector.detect(
                image,
                {
                    "hp_ocr": {
                        "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                        "scale": 2,
                        "debug_output_enabled": True,
                        "debug_output_dir": str(output_dir),
                    }
                },
                hp_ocr_enabled=True,
            )

        self.assertEqual(report.hp_values, [(33, 33), (18, 18)])
        self.assertIsNotNone(report.hp_debug_dir)
        debug_dir = report.hp_debug_dir or Path()
        self.assertTrue((debug_dir / "01_source_rgb.png").exists())
        self.assertTrue((debug_dir / "02_gray.png").exists())
        self.assertTrue((debug_dir / "03_binary_otsu.png").exists())
        self.assertTrue((debug_dir / "04_scaled.png").exists())
        self.assertEqual((debug_dir / "01_source_rgb.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        text = (debug_dir / "ocr.txt").read_text(encoding="utf-8")
        self.assertIn("33/33", text)
        self.assertIn("hp_values: [33/33, 18/18]", text)

    def test_ocr_debug_output_writes_row_images_and_status(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.full((20, 80, 3), 255, dtype=np.uint8)
        row_image = np.full((10, 30), 255, dtype=np.uint8)
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image, config="": "7\n")
        output_dir = debug_test_dir("rows")

        with patch.dict(sys.modules, {"cv2": fake_cv2_module(), "pytesseract": fake_tesseract}):
            with patch.object(
                detector,
                "_ocr_hp_lines",
                return_value=[
                    HpLineResult(
                        row_index=0,
                        value=(77, 77),
                        raw_line_text="7\n",
                        fallback_text="77/77",
                        status="fallback_ok",
                        image=row_image,
                    )
                ],
            ):
                report = detector.detect(
                    image,
                    {
                        "hp_ocr": {
                            "region": {"x": 0, "y": 0, "width": 80, "height": 20},
                            "debug_output_enabled": True,
                            "debug_output_dir": str(output_dir),
                        }
                    },
                    hp_ocr_enabled=True,
                )

        debug_dir = report.hp_debug_dir or Path()
        self.assertTrue((debug_dir / "rows" / "row_01.png").exists())
        text = (debug_dir / "ocr.txt").read_text(encoding="utf-8")
        self.assertIn("row_01: status=fallback_ok", text)
        self.assertIn("fallback_text='77/77'", text)

    def test_ocr_debug_output_disabled_does_not_create_directory(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image, config="": "33/33\n")

        output_dir = debug_test_dir("disabled")
        with patch.dict(sys.modules, {"cv2": fake_cv2_module(), "pytesseract": fake_tesseract}):
            report = detector.detect(
                image,
                {
                    "hp_ocr": {
                        "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                        "debug_output_enabled": False,
                        "debug_output_dir": str(output_dir),
                    }
                },
                hp_ocr_enabled=True,
            )

        self.assertIsNone(report.hp_debug_dir)
        self.assertFalse(output_dir.exists())

    def test_invalid_ocr_region_does_not_write_debug_output(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image, config="": "33/33\n")

        output_dir = debug_test_dir("invalid")
        with patch.dict(sys.modules, {"cv2": fake_cv2_module(), "pytesseract": fake_tesseract}):
            report = detector.detect(
                image,
                {
                    "hp_ocr": {
                        "region": {"x": -1, "y": 0, "width": 10, "height": 10},
                        "debug_output_enabled": True,
                        "debug_output_dir": str(output_dir),
                    }
                },
                hp_ocr_enabled=True,
            )

        self.assertEqual(report.ocr_error, "invalid_region")
        self.assertIsNone(report.hp_debug_dir)
        self.assertFalse(output_dir.exists())

    def test_single_seven_without_part_fallback_stays_unreadable(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.full((20, 80, 3), 255, dtype=np.uint8)
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image, config="": "7\n")

        with patch.dict(sys.modules, {"cv2": fake_cv2_module(), "pytesseract": fake_tesseract}):
            with patch.object(detector, "_hp_line_boxes", return_value=[(0, 20, 0, 80)]):
                with patch.object(detector, "_ocr_hp_line_by_parts", return_value=""):
                    report = detector.detect(
                        image,
                        {"hp_ocr": {"region": {"x": 0, "y": 0, "width": 80, "height": 20}}},
                        hp_ocr_enabled=True,
                    )

        self.assertEqual(report.hp_values, [])
        self.assertEqual(report.hp_unreadable_rows, [1])

    def test_part_fallback_can_recover_repeated_sevens(self) -> None:
        detector = DamageDetector(Path("assets/templates"))
        image = np.full((20, 80, 3), 255, dtype=np.uint8)
        fake_tesseract = SimpleNamespace(image_to_string=lambda _image, config="": "7\n")

        with patch.dict(sys.modules, {"cv2": fake_cv2_module(), "pytesseract": fake_tesseract}):
            with patch.object(detector, "_hp_line_boxes", return_value=[(0, 20, 0, 80)]):
                with patch.object(detector, "_ocr_hp_line_by_parts", return_value="77/77"):
                    report = detector.detect(
                        image,
                        {"hp_ocr": {"region": {"x": 0, "y": 0, "width": 80, "height": 20}}},
                        hp_ocr_enabled=True,
                    )

        self.assertEqual(report.hp_values, [(77, 77)])
        self.assertEqual(report.hp_unreadable_rows, [])

    def test_existing_debug_sample_recovers_77_77_when_available(self) -> None:
        sample = Path("debug_screenshots/ocr/20260604-015835-583/04_scaled.png")
        if not sample.exists():
            self.skipTest("local OCR debug sample is not available")
        try:
            import cv2
            import pytesseract
        except Exception as exc:
            self.skipTest(f"OCR dependencies are not available: {exc}")

        image = cv2.imdecode(np.fromfile(sample, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        detector = DamageDetector(Path("assets/templates"))
        results = detector._ocr_hp_lines(cv2, pytesseract, image, max_rows=6)
        values = [result.value for result in results if result.value is not None]

        self.assertEqual(values, [(50, 50), (30, 30), (77, 77), (68, 75), (30, 30)])


if __name__ == "__main__":
    unittest.main()
