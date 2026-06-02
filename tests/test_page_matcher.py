from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np

from poi_auto.vision.pages import PageMatcher
from poi_auto.vision.recognizer import MatchResult


class FakeRecognizer:
    default_threshold = 0.86

    def __init__(self, matches: dict[str, bool]) -> None:
        self.matches = matches

    def match_template(
        self,
        image: np.ndarray,
        template: Path,
        threshold: float | None = None,
        region: dict | None = None,
    ) -> MatchResult:
        matched = self.matches.get(template.as_posix(), False)
        return MatchResult(matched=matched, score=1.0 if matched else 0.0)


class PageMatcherTest(unittest.TestCase):
    def test_min_matches_without_required_keeps_any_n_behavior(self) -> None:
        matcher = PageMatcher(
            {
                "pages": {
                    "sample": {
                        "min_matches": 2,
                        "templates": [
                            {"path": "a.png"},
                            {"path": "b.png"},
                            {"path": "c.png"},
                        ],
                    }
                }
            },
            FakeRecognizer({"pages/b.png": True, "pages/c.png": True}),
        )

        result = matcher.match(np.zeros((10, 10, 3), dtype=np.uint8))

        self.assertEqual(result.key, "sample")
        self.assertTrue(result.is_known)
        self.assertEqual(result.matched_count, 2)

    def test_required_template_must_match_alongside_min_matches(self) -> None:
        config = {
            "pages": {
                "sample": {
                    "min_matches": 2,
                    "templates": [
                        {"path": "must.png", "required": True},
                        {"path": "b.png"},
                        {"path": "c.png"},
                    ],
                }
            }
        }
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        missing_required = PageMatcher(
            config,
            FakeRecognizer({"pages/b.png": True, "pages/c.png": True}),
        ).match(image)
        self.assertFalse(missing_required.is_known)
        self.assertTrue(any(check.required for check in missing_required.checks))

        with_required = PageMatcher(
            config,
            FakeRecognizer({"pages/must.png": True, "pages/c.png": True}),
        ).match(image)
        self.assertEqual(with_required.key, "sample")
        self.assertTrue(with_required.is_known)


if __name__ == "__main__":
    unittest.main()
