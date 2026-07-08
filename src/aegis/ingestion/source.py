"""The LogSource seam every transport implements."""

from collections.abc import AsyncGenerator
from typing import Protocol

from aegis.events import RawLogEvent


class LogSource(Protocol):
    """A stream of raw log records with stable provenance.

    ``stream`` returns an async generator (or an equally ``aclose``-able
    iterator): it must never load the whole input into memory, and it must
    release underlying resources when closed -- the supervisor guarantees a
    deterministic ``aclose`` on every exit path, including cancellation, so a
    ``finally`` block around the yields is the expected implementation shape.
    """

    source_id: str

    def stream(self) -> AsyncGenerator[RawLogEvent]: ...
