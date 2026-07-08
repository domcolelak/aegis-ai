"""Graph-analysis result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aegis.events import LogEvent


@dataclass(slots=True, frozen=True)
class RootCandidate:
    """A probable incident origin with the numbers behind its ranking.

    ``event`` is the earliest member of the candidate's strongly connected
    component; ``reach`` is the fraction of all graph events causally
    downstream of it; ``earliness`` positions it in the incident time span;
    ``impact_weight`` is the worst severity (or anomaly confidence) among the
    component's members.
    """

    event: LogEvent
    score: float
    reach: float
    earliness: float
    impact_weight: float
    scc_size: int
