"""Statistical anomaly detection over the normalized event stream.

Deliberately deterministic (EWMA baselines, ratio rules) rather than learned:
the detectors' job is to reduce millions of events to a handful of anomaly
clusters with explainable evidence attached, which is what the AI
investigators are later allowed to reason about. Every cluster carries the
numbers that triggered it.
"""

from aegis.detection.detector import AnomalyDetector, DetectionEngine
from aegis.detection.error_ratio import ErrorRatioConfig, ErrorRatioDetector
from aegis.detection.frequency import FrequencyConfig, FrequencySpikeDetector
from aegis.detection.models import AnomalyCluster, AnomalyKind
from aegis.detection.new_signature import NewSignatureConfig, NewSignatureDetector
from aegis.detection.retry_storm import RetryStormConfig, RetryStormDetector

__all__ = [
    "AnomalyCluster",
    "AnomalyDetector",
    "AnomalyKind",
    "DetectionEngine",
    "ErrorRatioConfig",
    "ErrorRatioDetector",
    "FrequencyConfig",
    "FrequencySpikeDetector",
    "NewSignatureConfig",
    "NewSignatureDetector",
    "RetryStormConfig",
    "RetryStormDetector",
]


def default_engine() -> DetectionEngine:
    """The standard detector set with default thresholds."""
    return DetectionEngine(
        [
            FrequencySpikeDetector(),
            NewSignatureDetector(),
            ErrorRatioDetector(),
            RetryStormDetector(),
        ]
    )
