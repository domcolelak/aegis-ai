"""Canned demo scripts: the offline investigation of the synthetic incident.

Used when ``AEGIS_LLM_PROVIDER=scripted`` (the default) so the entire system
-- API, WebSocket stream, persistence -- runs end to end without any API key.
The content matches what the deterministic pipeline actually finds in the
synthetic incident, so the demo is representative, not fake theater: swap in
the Anthropic provider and only this module stops being used.
"""

from __future__ import annotations

from aegis.investigation.providers.base import Completion
from aegis.investigation.providers.scripted import json_completion


def demo_scripts() -> dict[str, list[Completion]]:
    return {
        "log_analyst": [
            json_completion(
                {
                    "summary": (
                        "Stripe timeout signatures appear first, immediately followed by "
                        "session-leak exceptions and QueuePool exhaustion in booking-api."
                    ),
                    "hypotheses": [
                        {
                            "statement": (
                                "The timeout handling path in booking-api leaks database "
                                "sessions, which exhausts the pool under load."
                            ),
                            "confidence": 0.85,
                            "supporting_evidence": [
                                "new-signature clusters: stripe timeout, QueuePool limit",
                                "error ratio in booking-api jumps only after the timeouts",
                            ],
                        }
                    ],
                    "open_questions": ["was the pool size marginal even before the spike?"],
                }
            )
        ],
        "database_investigator": [
            json_completion(
                {
                    "summary": (
                        "Pool pressure starts in booking-api and propagates to postgres "
                        "FATAL connection-slot errors; the pattern matches a leak, not "
                        "organic load."
                    ),
                    "hypotheses": [
                        {
                            "statement": (
                                "Sessions opened in create_booking are not closed when the "
                                "external payment call raises, so each timeout permanently "
                                "consumes a connection."
                            ),
                            "confidence": 0.88,
                            "supporting_evidence": [
                                "pool exhaustion persists after traffic normalizes",
                                "postgres runs out of slots ~15s after the first timeout",
                            ],
                        }
                    ],
                }
            )
        ],
        "devils_advocate": [
            json_completion(
                {
                    "weaknesses": [
                        "the traffic spike alone could exhaust an undersized pool",
                        "no log line directly shows an unclosed session object",
                    ],
                    "alternative_hypotheses": [
                        {
                            "statement": "pool size 100 was simply too small for 11x traffic",
                            "confidence": 0.3,
                        }
                    ],
                    "strongest_counterargument": (
                        "correlation between timeouts and pool decay is temporal, not proven causal"
                    ),
                    "doubt": 0.3,
                }
            )
        ],
        "incident_commander": [
            json_completion(
                {
                    "root_cause": (
                        "Database sessions leak in booking-api's payment-timeout path; "
                        "under Stripe latency and elevated traffic the connection pool "
                        "and PostgreSQL slots exhaust, cascading into a retry storm and "
                        "user-visible outage."
                    ),
                    "confidence": 0.82,
                    "probable_trigger": (
                        "Stripe latency during an ~11x traffic spike on POST /api/bookings"
                    ),
                    "failure_chain": [
                        {
                            "service": "payments",
                            "description": "stripe requests slow down and time out",
                        },
                        {
                            "service": "booking-api",
                            "description": "timeout path leaks sessions; QueuePool exhausts",
                        },
                        {"service": "postgres", "description": "connection slots run out (FATAL)"},
                        {
                            "service": "worker-1",
                            "description": "retry storm amplifies the failure",
                        },
                        {
                            "service": "nginx",
                            "description": "upstream timeouts: user-visible outage",
                        },
                    ],
                    "supporting_evidence": [
                        "QueuePool exhaustion follows each stripe timeout",
                        "postgres slot exhaustion persists after the spike ends",
                    ],
                    "contradicting_evidence": [
                        "pool sizing was marginal for peak traffic even without a leak"
                    ],
                    "affected_services": ["booking-api", "postgres", "worker-1", "nginx"],
                    "recommended_actions": [
                        "wrap booking-api database sessions in an async context manager "
                        "so error paths release connections",
                        "add pool saturation alerting before exhaustion",
                    ],
                }
            )
        ],
    }
