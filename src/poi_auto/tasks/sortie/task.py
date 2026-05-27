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
        self.states = {state["name"] if "name" in state else name: state for name, state in self.rules.get("states", {}).items()}
        self.current_state = str(self.rules.get("initial_state", "home"))
        self.run_count = 0

    def step(self, context: RuntimeContext) -> bool:
        state = self.states.get(self.current_state)
        if not state:
            context.log(f"未找到状态：{self.current_state}")
            return False

        screenshot = context.device.capture_game()
        context.log(
            "截图完成："
            f"region=({screenshot.source_region.left},{screenshot.source_region.top},"
            f"{screenshot.source_region.width}x{screenshot.source_region.height}), "
            f"scale=({screenshot.scale_x:.3f},{screenshot.scale_y:.3f}), "
            f"state={self.current_state}"
        )

        detect_rule = state.get("detect", {"type": "always"})
        match = context.recognizer.detect(screenshot.image, detect_rule, context.flags)
        if not match.matched:
            self._log_detection_failure(context, detect_rule, match)
            return self._handle_fail(context, state)

        for action in state.get("actions", []):
            if context.stop_event.is_set():
                return False
            should_continue = self._run_action(context, action)
            if not should_continue:
                return False

        next_state = state.get("next")
        if next_state is None:
            return False
        self.current_state = str(next_state)
        return True

    def _run_action(self, context: RuntimeContext, action: dict[str, Any]) -> bool:
        action_type = action.get("type")
        if action_type == "log":
            context.log(str(action.get("message", "")))
            return True
        if action_type == "wait":
            self._wait(context, int(action.get("ms", 0)))
            return True
        if action_type == "click":
            point_name = str(action.get("point", ""))
            point = self.rules.get("points", {}).get(point_name)
            if not point:
                context.log(f"点击点不存在：{point_name}")
                return False
            screen = context.device.click(int(point["x"]), int(point["y"]))
            context.log(f"点击：{point_name}({point.get('name', '')}) logical=({point['x']},{point['y']}) screen={screen}")
            return True
        if action_type == "formation":
            formation = context.config.get("sortie", {}).get("formation", "line_ahead")
            point = self.rules.get("formation_points", {}).get(formation)
            if not point:
                context.log(f"阵型未配置：{formation}")
                return False
            screen = context.device.click(int(point["x"]), int(point["y"]))
            context.log(f"选择阵型：{point.get('name', formation)} logical=({point['x']},{point['y']}) screen={screen}")
            return True
        if action_type == "damage_check":
            return self._damage_check(context)
        if action_type == "stop":
            context.log("规则请求停止。")
            return False
        context.log(f"未知动作类型：{action_type}")
        return False

    def _damage_check(self, context: RuntimeContext) -> bool:
        screenshot = context.device.last_screenshot or context.device.capture_game()
        damage_rules = self.rules.get("damage_rules", {})
        sortie_config = context.config.get("sortie", {})
        for name, rule in damage_rules.items():
            match = context.recognizer.detect(screenshot.image, rule.get("detect", {}), context.flags)
            if not match.matched:
                if match.template_path and not match.template_path.exists():
                    context.log(f"血量模板缺失：{match.template_path}")
                continue
            action = rule.get("action")
            context.log(f"检测到血量规则：{name}, action={action}, score={match.score:.3f}")
            if name == "heavy_damage" and sortie_config.get("stop_on_heavy_damage", True):
                context.log("检测到大破规则，按配置停止。")
                return False
            if name == "medium_damage" and sortie_config.get("retreat_on_medium_damage", False):
                retreat = self.rules.get("points", {}).get("retreat")
                if retreat:
                    context.device.click(int(retreat["x"]), int(retreat["y"]))
                    context.log("检测到中破规则，按配置点击撤退。")
                return False
        return True

    def _handle_fail(self, context: RuntimeContext, state: dict[str, Any]) -> bool:
        on_fail = state.get("on_fail", "stop")
        if on_fail == "retry":
            self._wait(context, 500)
            return True
        if on_fail and on_fail != "stop":
            self.current_state = str(on_fail)
            context.log(f"识别失败，跳转到：{self.current_state}")
            return True
        context.log("识别失败，任务停止。")
        return False

    def _log_detection_failure(self, context: RuntimeContext, rule: dict[str, Any], match: Any) -> None:
        template_path = getattr(match, "template_path", None)
        if template_path:
            context.log(f"识别失败：template={template_path}, score={match.score:.3f}")
        else:
            context.log(f"识别失败：rule={rule}, score={match.score:.3f}")

    def _wait(self, context: RuntimeContext, ms: int) -> None:
        end_at = time.monotonic() + max(ms, 0) / 1000
        while time.monotonic() < end_at and not context.stop_event.is_set():
            time.sleep(min(0.05, end_at - time.monotonic()))

