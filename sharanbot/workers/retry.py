"""Worker retry manager — exponential backoff for failed workers.

When a worker task fails, this manager handles retry scheduling with
configurable backoff delays and maximum attempt limits.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger


@dataclass
class RetryRecord:
    """Tracks retry state for a single task."""

    task_id: str
    worker_id: str
    attempt: int = 0
    max_attempts: int = 3
    last_error: str = ""
    next_retry_at: float = 0.0
    status: str = "pending"  # pending, retrying, succeeded, exhausted
    created_at: float = field(default_factory=time.time)


class WorkerRetryManager:
    """Manages retry logic with exponential backoff.

    Parameters
    ----------
    base_delay_s:
        Base delay for the first retry (default 30s).
    max_delay_s:
        Maximum delay between retries (default 300s = 5min).
    max_attempts:
        Maximum number of retry attempts per task.
    backoff_factor:
        Multiplier for each successive delay (default 2.0).
    """

    def __init__(
        self,
        base_delay_s: float = 30.0,
        max_delay_s: float = 300.0,
        max_attempts: int = 3,
        backoff_factor: float = 2.0,
    ):
        self._base_delay = base_delay_s
        self._max_delay = max_delay_s
        self._max_attempts = max_attempts
        self._backoff = backoff_factor
        self._records: dict[str, RetryRecord] = {}

    def record_failure(
        self, task_id: str, worker_id: str, error: str,
    ) -> RetryRecord:
        """Record a task failure and schedule a retry.

        Returns the updated RetryRecord. Check record.status to see if
        retries are exhausted.
        """
        rec = self._records.get(task_id)
        if rec is None:
            rec = RetryRecord(
                task_id=task_id,
                worker_id=worker_id,
                max_attempts=self._max_attempts,
            )
            self._records[task_id] = rec

        rec.attempt += 1
        rec.last_error = error[:500]

        if rec.attempt >= rec.max_attempts:
            rec.status = "exhausted"
            logger.warning("Task {} exhausted retries ({}/{})",
                           task_id, rec.attempt, rec.max_attempts)
        else:
            delay = min(
                self._base_delay * (self._backoff ** (rec.attempt - 1)),
                self._max_delay,
            )
            rec.next_retry_at = time.time() + delay
            rec.status = "pending"
            logger.info("Task {} retry #{} scheduled in {:.0f}s",
                        task_id, rec.attempt + 1, delay)

        return rec

    def record_success(self, task_id: str) -> None:
        """Mark a task as successfully completed (removes from retry queue)."""
        rec = self._records.get(task_id)
        if rec:
            rec.status = "succeeded"
            logger.debug("Task {} succeeded on attempt {}", task_id, rec.attempt)

    def get_due_retries(self) -> list[RetryRecord]:
        """Return tasks that are due for retry NOW."""
        now = time.time()
        return [
            rec for rec in self._records.values()
            if rec.status == "pending" and rec.next_retry_at <= now
        ]

    def get_record(self, task_id: str) -> RetryRecord | None:
        return self._records.get(task_id)

    def should_retry(self, task_id: str) -> bool:
        rec = self._records.get(task_id)
        if rec is None:
            return True  # first attempt
        return rec.status == "pending" and rec.attempt < rec.max_attempts

    def get_delay(self, task_id: str) -> float:
        """Return how many seconds to wait before the next retry."""
        rec = self._records.get(task_id)
        if rec is None:
            return 0.0
        remaining = rec.next_retry_at - time.time()
        return max(0.0, remaining)

    async def wait_and_retry(
        self,
        task_id: str,
        retry_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Wait for the scheduled delay, then execute the retry function."""
        delay = self.get_delay(task_id)
        if delay > 0:
            await asyncio.sleep(delay)

        rec = self._records.get(task_id)
        if rec:
            rec.status = "retrying"

        return await retry_fn()

    def get_stats(self) -> dict[str, Any]:
        from collections import Counter
        statuses = Counter(r.status for r in self._records.values())
        return {
            "total_tracked": len(self._records),
            "pending": statuses.get("pending", 0),
            "retrying": statuses.get("retrying", 0),
            "succeeded": statuses.get("succeeded", 0),
            "exhausted": statuses.get("exhausted", 0),
        }

    def clear_completed(self) -> int:
        """Remove succeeded/exhausted records. Returns count removed."""
        to_remove = [
            tid for tid, rec in self._records.items()
            if rec.status in ("succeeded", "exhausted")
        ]
        for tid in to_remove:
            del self._records[tid]
        return len(to_remove)
