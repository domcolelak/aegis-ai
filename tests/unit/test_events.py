from datetime import UTC, datetime, timedelta

import pytest

from aegis.events import EventSignature, Severity, TimeWindow


def test_severity_orders_like_syslog() -> None:
    assert Severity.DEBUG < Severity.INFO < Severity.WARNING < Severity.ERROR < Severity.CRITICAL


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("warn", Severity.WARNING),
        ("WARNING", Severity.WARNING),
        ("err", Severity.ERROR),
        ("fatal", Severity.CRITICAL),
        (" info ", Severity.INFO),
        ("trace", Severity.DEBUG),
    ],
)
def test_severity_from_text_aliases(text: str, expected: Severity) -> None:
    assert Severity.from_text(text) is expected


def test_severity_unknown_uses_default_or_raises() -> None:
    assert Severity.from_text("weird", default=Severity.INFO) is Severity.INFO

    with pytest.raises(ValueError, match="unknown severity"):
        Severity.from_text("weird")


def test_signature_fingerprint_is_stable_and_distinct() -> None:
    a = EventSignature.from_template("connection timeout after <NUM>ms")
    b = EventSignature.from_template("connection timeout after <NUM>ms")
    c = EventSignature.from_template("connection refused")

    assert a == b
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint
    assert len(a.fingerprint) == 16


def test_time_window_duration_and_containment() -> None:
    t0 = datetime(2026, 7, 6, 14, 31, tzinfo=UTC)
    window = TimeWindow(start=t0, end=t0 + timedelta(seconds=30))

    assert window.duration == timedelta(seconds=30)
    assert window.contains(t0)
    assert window.contains(t0 + timedelta(seconds=30))
    assert not window.contains(t0 + timedelta(seconds=31))


def test_time_window_overlap_is_symmetric() -> None:
    t0 = datetime(2026, 7, 6, 14, 31, tzinfo=UTC)
    window = TimeWindow(start=t0, end=t0 + timedelta(minutes=1))
    overlapping = TimeWindow(start=t0 + timedelta(seconds=20), end=t0 + timedelta(minutes=2))
    disjoint = TimeWindow(start=t0 + timedelta(minutes=5), end=t0 + timedelta(minutes=6))

    assert window.overlaps(overlapping)
    assert overlapping.overlaps(window)
    assert not window.overlaps(disjoint)
    assert not disjoint.overlaps(window)


def test_time_window_validation() -> None:
    t0 = datetime(2026, 7, 6, 14, 31, tzinfo=UTC)

    with pytest.raises(ValueError, match="precedes"):
        TimeWindow(start=t0, end=t0 - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeWindow(start=t0.replace(tzinfo=None), end=t0)
