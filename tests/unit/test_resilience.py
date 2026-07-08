import pytest

from aegis.core.errors import RetryExhaustedError
from aegis.core.resilience import RetryBudget, RetryPolicy, retry_async


class Flaky:
    """Coroutine callable that fails N times, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError(f"boom {self.calls}")
        return "ok"


async def _instant(_delay: float) -> None:
    return None


async def test_recovers_after_transient_failures_with_expected_backoff() -> None:
    fn = Flaky(failures=2)
    delays: list[float] = []
    retried: list[int] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    result = await retry_async(
        fn,
        retry_on=(ConnectionError,),
        policy=RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=10.0, jitter=False),
        on_retry=lambda attempt, _delay, _exc: retried.append(attempt),
        sleep=record_sleep,
    )

    assert result == "ok"
    assert fn.calls == 3
    assert delays == [1.0, 2.0]
    assert retried == [1, 2]


async def test_exhaustion_chains_last_error() -> None:
    fn = Flaky(failures=10)

    with pytest.raises(RetryExhaustedError) as excinfo:
        await retry_async(
            fn,
            retry_on=(ConnectionError,),
            policy=RetryPolicy(max_attempts=3),
            sleep=_instant,
        )

    assert excinfo.value.attempts == 3
    assert isinstance(excinfo.value.__cause__, ConnectionError)
    assert fn.calls == 3


async def test_unlisted_exception_propagates_immediately() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError, match="not retryable"):
        await retry_async(fn, retry_on=(ConnectionError,), sleep=_instant)

    assert calls == 1


async def test_shared_budget_limits_total_retries() -> None:
    budget = RetryBudget(max_retries=1)
    policy = RetryPolicy(max_attempts=5)

    first = Flaky(failures=1)
    result = await retry_async(
        first, retry_on=(ConnectionError,), policy=policy, budget=budget, sleep=_instant
    )
    assert result == "ok"
    assert budget.remaining == 0

    second = Flaky(failures=1)
    with pytest.raises(RetryExhaustedError):
        await retry_async(
            second, retry_on=(ConnectionError,), policy=policy, budget=budget, sleep=_instant
        )
    # The budget was spent, so the second call site got no retry at all.
    assert second.calls == 1


def test_delay_doubles_then_caps() -> None:
    policy = RetryPolicy(max_attempts=10, base_delay=1.0, max_delay=4.0, jitter=False)

    assert [policy.delay_for(n) for n in range(1, 6)] == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_jittered_delay_stays_within_bounds() -> None:
    policy = RetryPolicy(max_attempts=10, base_delay=1.0, max_delay=4.0, jitter=True)

    for attempt in range(1, 8):
        assert 0.0 <= policy.delay_for(attempt) <= 4.0


def test_invalid_policy_rejected() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="delays"):
        RetryPolicy(base_delay=0.0)
    with pytest.raises(ValueError, match="delays"):
        RetryPolicy(base_delay=2.0, max_delay=1.0)


def test_budget_rejects_negative_cap() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        RetryBudget(max_retries=-1)
