"""Log template extraction: mask the variable parts of a message.

Two messages that differ only in ids, addresses, or measurements collapse to
the same template ("connection timeout after <NUM>ms"), which is the
deduplication unit for anomaly bookkeeping and embeddings. Pure and picklable;
runs in worker processes.
"""

import re

from aegis.events import EventSignature

# Order matters: composite shapes (timestamps, UUIDs) must be masked before
# their fragments would match as plain hex or numbers.
_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
        ),
        "<TS>",
    ),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{12,}\b"), "<HEX>"),
    # No word boundaries: "3000ms" and "web-2" must mask too (digit-to-letter
    # transitions have no \b), and that aggressiveness is what makes templates
    # collapse well.
    (re.compile(r"\d+(?:\.\d+)?"), "<NUM>"),
)


def mask_message(message: str) -> str:
    template = message
    for pattern, token in _MASKS:
        template = pattern.sub(token, template)
    return template


def signature_of(message: str) -> EventSignature:
    return EventSignature.from_template(mask_message(message))
