"""Signature similarity seam.

TokenJaccardSimilarity is the deterministic default: cheap, dependency-free,
and good enough for templates from the same log vocabulary. An
embedding-backed implementation (pgvector milestone) satisfies the same
protocol; because similarity is computed between a few hundred unique
*templates* rather than millions of events, either implementation is cheap.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from aegis.events import EventSignature

_TOKEN = re.compile(r"[a-z]{3,}")
# Mask placeholders and short glue words carry no meaning between templates.
_STOPWORDS = frozenset({"num", "uuid", "hex", "the", "for", "and", "with", "while", "after"})


class SignatureSimilarity(Protocol):
    def score(self, a: EventSignature, b: EventSignature) -> float: ...


class TokenJaccardSimilarity:
    def __init__(self) -> None:
        self._tokens: dict[str, frozenset[str]] = {}

    def score(self, a: EventSignature, b: EventSignature) -> float:
        if a.fingerprint == b.fingerprint:
            return 1.0
        tokens_a = self._tokenize(a)
        tokens_b = self._tokenize(b)
        if not tokens_a or not tokens_b:
            return 0.0
        overlap = len(tokens_a & tokens_b)
        return overlap / len(tokens_a | tokens_b)

    def _tokenize(self, signature: EventSignature) -> frozenset[str]:
        cached = self._tokens.get(signature.fingerprint)
        if cached is None:
            cached = frozenset(_TOKEN.findall(signature.template.lower())) - _STOPWORDS
            self._tokens[signature.fingerprint] = cached
        return cached
