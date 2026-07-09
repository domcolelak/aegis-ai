"""Deterministic synthetic incident data.

Shipped inside the package (not under tests/) because three consumers need
it: the end-to-end test suite, the demo script, and the ingest benchmark.
Everything is seeded -- the same seed always produces byte-identical logs.
"""

from aegis.synthetic.incident import SyntheticIncident, generate, materialize
from aegis.synthetic.repo import materialize_repo

__all__ = ["SyntheticIncident", "generate", "materialize", "materialize_repo"]
