from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Callable
from uuid import uuid4


TaskCallable = Callable[..., Any]


@dataclass(slots=True)
class TaskSubmission:
    task_id: str
    task_name: str
    backend: str
    submitted_at: datetime


class TaskRunner(ABC):
    @abstractmethod
    def submit(self, task_name: str, func: TaskCallable, *args: Any, **kwargs: Any) -> TaskSubmission:
        """Submit work for background execution."""

    @abstractmethod
    def get_status(self, task_id: str) -> str:
        """Return the current status for a task."""

    @abstractmethod
    def get_result(self, task_id: str, timeout: float | None = None) -> Any:
        """Block until task completion and return the result."""

    @abstractmethod
    def shutdown(self, wait: bool = True) -> None:
        """Release worker resources."""


class LocalTaskRunner(TaskRunner):
    def __init__(self, max_workers: int = 2) -> None:
        self.backend = "local"
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="weekly_report-task",
        )
        self._futures: dict[str, Future[Any]] = {}
        self._lock = Lock()

    def submit(self, task_name: str, func: TaskCallable, *args: Any, **kwargs: Any) -> TaskSubmission:
        task_id = str(uuid4())
        future = self._executor.submit(func, *args, **kwargs)
        with self._lock:
            self._futures[task_id] = future

        return TaskSubmission(
            task_id=task_id,
            task_name=task_name,
            backend=self.backend,
            submitted_at=datetime.now(UTC),
        )

    def get_status(self, task_id: str) -> str:
        future = self._futures.get(task_id)
        if future is None:
            return "unknown"
        if future.cancelled():
            return "cancelled"
        if future.running():
            return "running"
        if future.done():
            return "failed" if future.exception() else "completed"
        return "pending"

    def get_result(self, task_id: str, timeout: float | None = None) -> Any:
        future = self._futures.get(task_id)
        if future is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return future.result(timeout=timeout)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)


class RedisTaskRunner(TaskRunner):
    def submit(self, task_name: str, func: TaskCallable, *args: Any, **kwargs: Any) -> TaskSubmission:
        raise NotImplementedError("RedisTaskRunner will be implemented in a later phase.")

    def get_status(self, task_id: str) -> str:
        raise NotImplementedError("RedisTaskRunner will be implemented in a later phase.")

    def get_result(self, task_id: str, timeout: float | None = None) -> Any:
        raise NotImplementedError("RedisTaskRunner will be implemented in a later phase.")

    def shutdown(self, wait: bool = True) -> None:
        return None
