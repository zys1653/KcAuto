from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from poi_auto.core.config import load_yaml
from poi_auto.core.context import RuntimeContext
from poi_auto.tasks.base import BaseTask
from poi_auto.vision.damage import DamageDetector, DamageReport


class SortieTask(BaseTask):
    name = "sortie"

    def __init__(self, rules_path: Path) -> None:
        self.rules_path = rules_path
        self.rules = load_yaml(rules_path)
        self.battle_count = 0
        self.entry_index = 0
        self.sortie_started = False
        self.last_damage_report = DamageReport()
        recovery = self.rules.get("recovery", {}) or {}
        self.unknown_max_retries = int(recovery.get("unknown_max_retries", 3))
        self.last_wait_ms = int(recovery.get("default_wait_ms", 500))
        self.unknown_retry_count = 0

    def step(self, context: RuntimeContext) -> bool:
        if context.page_matcher is None:
            context.log("页面识别器未初始化，出击任务停止。")
            return False

        screenshot = context.device.capture_game()
        page = context.page_matcher.match(screenshot.image)
        context.latest_page = page

        sortie_config = context.config.get("sortie", {})
        target_map = str(sortie_config.get("map", "1-1"))
        max_battles = int(sortie_config.get("max_battles", 6))
        context.log(
            f"出击状态：page={page.key}, score={page.score:.3f}, "
            f"map={target_map}, battle={self.battle_count}/{max_battles}"
        )

        if not page.is_known:
            return self._handle_unknown_page(context)

        if self.unknown_retry_count:
            context.log(f"页面已恢复识别：page={page.key}，清除未知页面重试计数。")
            self.unknown_retry_count = 0

        if not self.sortie_started:
            return self._handle_entry_page(context, page, target_map)

        page_rule = self._loop_rule(page.key)
        if page_rule is None:
            context.log(f"页面 {page.key} 暂未纳入出击流程，任务停止。")
            return False
        return self._run_page_rule(context, page, page_rule, max_battles)

    def _handle_unknown_page(self, context: RuntimeContext) -> bool:
        self.unknown_retry_count += 1
        if self.unknown_retry_count >= self.unknown_max_retries:
            context.log(
                "当前页面未知，出击任务停止："
                f"retry={self.unknown_retry_count}/{self.unknown_max_retries}, "
                f"last_wait_ms={self.last_wait_ms}"
            )
            return False
        context.log(
            "当前页面未知，等待后重试："
            f"retry={self.unknown_retry_count}/{self.unknown_max_retries}, "
            f"wait_ms={self.last_wait_ms}"
        )
        self._wait(context, self.last_wait_ms)
        return True

    def _handle_entry_page(self, context: RuntimeContext, page: Any, target_map: str) -> bool:
        entry_flow = self.rules.get("entry_flow", []) or []
        if self.entry_index >= len(entry_flow):
            self.sortie_started = True
            return True

        expected = entry_flow[self.entry_index] or {}
        expected_page = str(expected.get("page", ""))
        if page.key != expected_page:
            return self._recover_entry_flow(context, page, target_map, entry_flow, expected, expected_page)

        return self._run_entry_step(context, page, target_map, expected, self.entry_index, len(entry_flow))

    def _recover_entry_flow(
        self,
        context: RuntimeContext,
        page: Any,
        target_map: str,
        entry_flow: list[Any],
        expected: dict[str, Any],
        expected_page: str,
    ) -> bool:
        current_index = self._entry_index_for_page(entry_flow, page.key)
        if current_index is None:
            context.log(f"等待出击入口流程页面：expected={expected_page}, current={page.key}，当前页面不在入口流程中。")
            self._wait(context, int(expected.get("poll_wait_ms", 500)))
            return True

        current_step = entry_flow[current_index] or {}
        context.log(
            "入口流程页面错位，按当前页面重试点击："
            f"expected={expected_page}, current={page.key}, step_index={current_index}"
        )
        return self._run_entry_step(context, page, target_map, current_step, current_index, len(entry_flow))

    def _entry_index_for_page(self, entry_flow: list[Any], page_key: str) -> int | None:
        for index, step in enumerate(entry_flow):
            if isinstance(step, dict) and str(step.get("page", "")) == page_key:
                return index
        return None

    def _run_entry_step(
        self,
        context: RuntimeContext,
        page: Any,
        target_map: str,
        step: dict[str, Any],
        step_index: int,
        entry_flow_len: int,
    ) -> bool:
        action_key = self._resolve_action_key(context, step, target_map)
        if not action_key:
            context.log(f"入口流程页面缺少动作配置：page={page.key}")
            return False
        if not self._click_page_action(context, page, action_key):
            return False

        self.entry_index = step_index + 1
        if self.entry_index >= entry_flow_len:
            self.sortie_started = True
            context.log("出击入口流程已完成，进入地图循环。")
        self._wait_for_rule(context, step, 500)
        return True

    def _resolve_action_key(self, context: RuntimeContext, rule: dict[str, Any], target_map: str) -> str:
        if rule.get("action_from_map"):
            map_rule = self.rules.get("map_actions", {}).get(target_map)
            if not map_rule:
                context.log(f"sortie.yaml 未配置地图动作：{target_map}")
                return ""
            return str(map_rule.get("action", ""))
        return str(rule.get("action", ""))

    def _loop_rule(self, page_key: str) -> dict[str, Any] | None:
        rule = (self.rules.get("loop_pages", {}) or {}).get(page_key)
        return rule if isinstance(rule, dict) else None

    def _run_page_rule(self, context: RuntimeContext, page: Any, rule: dict[str, Any], max_battles: int) -> bool:
        action_type = str(rule.get("action_type", "click_action"))
        if action_type == "wait":
            self._wait_for_rule(context, rule, 500)
            return True
        if action_type == "click_action":
            return self._click_action_rule(context, page, rule)
        if action_type == "click_once":
            return self._click_action_rule(context, page, rule)
        if action_type == "click_anywhere":
            return self._click_anywhere(context, page, rule)
        if action_type == "click_until_next_page":
            return self._click_until_next_page(context, page, rule)
        if action_type == "collect_damage":
            return self._collect_damage(context, rule)
        if action_type == "choose_formation":
            return self._choose_formation(context, page, rule)
        if action_type == "advance_or_retreat":
            return self._advance_or_retreat(context, page, rule, max_battles)
        if action_type == "finish":
            context.log("已回到母港或回港页面，出击任务结束。")
            return False

        context.log(f"未知出击动作类型：{action_type}")
        return False

    def _click_action_rule(self, context: RuntimeContext, page: Any, rule: dict[str, Any]) -> bool:
        action_key = str(rule.get("action", ""))
        if action_key and not self._click_page_action(context, page, action_key):
            return False
        self._wait_for_rule(context, rule, 500)
        return True

    def _click_anywhere(self, context: RuntimeContext, page: Any, rule: dict[str, Any]) -> bool:
        action_key = str(rule.get("action", ""))
        if action_key and action_key in page.actions:
            if not self._click_page_action(context, page, action_key):
                return False
        else:
            point = rule.get("point") or self.rules.get("default_click")
            if not isinstance(point, dict):
                context.log(f"页面 {page.key} 缺少继续点击坐标。")
                return False
            self._click_xy(context, point, str(point.get("name", "继续")))
        self._wait_for_rule(context, rule, 500)
        return True

    def _click_until_next_page(self, context: RuntimeContext, page: Any, rule: dict[str, Any]) -> bool:
        action_key = str(rule.get("action", ""))
        timeout_ms = int(rule.get("timeout_ms", 5000))
        interval_ms = int(rule.get("interval_ms", 500))
        end_at = time.monotonic() + max(timeout_ms, 0) / 1000
        while time.monotonic() < end_at and not context.stop_event.is_set():
            if action_key and action_key in page.actions:
                if not self._click_page_action(context, page, action_key, wait_ms=0):
                    return False
            else:
                point = rule.get("point") or self.rules.get("default_click")
                if not isinstance(point, dict):
                    context.log(f"页面 {page.key} 缺少持续点击坐标。")
                    return False
                self._click_xy(context, point, str(point.get("name", "继续")))
            self.last_wait_ms = interval_ms
            self._wait(context, interval_ms)
            new_page = context.page_matcher.match(context.device.capture_game().image) if context.page_matcher else page
            if new_page.key != page.key:
                context.latest_page = new_page
                return True
        return True

    def _collect_damage(self, context: RuntimeContext, rule: dict[str, Any]) -> bool:
        screenshot = context.device.last_screenshot or context.device.capture_game()
        self.last_damage_report = self._detect_damage(context, screenshot.image)
        context.log(f"损伤采集：{self.last_damage_report.summary()}")
        if bool(rule.get("click", False)):
            point = rule.get("point") or self.rules.get("default_click")
            if isinstance(point, dict):
                self._click_xy(context, point, str(point.get("name", "继续")))
        self._wait_for_rule(context, rule, 500)
        return True

    def _choose_formation(self, context: RuntimeContext, page: Any, rule: dict[str, Any]) -> bool:
        sortie_config = context.config.get("sortie", {})
        formation = str(sortie_config.get("formation", "line_ahead"))
        if not self._click_page_action(context, page, formation):
            return False
        if bool(rule.get("count_battle", True)):
            self.battle_count += 1
        self._wait_for_rule(context, rule, 1000)
        return True

    def _advance_or_retreat(self, context: RuntimeContext, page: Any, rule: dict[str, Any], max_battles: int) -> bool:
        retreat_reasons: list[str] = []
        if self._should_retreat_for_damage(context):
            retreat_reasons.append("检测到撤退损伤")
        if self.battle_count >= max_battles:
            retreat_reasons.append("达到最大战斗次数")

        action_key = "retreat" if retreat_reasons else "proceed"
        if not self._click_page_action(context, page, action_key):
            return False
        if retreat_reasons:
            context.log("撤退：" + "，".join(retreat_reasons))
        else:
            context.log("继续进击。")
        self._wait_for_rule(context, rule, 1000)
        return True

    def _should_retreat_for_damage(self, context: RuntimeContext) -> bool:
        sortie_config = context.config.get("sortie", {})
        if not sortie_config.get("stop_on_heavy_damage", True):
            return False
        screenshot = context.device.last_screenshot or context.device.capture_game()
        self.last_damage_report = self._detect_damage(context, screenshot.image)
        retreat_on = [str(item) for item in self.rules.get("damage_detection", {}).get("retreat_on", ["heavy"])]
        if self.last_damage_report.has_any(retreat_on):
            context.log(f"撤退损伤命中：{self.last_damage_report.summary()}")
            return True
        context.log(f"撤退损伤未命中：{self.last_damage_report.summary()}")
        return False

    def _detect_damage(self, context: RuntimeContext, image: Any) -> DamageReport:
        damage_rules = dict(self.rules.get("damage_detection", {}) or {})
        damage_rules["hp_ocr"] = self.rules.get("hp_ocr", {}) or {}
        sortie_config = context.config.get("sortie", {})
        hp_ocr_enabled = bool(sortie_config.get("hp_ocr_enabled", self.rules.get("hp_ocr", {}).get("enabled", False)))
        detector = DamageDetector(context.paths.templates)
        return detector.detect(image, damage_rules, hp_ocr_enabled=hp_ocr_enabled)

    def _click_page_action(self, context: RuntimeContext, page: Any, action_key: str, wait_ms: int | None = None) -> bool:
        action = page.actions.get(action_key) if page.actions else None
        if not isinstance(action, dict):
            context.log(f"页面 {page.key} 缺少动作入口：{action_key}")
            return False
        self._click_xy(context, action, f"{page.key}.{action_key}")
        if wait_ms is not None:
            self._wait(context, wait_ms)
        return True

    def _click_xy(self, context: RuntimeContext, point: dict[str, Any], label: str) -> None:
        x = int(point.get("x", 0))
        y = int(point.get("y", 0))
        screen = context.device.click(x, y)
        context.log(f"{label}: logical=({x},{y}) screen={screen}")

    def _wait(self, context: RuntimeContext, ms: int) -> None:
        end_at = time.monotonic() + max(ms, 0) / 1000
        while time.monotonic() < end_at and not context.stop_event.is_set():
            time.sleep(min(0.05, end_at - time.monotonic()))

    def _wait_for_rule(self, context: RuntimeContext, rule: dict[str, Any], default_ms: int) -> None:
        self.last_wait_ms = int(rule.get("wait_ms", default_ms))
        self._wait(context, self.last_wait_ms)
