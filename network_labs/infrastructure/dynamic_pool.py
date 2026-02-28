import threading
import time
from queue import Queue, Empty
from typing import Callable, Dict

from network_labs.config import DynamicPoolConfig


class DynamicThreadPool:
    def __init__(self, config: DynamicPoolConfig) -> None:
        self._min = config.min_threads
        self._max = config.max_threads
        self._idle_timeout = config.idle_timeout
        self._queue: Queue = Queue()
        self._lock = threading.Lock()
        self._active = 0
        self._total = 0
        self._running = True

    def start(self) -> None:
        for _ in range(self._min):
            self._add_worker()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def submit(self, task: Callable[[], None]) -> None:
        self._queue.put(task)
        self._expand_if_needed()

    @property
    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total": self._total,
                "active": self._active,
                "free": self._total - self._active,
            }

    def shutdown(self) -> None:
        self._running = False

    def _monitor_loop(self) -> None:
        prev_total = self._min
        while self._running:
            time.sleep(2.0)
            with self._lock:
                total = self._total
                active = self._active
            if total != prev_total:
                free = total - active
                print(f"[Pool] threads={total} active={active} free={free}")
                prev_total = total
            self._expand_if_needed()

    def _expand_if_needed(self) -> None:
        need_spawn = False
        with self._lock:
            if self._total - self._active <= 0 and self._total < self._max:
                self._total += 1
                need_spawn = True
        if need_spawn:
            threading.Thread(target=self._worker_loop, daemon=True).start()

    def _add_worker(self) -> None:
        with self._lock:
            self._total += 1
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self) -> None:
        while self._running:
            try:
                task = self._queue.get(timeout=self._idle_timeout)
            except Empty:
                if self._try_shrink():
                    return
                continue
            self._run_task(task)

    def _run_task(self, task: Callable[[], None]) -> None:
        with self._lock:
            self._active += 1
        try:
            task()
        except Exception as exc:
            print(f"[ThreadPool] Error: {exc}")
        finally:
            with self._lock:
                self._active -= 1

    def _try_shrink(self) -> bool:
        with self._lock:
            if self._total > self._min:
                self._total -= 1
                return True
        return False
