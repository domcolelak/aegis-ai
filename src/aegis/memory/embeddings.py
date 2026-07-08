"""Embedding providers.

VoyageEmbedder is the production path (Anthropic's recommended embedding
partner; plain httpx, no extra SDK). HashingEmbedder is the deterministic
fallback used by tests and the offline demo: token-bucket hashing gives
"shared vocabulary => nearby vectors", which is exactly enough to exercise
pgvector retrieval without a paid API. Both emit EMBEDDING_DIM dimensions so
they are drop-in interchangeable against the same column.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Protocol

import httpx

from aegis.core.errors import (
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from aegis.db.models import EMBEDDING_DIM

if TYPE_CHECKING:
    from collections.abc import Sequence


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_TOKEN = re.compile(r"[a-z0-9]{2,}")


class HashingEmbedder:
    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            # Second hash byte decides the sign: keeps buckets from only
            # accumulating, which would make every long text look alike.
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class VoyageEmbedder:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "voyage-3.5-lite",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = await self._client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": list(texts)},
            )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc
        if response.status_code == 429:
            raise ProviderRateLimitedError("voyage rate limit")
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"voyage {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(f"voyage {response.status_code}: {response.text[:200]}")
        payload = response.json()
        return [item["embedding"] for item in payload["data"]]
