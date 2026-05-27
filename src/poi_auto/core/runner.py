from __future__ import annotations

import threading
import traceback
from typing import Callable

from poi_auto.core.context import RuntimeContext
from poi_auto.tasks.base import BaseTask


class TaskRunner:
    def __init__(self, context_factory: Callable[[threading.Event], RuntimeContext]) -> None:
        self._context_factory = context_factory
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, task: BaseTask) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self._stop_event = threading.Event()
            context = self._context_factory(self._stop_event)
            self._thread = threading.Thread(
                target=self._run_task,
                args=(task, context),
                daemon=True,
                name=f"poi-auto-{task.name}",
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()

    def _run_task(self, task: BaseTask, context: RuntimeContext) -> None:
        try:
            context.log(f"任务启动：{task.name}")
            task.run_loop(context)
        except Exception:
            context.log("任务异常：\n" + traceback.format_exc())
        finally:
            context.log(f"任务结束：{task.name}")

