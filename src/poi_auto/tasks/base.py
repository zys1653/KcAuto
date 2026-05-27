from __future__ import annotations

from abc import ABC, abstractmethod

from poi_auto.core.context import RuntimeContext


class BaseTask(ABC):
    name = "base"

    @abstractmethod
    def step(self, context: RuntimeContext) -> bool:
        """Run one task step. Return False when the task should stop."""

    def run_loop(self, context: RuntimeContext) -> None:
        while not context.stop_event.is_set():
            if not self.step(context):
                break

