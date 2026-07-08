"""Resilience decorators composed around any LLMProvider.

The composition root stacks them: RetryingProvider(RateLimitedProvider(real)).
The semaphore bounds concurrent in-flight requests (agents run in parallel);
retries apply backoff with a shared budget so a rate-limited investigation
degrades instead of amplifying.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aegis.core.errors import ProviderRateLimitedError, ProviderUnavailableError
from aegis.core.resilience import RetryPolicy, retry_async

if TYPE_CHECKING:
    from aegis.core.resilience import RetryBudget
    from aegis.investigation.providers.base import Completion, CompletionRequest, LLMProvider


class RateLimitedProvider:
    def __init__(self, inner: LLMProvider, *, max_concurrent: int = 4) -> None:
        self._inner = inner
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def complete(self, request: CompletionRequest) -> Completion:
        async with self._semaphore:
            return await self._inner.complete(request)


class RetryingProvider:
    """Retries only transient failures; auth/validation errors surface fast."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        policy: RetryPolicy | None = None,
        budget: RetryBudget | None = None,
    ) -> None:
        self._inner = inner
        self._policy = policy or RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=20.0)
        self._budget = budget

    async def complete(self, request: CompletionRequest) -> Completion:
        return await retry_async(
            lambda: self._inner.complete(request),
            retry_on=(ProviderRateLimitedError, ProviderUnavailableError),
            policy=self._policy,
            budget=self._budget,
        )
