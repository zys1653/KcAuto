from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from threading import Event
import tempfile
import unittest

import numpy as np
import yaml

from poi_auto.core.context import RuntimeContext
from poi_auto.tasks.sortie.task import SortieTask
from poi_auto.vision.damage import DamageReport, DamageStateResult
from poi_auto.vision.pages import PageMatch, PageMatcher
from poi_auto.vision.recognizer import Recognizer


def page(key: str, actions: dict[str, dict[str, int]] | None = None) -> PageMatch:
    return PageMatch(
        key=key,
        name=key,
        matched=key != "unknown",
        score=1.0 if key != "unknown" else 0.0,
        matched_count=1 if key != "unknown" else 0,
        required_count=1,
        actions=actions or {},
    )


class FakePageMatcher:
    def __init__(self, pages: list[PageMatch]) -> None:
        self.pages = pages
        self.index = 0

    def match(self, _image: np.ndarray) -> PageMatch:
        item = self.pages[min(self.index, len(self.pages) - 1)]
        self.index += 1
        return item


class FakeDevice:
    def __init__(self) -> None:
        self.image = np.zeros((720, 1200, 3), dtype=np.uint8)
        self.last_screenshot = SimpleNamespace(image=self.image)
        self.clicks: list[tuple[int, int]] = []

    def capture_game(self) -> object:
        return self.last_screenshot

    def click(self, x: int, y: int) -> tuple[int, int]:
        self.clicks.append((x, y))
        return x, y


