"""Incident memory: what retrieval actually is here (and is not).

When a new incident is investigated, its summary is embedded and the top-k
most similar *historical incidents* are retrieved from pgvector -- their
root causes and remediations become evidence items with provenance in the
EvidenceBundle. That is retrieval-augmented investigation context: the
retrieved text tells investigators "last time this pattern appeared, the
cause was X", it is never blindly pasted into conclusions. Nothing else in
the system is called RAG.
"""

from aegis.memory.embeddings import EmbeddingProvider, HashingEmbedder, VoyageEmbedder
from aegis.memory.service import IncidentMemory, SimilarIncident

__all__ = [
    "EmbeddingProvider",
    "HashingEmbedder",
    "IncidentMemory",
    "SimilarIncident",
    "VoyageEmbedder",
]
