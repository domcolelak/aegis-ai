"""Causal evidence graph and the deterministic analysis on top of it.

Where the graph algorithms genuinely earn their place:

- **SCC condensation**: retry storms create real cycles (timeout -> retry ->
  timeout); condensing them into super-nodes is what makes topological
  reasoning possible at all.
- **Topological order** over the condensation gives the failure timeline even
  when clock skew makes raw timestamps lie slightly.
- **Reverse reachability** from root SCCs measures blast radius, the main
  ingredient of root-candidate scoring.
- **Dijkstra over -log(edge score)** finds the single strongest causal chain
  (maximum product of edge plausibilities) from a candidate to the visible
  failure.
"""

from aegis.graph.incident_graph import IncidentGraph
from aegis.graph.models import RootCandidate

__all__ = ["IncidentGraph", "RootCandidate"]
