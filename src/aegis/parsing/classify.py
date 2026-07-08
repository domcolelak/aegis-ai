"""EventKind classification from message text.

Deliberately transparent ordered rules instead of a model: the kinds feed
correlation scoring, so a wrong-but-explainable label beats an opaque one.
Rule order encodes specificity -- a retry log usually also mentions the
exception it retries on, and for causal reasoning TASK_RETRY is the more
useful label. Pure and picklable; runs in worker processes.
"""

import re

from aegis.events import EventKind

_RULES: tuple[tuple[EventKind, re.Pattern[str]], ...] = (
    (EventKind.TASK_RETRY, re.compile(r"\bretr(?:y|ying|ies|ied)\b", re.IGNORECASE)),
    (
        EventKind.DB_POOL,
        re.compile(
            r"connection pool|pool (?:exhaust|timeout|limit|size)"
            r"|too many connections|remaining connection slots"
            r"|QueuePool",
            re.IGNORECASE,
        ),
    ),
    (
        EventKind.DB_QUERY,
        re.compile(
            r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b.*\b(?:FROM|INTO|SET|WHERE)\b"
            r"|sqlalchemy|deadlock|\bquery\b|database session",
            re.IGNORECASE,
        ),
    ),
    (
        EventKind.EXTERNAL_CALL,
        re.compile(r"\bstripe\b|external api|upstream|third[- ]party|webhook", re.IGNORECASE),
    ),
    (EventKind.HTTP_REQUEST, re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/\S*|\bHTTP/\d")),
    (
        EventKind.EXCEPTION,
        re.compile(r"\btraceback\b|\b\w+(?:Error|Exception)\b|\bpanic\b", re.IGNORECASE),
    ),
    (
        EventKind.LIFECYCLE,
        re.compile(
            r"\b(?:starting|started|stopping|stopped|shut(?:ting)? ?down|listening on|booted)\b",
            re.IGNORECASE,
        ),
    ),
)


def classify_kind(message: str) -> EventKind:
    for kind, pattern in _RULES:
        if pattern.search(message):
            return kind
    return EventKind.GENERIC
