"""Parsing and normalization: RawLogEvent bytes -> typed LogEvents.

Split along the concurrency boundary on purpose:

- ``masking``, ``classify``, ``formats``, ``cpu`` are pure, picklable,
  asyncio-free functions -- they run inside worker processes. Large-scale
  regex parsing and template extraction are genuinely GIL-bound, which is why
  this is the one place in Aegis that crosses a process boundary.
- ``batcher`` and ``stage`` are the asyncio side: they group raw events into
  batches (amortizing pickling overhead) and shuttle them through an executor
  without ever blocking the event loop.
"""

from aegis.parsing.batcher import batched
from aegis.parsing.cpu import parse_batch, parse_one
from aegis.parsing.masking import mask_message, signature_of
from aegis.parsing.stage import ParsingStage

__all__ = [
    "ParsingStage",
    "batched",
    "mask_message",
    "parse_batch",
    "parse_one",
    "signature_of",
]
