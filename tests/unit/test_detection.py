from datetime import UTC, datetime, timedelta
from uuid import uuid4

from aegis.detection import (
    AnomalyKind,
    ErrorRatioConfig,
    ErrorRatioDetector,
    FrequencyConfig,
    FrequencySpikeDetector,
    NewSignatureConfig,
    NewSignatureDetector,
    RetryStormConfig,
    RetryStormDetector,
    default_engine,
)
from aegis.detection._stats import EwmaStats
from aegis.events import EventKind, LogEvent, Severity
from aegis.parsing import signature_of

BASE = datetime(2026, 7, 6, 14, 30, 0, tzinfo=UTC)
WINDOW = timedelta(seconds=10)


def make_event(
    message: str,
    *,
    service: str = "booking-api",
    ts: datetime = BASE,
    severity: Severity = Severity.INFO,
    kind: EventKind = EventKind.GENERIC,
) -> LogEvent:
    return LogEvent(
        event_id=uuid4(),
        timestamp=ts,
        service=service,
        source_id="test.log",
        severity=severity,
        kind=kind,
        message=message,
        signature=signature_of(message),
    )


def in_window(n: int, offset_s: float = 0.5) -> datetime:
    return BASE + n * WINDOW + timedelta(seconds=offset_s)


class TestEwmaStats:
    def test_tracks_mean_and_reacts_to_change(self) -> None:
        stats = EwmaStats(alpha=0.5)
        for value in (10.0, 10.0, 10.0):
            stats.update(value)

        assert stats.mean == 10.0
        assert stats.z_score(10.0) == 0.0
        assert stats.z_score(100.0) > 3.0

    def test_zero_variance_uses_poisson_floor_not_infinity(self) -> None:
        stats = EwmaStats(alpha=0.3)
        for _ in range(10):
            stats.update(4.0)

        # std floor = sqrt(4) = 2, so 10 is z = 3, not z = infinity.
        assert 2.9 < stats.z_score(10.0) < 3.1


class TestFrequencySpike:
    def test_detects_spike_after_stable_baseline(self) -> None:
        detector = FrequencySpikeDetector(FrequencyConfig(window=WINDOW, warmup_windows=3))
        for window in range(5):
            for _ in range(5):
                detector.observe(
                    make_event("connection timeout after 3000ms", ts=in_window(window))
                )
        for _ in range(100):
            detector.observe(make_event("connection timeout after 12ms", ts=in_window(5)))

        clusters = detector.flush(now=in_window(7))

        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.kind is AnomalyKind.FREQUENCY_SPIKE
        assert cluster.service == "booking-api"
        assert cluster.event_count == 100
        assert cluster.window.start == BASE + 5 * WINDOW
        assert 0.5 <= cluster.confidence <= 0.99
        assert cluster.attributes["observed"] == 100
        assert "connection timeout" in str(cluster.attributes["template"])
        assert len(cluster.representative_events) == 10

    def test_no_detection_during_warmup(self) -> None:
        detector = FrequencySpikeDetector(FrequencyConfig(window=WINDOW, warmup_windows=3))
        for _ in range(100):
            detector.observe(make_event("connection timeout after 5ms", ts=in_window(0)))

        assert detector.flush(now=in_window(2)) == []

    def test_incomplete_window_is_not_flushed_yet(self) -> None:
        detector = FrequencySpikeDetector(FrequencyConfig(window=WINDOW))
        detector.observe(make_event("hello 1", ts=in_window(0)))

        # "now" is still inside window 0, so nothing may finalize.
        assert detector.flush(now=in_window(0, offset_s=9.0)) == []