class SortieTaskTest(unittest.TestCase):
    def make_task(self, rules: dict) -> SortieTask:
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False)
        with tmp:
            yaml.safe_dump(rules, tmp, allow_unicode=True)
        return SortieTask(Path(tmp.name))

    def make_context(
        self,
        pages: list[PageMatch],
        device: FakeDevice | None = None,
        logs: list[str] | None = None,
    ) -> RuntimeContext:
        fake_device = device or FakeDevice()
        return RuntimeContext(
            config={
                "sortie": {
                    "map": "1-1",
                    "formation": "line_ahead",
                    "max_battles": 1,
                    "stop_on_heavy_damage": True,
                    "hp_ocr_enabled": False,
                }
            },
            paths=SimpleNamespace(templates=Path("assets/templates")),
            device=fake_device,
            recognizer=SimpleNamespace(),
            stop_event=Event(),
            logger=(logs.append if logs is not None else lambda _message: None),
            page_matcher=FakePageMatcher(pages),
        )

    def test_entry_flow_clicks_page_actions_in_order(self) -> None:
        rules = {
            "entry_flow": [
                {"page": "home", "action": "sortie"},
                {"page": "sortie_menu", "action": "sortie_start"},
                {"page": "map_select_1", "action_from_map": True},
                {"page": "sortie_confirm", "action": "decide"},
            ],
            "map_actions": {"1-1": {"page": "map_select_1", "action": "map_1_1"}},
            "loop_pages": {},
        }
        task = self.make_task(rules)
        device = FakeDevice()
        context = self.make_context(
            [
                page("home", {"sortie": {"x": 1, "y": 2}}),
                page("sortie_menu", {"sortie_start": {"x": 3, "y": 4}}),
                page("map_select_1", {"map_1_1": {"x": 5, "y": 6}}),
                page("sortie_confirm", {"decide": {"x": 7, "y": 8}}),
            ],
            device,
        )

        for _ in range(4):
            self.assertTrue(task.step(context))

        self.assertTrue(task.sortie_started)
        self.assertEqual(device.clicks, [(1, 2), (3, 4), (5, 6), (7, 8)])

    def test_loop_pages_dispatch_core_actions(self) -> None:
        rules = {
            "entry_flow": [],
            "loop_pages": {
                "compass": {"action": "spin", "wait_ms": 1},
                "formation": {"action_type": "choose_formation", "wait_ms": 1},
                "battle": {"action_type": "collect_damage", "click": False, "poll_ms": 1},
                "night_battle": {"action": "enter", "wait_ms": 1},
                "battle_result": {"action_type": "click_anywhere", "action": "continue", "wait_ms": 1},
                "exp_gain": {"action_type": "click_anywhere", "action": "continue", "wait_ms": 1},
                "new_ship": {"action_type": "click_anywhere", "action": "continue", "wait_ms": 1},
                "resource_node": {"action_type": "click_anywhere", "action": "continue", "wait_ms": 1},
            },
        }
        task = self.make_task(rules)
        task.sortie_started = True
        task._detect_damage = lambda _context, _image: DamageReport()
        device = FakeDevice()
        context = self.make_context(
            [
                page("compass", {"spin": {"x": 10, "y": 10}}),
                page("formation", {"line_ahead": {"x": 20, "y": 20}}),
                page("night_battle", {"enter": {"x": 30, "y": 30}}),
                page("battle_result", {"continue": {"x": 40, "y": 40}}),
                page("exp_gain", {"continue": {"x": 50, "y": 50}}),
                page("new_ship", {"continue": {"x": 60, "y": 60}}),
                page("resource_node", {"continue": {"x": 70, "y": 70}}),
                page("battle"),
            ],
            device,
        )

        for _ in range(8):
            self.assertTrue(task.step(context))

        self.assertEqual(task.battle_count, 1)
        self.assertEqual(device.clicks, [(10, 10), (20, 20), (30, 30), (40, 40), (50, 50), (60, 60), (70, 70)])

    def test_battle_page_uses_poll_delay_and_logs_hp(self) -> None:
        rules = {
            "entry_flow": [],
            "loop_pages": {"battle": {"action_type": "collect_damage", "click": False, "poll_ms": 1, "wait_ms": 9999}},
        }
        task = self.make_task(rules)
        task.sortie_started = True
        task._detect_damage = lambda _context, _image: DamageReport(hp_values=[(33, 33), (18, 18), (17, 35)])
        logs: list[str] = []

        self.assertTrue(task.step(self.make_context([page("battle")], logs=logs)))
        self.assertEqual(task.last_wait_ms, 1)
        self.assertEqual(task.last_page_key, "battle")
        self.assertTrue(any("战斗中血量：1:33/33, 2:18/18, 3:17/35" in item for item in logs))

    def test_battle_transition_waits_then_processes_refreshed_page(self) -> None:
        rules = {
            "entry_flow": [],
            "loop_pages": {
                "battle": {"action_type": "collect_damage", "click": False, "poll_ms": 1, "transition_wait_ms": 1},
                "battle_result": {"action_type": "click_anywhere", "action": "continue", "wait_ms": 1},
            },
        }
        task = self.make_task(rules)
        task.sortie_started = True
        task._detect_damage = lambda _context, _image: DamageReport()
        device = FakeDevice()
        context = self.make_context(
            [
                page("battle"),
                page("battle_result", {"continue": {"x": 40, "y": 40}}),
                page("battle_result", {"continue": {"x": 40, "y": 40}}),
            ],
            device,
        )

        self.assertTrue(task.step(context))
        self.assertTrue(task.step(context))
        self.assertEqual(device.clicks, [(40, 40)])
        self.assertEqual(task.last_page_key, "battle_result")

    def test_battle_transition_to_unknown_waits_before_unknown_retry(self) -> None:
        rules = {
            "recovery": {"default_wait_ms": 1},
            "entry_flow": [],
            "loop_pages": {"battle": {"action_type": "collect_damage", "click": False, "poll_ms": 1, "transition_wait_ms": 1}},
        }
        task = self.make_task(rules)
        task.sortie_started = True
        task._detect_damage = lambda _context, _image: DamageReport()
        context = self.make_context([page("battle"), page("unknown"), page("unknown")])

        self.assertTrue(task.step(context))
        self.assertTrue(task.step(context))
        self.assertEqual(task.unknown_retry_count, 1)
        self.assertEqual(task.last_page_key, "unknown")

    def test_advance_or_retreat_uses_damage_before_proceeding(self) -> None:
        rules = {
            "entry_flow": [],
            "loop_pages": {"advance_or_retreat": {"action_type": "advance_or_retreat", "wait_ms": 1}},
            "damage_detection": {"retreat_on": ["heavy"]},
        }
        task = self.make_task(rules)
        task.sortie_started = True
        task._detect_damage = lambda _context, _image: DamageReport(
            states={"heavy": DamageStateResult("heavy", 1, 1.0)}
        )
        device = FakeDevice()
        context = self.make_context(
            [page("advance_or_retreat", {"proceed": {"x": 1, "y": 1}, "retreat": {"x": 2, "y": 2}})],
            device,
        )

        self.assertTrue(task.step(context))
        self.assertEqual(device.clicks, [(2, 2)])

    def test_return_home_stops_task(self) -> None:
        finish = self.make_task({"entry_flow": [], "loop_pages": {"return_home": {"action_type": "finish"}}})
        finish.sortie_started = True
        self.assertFalse(finish.step(self.make_context([page("return_home")])))

    def test_unknown_page_retries_three_times_before_stopping(self) -> None:
        task = self.make_task({"recovery": {"default_wait_ms": 1}, "entry_flow": [], "loop_pages": {}})
        task.sortie_started = True
        logs: list[str] = []
        context = self.make_context([page("unknown"), page("unknown"), page("unknown")], logs=logs)

        self.assertTrue(task.step(context))
        self.assertTrue(task.step(context))
        self.assertFalse(task.step(context))
        self.assertEqual(task.unknown_retry_count, 3)
        self.assertTrue(any("retry=3/3" in item for item in logs))

    def test_unknown_retry_count_clears_after_known_page(self) -> None:
        task = self.make_task(
            {
                "recovery": {"default_wait_ms": 1},
                "entry_flow": [],
                "loop_pages": {"battle": {"action_type": "wait", "wait_ms": 1}},
            }
        )
        task.sortie_started = True
        context = self.make_context([page("unknown"), page("battle")])

        self.assertTrue(task.step(context))
        self.assertTrue(task.step(context))
        self.assertEqual(task.unknown_retry_count, 0)

    def test_entry_flow_replays_current_known_page_when_expected_differs(self) -> None:
        rules = {
            "entry_flow": [
                {"page": "home", "action": "sortie", "wait_ms": 1},
                {"page": "sortie_menu", "action": "sortie_start", "wait_ms": 1},
                {"page": "map_select_1", "action_from_map": True, "wait_ms": 1},
            ],
            "map_actions": {"1-1": {"page": "map_select_1", "action": "map_1_1"}},
            "loop_pages": {},
        }
        task = self.make_task(rules)
        task.entry_index = 2
        device = FakeDevice()
        context = self.make_context([page("home", {"sortie": {"x": 1, "y": 2}})], device)

        self.assertTrue(task.step(context))
        self.assertEqual(device.clicks, [(1, 2)])
        self.assertEqual(task.entry_index, 1)

    def test_entry_flow_replays_current_intermediate_page_when_expected_differs(self) -> None:
        rules = {
            "entry_flow": [
                {"page": "home", "action": "sortie", "wait_ms": 1},
                {"page": "sortie_menu", "action": "sortie_start", "wait_ms": 1},
                {"page": "map_select_1", "action_from_map": True, "wait_ms": 1},
            ],
            "map_actions": {"1-1": {"page": "map_select_1", "action": "map_1_1"}},
            "loop_pages": {},
        }
        task = self.make_task(rules)
        task.entry_index = 2
        device = FakeDevice()
        context = self.make_context([page("sortie_menu", {"sortie_start": {"x": 3, "y": 4}})], device)

        self.assertTrue(task.step(context))
        self.assertEqual(device.clicks, [(3, 4)])
        self.assertEqual(task.entry_index, 2)

    def test_entry_flow_waits_when_current_page_is_not_configured(self) -> None:
        task = self.make_task(
            {
                "recovery": {"default_wait_ms": 1},
                "entry_flow": [{"page": "map_select_1", "action_from_map": True, "wait_ms": 1}],
                "map_actions": {"1-1": {"page": "map_select_1", "action": "map_1_1"}},
                "loop_pages": {},
            }
        )
        device = FakeDevice()
        context = self.make_context([page("sortie_menu", {"sortie_start": {"x": 3, "y": 4}})], device)

        self.assertTrue(task.step(context))
        self.assertEqual(device.clicks, [])
        self.assertEqual(task.entry_index, 0)

    def test_missing_template_is_reported_without_crashing(self) -> None:
        matcher = PageMatcher(
            {"pages": {"missing": {"templates": [{"path": "missing/nope.png", "threshold": 0.9}]}}},
            Recognizer(Path("assets/templates")),
        )
        result = matcher.match(np.zeros((20, 20, 3), dtype=np.uint8))

        self.assertFalse(result.is_known)
        self.assertTrue(any(check.error == "missing_template" for check in result.checks))


if __name__ == "__main__":
    unittest.main()
