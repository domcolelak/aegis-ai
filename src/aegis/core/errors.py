"""Exception hierarchy for Aegis.

Every error raised by Aegis derives from AegisError so callers can separate
domain failures from programming errors. Subclasses are added together with
the modules that raise them; no speculative branches.
"""


class AegisError(Exception):
    """Base class for all errors raised by Aegis."""


class ConfigurationError(AegisError):
    """Invalid or missing runtime configuration."""


class ChannelClosedError(AegisError):
    """A pipeline channel was used after being closed and drained."""


class IngestionError(AegisError):
    """A log source failed while streaming; the cause is chained."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        super().__init__(f"ingestion failed for source {source_id!r}")


class InvestigationError(AegisError):
    """An AI investigation could not produce a valid result."""


class ProviderError(AegisError):
    """An LLM provider call failed for a non-transient reason."""


class ProviderRateLimitedError(ProviderError):
    """The provider throttled us; retryable with backoff."""


class ProviderUnavailableError(ProviderError):
    """Transient provider failure (5xx, connection, timeout); retryable."""


class RepositoryAccessError(AegisError):
    """Source-repository access denied or misconfigured (path escape, size cap)."""


class RetryExhaustedError(AegisError):
    """All retry attempts failed; the last failure is chained as __cause__."""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"retry gave up after {attempts} attempt(s)")