class TestNewSignature:
    def test_new_error_template_after_learning_is_flagged_once(self) -> None:
        detector = NewSignatureDetector(NewSignatureConfig(learning_events=3))
        for i in range(3):
            detector.observe(make_event(f"normal operation {i}", ts=in_window(0)))

        for _ in range(3):
            detector.observe(
                make_event(
                    "stripe timeout: TimeoutError after 30s",
                    ts=in_window(1),
                    severity=Severity.ERROR,
                )
            )
        detector.observe(make_event("new but harmless info line", ts=in_window(1)))

        clusters = detector.flush(now=in_window(2))

        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.kind is AnomalyKind.NEW_SIGNATURE
        assert cluster.event_count == 3
        assert "TimeoutError" in str(cluster.attributes["template"])

        # The template is vocabulary now; a recurrence is not "new" again.
        detector.observe(
            make_event(
                "stripe timeout: TimeoutError after 99s",
                ts=in_window(3),
                severity=Severity.ERROR,
            )
        )
        assert detector.flush(now=in_window(4)) == []


class TestErrorRatio:
    def test_ratio_jump_detected_against_low_baseline(self) -> None:
        detector = ErrorRatioDetector(ErrorRatioConfig(window=WINDOW, warmup_windows=3))
        for window in range(4):
            for i in range(50):
                severity = Severity.ERROR if i == 0 else Severity.INFO
                detector.observe(
                    make_event(f"request {i} handled", ts=in_window(window), severity=severity)
                )
        for i in range(50):
            severity = Severity.ERROR if i < 25 else Severity.INFO
            detector.observe(make_event(f"request {i} handled", ts=in_window(4), severity=severity))

        clusters = detector.flush(now=in_window(6))

        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.kind is AnomalyKind.ERROR_RATIO_DEVIATION
        assert cluster.event_count == 25
        assert cluster.attributes["total"] == 50
        assert cluster.confidence > 0.9

    def test_small_windows_are_ignored(self) -> None:
        detector = ErrorRatioDetector(
            ErrorRatioConfig(window=WINDOW, warmup_windows=1, min_events=20)
        )
        detector.observe(make_event("ok", ts=in_window(0)))
        detector.flush(now=in_window(1))
        # 3 events, all errors -- but far below min_events.
        for _ in range(3):
            detector.observe(make_event("boom", ts=in_window(1), severity=Severity.ERROR))

        assert detector.flush(now=in_window(3)) == []


class TestRetryStorm:
    def test_burst_over_quiet_baseline_is_a_storm(self) -> None:
        detector = RetryStormDetector(RetryStormConfig(window=WINDOW))
        for window in range(3):
            for _ in range(2):
                detector.observe(
                    make_event(
                        "Retrying create_booking (attempt 1)",
                        ts=in_window(window),
                        kind=EventKind.TASK_RETRY,
                        service="worker",
                    )
                )
        for _ in range(40):
            detector.observe(
                make_event(
                    "Retrying create_booking (attempt 3)",
                    ts=in_window(3),
                    kind=EventKind.TASK_RETRY,
                    service="worker",
                )
            )

        clusters = detector.flush(now=in_window(5))

        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.kind is AnomalyKind.RETRY_STORM
        assert cluster.service == "worker"
        assert cluster.event_count == 40
        assert cluster.confidence > 0.7

    def test_non_retry_events_are_invisible_to_this_detector(self) -> None:
        detector = RetryStormDetector(RetryStormConfig(window=WINDOW))
        for _ in range(200):
            detector.observe(make_event("plain error", severity=Severity.ERROR))

        assert detector.flush(now=in_window(2)) == []


def test_default_engine_runs_all_detectors_and_sorts_output() -> None:
    engine = default_engine()
    for window in range(6):
        for i in range(30):
            engine.observe(make_event(f"request {i} ok", ts=in_window(window)))
    for _ in range(300):
        engine.observe(
            make_event("QueuePool limit reached", ts=in_window(6), severity=Severity.ERROR)
        )

    clusters = engine.flush(now=in_window(8))

    assert clusters, "the burst must trip at least one detector"
    starts = [cluster.window.start for cluster in clusters]
    assert starts == sorted(starts)
