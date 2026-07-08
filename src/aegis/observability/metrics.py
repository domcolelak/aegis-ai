"""Metrics seam and the Prometheus implementation.

Metrics are declared once in _SPECS (Prometheus requires stable label sets
per metric); incrementing an undeclared metric is a programming error and
raises. Each PrometheusMetrics owns its CollectorRegistry so tests never
fight over the global default registry.
"""

from __future__ import annotations

from typing import Literal, Protocol

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class Metrics(Protocol):
    def inc(self, name: str, value: float = 1.0, **labels: str) -> None: ...

    def observe(self, name: str, value: float, **labels: str) -> None: ...

    def set_gauge(self, name: str, value: float, **labels: str) -> None: ...


class NullMetrics:
    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        return None

    def observe(self, name: str, value: float, **labels: str) -> None:
        return None

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        return None


type _Kind = Literal["counter", "histogram", "gauge"]

_SPECS: dict[str, tuple[_Kind, str, tuple[str, ...]]] = {
    "logs_ingested_total": ("counter", "Raw log records ingested", ("source",)),
    "logs_parsed_total": ("counter", "Log events parsed and normalized", ()),
    "anomaly_clusters_total": ("counter", "Anomaly clusters detected", ("kind",)),
    "correlation_edges_created_total": ("counter", "Causal edges scored above threshold", ()),
    "investigations_total": ("counter", "Investigations finished", ("status",)),
    "investigation_duration_seconds": ("histogram", "Wall time of one investigation", ()),
    "agent_tool_calls_total": ("counter", "Tool executions", ("agent", "tool", "outcome")),
    "ai_tokens_used_total": ("counter", "LLM tokens consumed", ("direction",)),
    "active_investigations": ("gauge", "Investigations currently running", ()),
    "http_requests_total": ("counter", "API requests", ("method", "route", "status")),
}


class PrometheusMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._gauges: dict[str, Gauge] = {}
        for name, (kind, help_text, labels) in _SPECS.items():
            if kind == "counter":
                self._counters[name] = Counter(name, help_text, labels, registry=self.registry)
            elif kind == "histogram":
                self._histograms[name] = Histogram(name, help_text, labels, registry=self.registry)
            else:
                self._gauges[name] = Gauge(name, help_text, labels, registry=self.registry)

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        counter = self._counters[name]
        (counter.labels(**labels) if labels else counter).inc(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        histogram = self._histograms[name]
        (histogram.labels(**labels) if labels else histogram).observe(value)

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        gauge = self._gauges[name]
        (gauge.labels(**labels) if labels else gauge).set(value)

    def render(self) -> bytes:
        """Prometheus exposition format for the /metrics endpoint."""
        return generate_latest(self.registry)
