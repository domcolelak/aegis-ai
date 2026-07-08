import pytest
import structlog

from aegis.observability import NullMetrics, PrometheusMetrics, configure_logging
from aegis.observability.logging import configure_logging as _reconfigure


class TestPrometheusMetrics:
    def test_counters_histograms_and_gauges_render(self) -> None:
        metrics = PrometheusMetrics()
        metrics.inc("logs_parsed_total", 42)
        metrics.inc("agent_tool_calls_total", agent="log_analyst", tool="search", outcome="ok")
        metrics.observe("investigation_duration_seconds", 1.5)
        metrics.set_gauge("active_investigations", 2)

        rendered = metrics.render().decode()

        assert "logs_parsed_total 42.0" in rendered
        assert 'agent_tool_calls_total{agent="log_analyst"' in rendered
        assert "investigation_duration_seconds_count 1.0" in rendered
        assert "active_investigations 2.0" in rendered

    def test_undeclared_metric_is_a_programming_error(self) -> None:
        metrics = PrometheusMetrics()

        with pytest.raises(KeyError):
            metrics.inc("made_up_metric_total")

    def test_instances_are_isolated(self) -> None:
        # Each instance owns its registry: no cross-test global state.
        first = PrometheusMetrics()
        second = PrometheusMetrics()
        first.inc("logs_parsed_total", 5)

        assert "logs_parsed_total 5.0" in first.render().decode()
        assert "logs_parsed_total 0.0" in second.render().decode()

    def test_null_metrics_accepts_everything_silently(self) -> None:
        metrics = NullMetrics()
        metrics.inc("anything")
        metrics.observe("anything", 1.0)
        metrics.set_gauge("anything", 1.0)


def test_json_logging_includes_bound_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_logs=True)
    logger = structlog.get_logger("test")

    with structlog.contextvars.bound_contextvars(investigation_id="inv-42"):
        logger.info("stage_done", events=10)

    line = capsys.readouterr().out.strip()
    assert '"investigation_id": "inv-42"' in line
    assert '"event": "stage_done"' in line
    assert '"events": 10' in line
    assert '"timestamp"' in line
    # Restore a clean configuration for other tests.
    _reconfigure()
