from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from poi_auto.core.config import load_yaml
from poi_auto.core.context import RuntimeContext
from poi_auto.tasks.base import BaseTask


class SortieTask(BaseTask):
    name = "sortie"

    def __init__(self, rules_path: Path) -> None:
        self.rules_path = rules_path
        self.rules = load_yaml(rules_path)
        self.battle_count = 0
        self.map_selected = False
        self.map_started = False
        self.returning_home = False

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
            context.log("当前页面未知，出击任务停止，避免盲点。")
            return False

        if page.key == "home":
            if self.returning_home or self.battle_count > 0:
                context.log("已回到母港，出击任务结束。")
                return False
            return self._enter_sortie(context, page)

        if page.key == "sortie_menu":
            return self._handle_sortie_menu(context, screenshot.image, target_map)

        if page.key == "formation":
            return self._choose_formation(context)

        if page.key == "battle":
            self._wait(context, 1000)
            return True

        if page.key == "battle_result":
            self._wait(context, 800)
            return True

        if page.key == "advance_or_retreat":
            return self._advance_or_retreat(context, max_battles)

        context.log(f"页面 {page.key} 暂未纳入出击流程，任务停止。")
        return False

    def _enter_sortie(self, context: RuntimeContext, page: Any) -> bool:
        action = page.actions.get("sortie") if page.actions else None
        if action:
            self._click_xy(context, action, "母港出击入口")
        else:
            point = self.rules.get("points", {}).get("sortie_entry")
            if not point:
                context.log("缺少母港出击入口：pages.home.actions.sortie 或 points.sortie_entry。")
                return False
            self._click_xy(context, point, "母港出击入口")
        self._wait(context, 800)
        return True

    def _handle_sortie_menu(self, context: RuntimeContext, image: Any, target_map: str) -> bool:
        map_rule = self.rules.get("maps", {}).get(target_map)
        if not map_rule:
            context.log(f"sortie.yaml 未配置海域：{target_map}")
            return False

        detect_rule = map_rule.get("detect")
        if detect_rule:
            match = context.recognizer.detect(image, detect_rule, context.flags)
            if not match.matched:
                if match.template_path and not match.template_path.exists():
                    context.log(f"海域模板缺失：{match.template_path}")
                else:
                    context.log(f"海域模板未命中：map={target_map}, score={match.score:.3f}")
                return False

        if not self.map_selected:
            self._click_xy(context, map_rule.get("select", {}), f"选择海域 {target_map}")
            self.map_selected = True
            self._wait(context, 700)
            return True

        if not self.map_started:
            self._click_xy(context, map_rule.get("start", {}), f"开始出击 {target_map}")
            self.map_started = True
            self._wait(context, 1000)
            return True

        self._wait(context, 500)
        return True

    def _choose_formation(self, context: RuntimeContext) -> bool:
        sortie_config = context.config.get("sortie", {})
        formation = str(sortie_config.get("formation", "line_ahead"))
        point = self.rules.get("formation_points", {}).get(formation)
        if not point:
            context.log(f"阵型未配置：{formation}")
            return False
        self.battle_count += 1
        self._click_xy(context, point, f"选择阵型 {point.get('name', formation)}")
        self._wait(context, 1200)
        return True

    def _advance_or_retreat(self, context: RuntimeContext, max_battles: int) -> bool:
        if self._should_retreat_for_damage(context):
            return self._retreat_and_return_home(context, "检测到大破，撤退。")

        if self.battle_count >= max_battles:
            return self._retreat_and_return_home(context, "达到最大战斗次数，撤退并回母港。")

        point = self.rules.get("points", {}).get("proceed")
        if not point:
            context.log("缺少进击坐标：points.proceed。")
            return False
        self._click_xy(context, point, "进击")
        self._wait(context, 1000)
        return True

    def _should_retreat_for_damage(self, context: RuntimeContext) -> bool:
        sortie_config = context.config.get("sortie", {})
        if not sortie_config.get("stop_on_heavy_damage", True):
            return False
        screenshot = context.device.last_screenshot or context.device.capture_game()
        heavy_rule = self.rules.get("damage_rules", {}).get("heavy_damage")
        if not heavy_rule:
            return False
        match = context.recognizer.detect(screenshot.image, heavy_rule.get("detect", {}), context.flags)
        if match.matched:
            context.log(f"大破模板命中：score={match.score:.3f}")
            return True
        if match.template_path and not match.template_path.exists():
            context.log(f"大破模板缺失：{match.template_path}")
        return False

    def _retreat_and_return_home(self, context: RuntimeContext, reason: str) -> bool:
        context.log(reason)
        retreat = self.rules.get("points", {}).get("retreat")
        if retreat:
            self._click_xy(context, retreat, "撤退")
            self._wait(context, 1000)
        return_home = self.rules.get("points", {}).get("return_home")
        if return_home:
            self._click_xy(context, return_home, "返回母港")
            self.returning_home = True
            self._wait(context, 1000)
            return True
        context.log("缺少返回母港坐标：points.return_home。")
        return False

    def _click_xy(self, context: RuntimeContext, point: dict[str, Any], label: str) -> None:
        x = int(point.get("x", 0))
        y = int(point.get("y", 0))
        screen = context.device.click(x, y)
        context.log(f"{label}: logical=({x},{y}) screen={screen}")

    def _wait(self, context: RuntimeContext, ms: int) -> None:
        end_at = time.monotonic() + max(ms, 0) / 1000
        while time.monotonic() < end_at and not context.stop_event.is_set():
            time.sleep(min(0.05, end_at - time.monotonic()))
