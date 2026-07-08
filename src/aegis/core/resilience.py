"""Retry with exponential backoff, full jitter, and a shared retry budget.

Hand-rolled instead of pulling in tenacity: the pieces Aegis needs beyond
plain backoff -- a cross-call retry budget and an injectable sleep for
deterministic tests -- would be custom either way, and the whole module is
under a hundred lines.

The budget guards against retry amplification: when a downstream dependency
fails broadly, call sites stop retrying once the shared budget is spent
instead of multiplying load -- the exact retry-storm pattern this system
diagnoses in other people's infrastructure.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aegis.core.errors import RetryExhaustedError


@dataclass(slots=True, frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("delays must satisfy 0 < base_delay <= max_delay")

    def delay_for(self, attempt: int) -> float:
        """Delay before the given retry (attempt 1 = first retry)."""
        capped = min(self.max_delay, self.base_delay * 2.0 ** (attempt - 1))
        if self.jitter:
            # Full jitter: uniform in [0, capped]. Decorrelates callers that
            # started failing at the same moment.
            return random.uniform(0.0, capped)
        return capped


class RetryBudget:
    """Shared cap on the total number of retries across call sites."""

    def __init__(self, max_retries: int) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._remaining = max_retries

    @property
    def remaining(self) -> int:
        return self._remaining

    def try_acquire(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retry_on: tuple[type[Exception], ...],
    policy: RetryPolicy | None = None,
    budget: RetryBudget | None = None,
    on_retry: Callable[[int, float, Exception], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``fn``, retrying on the listed exception types.

    Raises RetryExhaustedError (last failure chained as __cause__) when
    attempts or the shared budget run out. Exceptions outside ``retry_on``
    propagate immediately; cancellation is never swallowed.
    """
    policy = policy or RetryPolicy()
    attempts = 0
    while True:
        attempts += 1
        try:
            return await fn()
        except retry_on as exc:
            out_of_attempts = attempts >= policy.max_attempts
            out_of_budget = budget is not None and not budget.try_acquire()
            if out_of_attempts or out_of_budget:
                raise RetryExhaustedError(attempts) from exc
            delay = policy.delay_for(attempts)
            if on_retry is not None:
                on_retry(attempts, delay, exc)
            await sleep(delay)
