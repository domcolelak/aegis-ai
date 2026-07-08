"""Observability: a system that investigates incidents must not be a black
box itself. Structured JSON logs with contextvars-propagated correlation IDs,
and a Metrics seam with a Prometheus implementation."""

from aegis.observability.logging import configure_logging
from aegis.observability.metrics import Metrics, NullMetrics, PrometheusMetrics

__all__ = ["Metrics", "NullMetrics", "PrometheusMetrics", "configure_logging"]
