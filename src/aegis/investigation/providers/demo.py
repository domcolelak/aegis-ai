"""Canned demo scripts: the offline investigation of the synthetic incident.

Used when ``AEGIS_LLM_PROVIDER=scripted`` (the default) so the entire system
-- API, WebSocket stream, persistence -- runs end to end without any API key.
The content matches what the deterministic pipeline actually finds in the
synthetic incident, so the demo is representative, not fake theater: swap in
the Anthropic provider and only this module stops being used.
"""

from __future__ import annotations

from aegis.investigation.providers.base import Completion
from aegis.investigation.providers.scripted import json_completion, tool_call_completion

_DEMO_DIFF = """\
--- a/app/services/booking_service.py
+++ b/app/services/booking_service.py
@@ -5,12 +5,8 @@


 async def create_booking(request):
-    session = SessionLocal()
-    booking = await _insert_booking(session, request)
-    # BUG: when this call raises (e.g. TimeoutError under Stripe latency),
-    # the function unwinds without ever closing the session -- each timeout
-    # permanently consumes one connection from the pool.
-    await stripe_client.create_payment(booking.total, booking.reference)
-    await session.commit()
-    await session.close()
-    return booking
+    async with SessionLocal() as session:
+        booking = await _insert_booking(session, request)
+        await stripe_client.create_payment(booking.total, booking.reference)
+        await session.commit()
+        return booking
"""


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
        "code_investigator": [
            tool_call_completion("search_source", {"query": "SessionLocal()"}),
            tool_call_completion(
                "read_source", {"path": "app/services/booking_service.py"}, call_id="call-2"
            ),
            json_completion(
                {
                    "summary": (
                        "create_booking opens a session directly and only closes it on "
                        "the happy path; the stripe call sits between open and close."
                    ),
                    "hypotheses": [
                        {
                            "statement": (
                                "app/services/booking_service.py:8 acquires SessionLocal() "
                                "without a context manager; when create_payment raises at "
                                "line 13 the session is never closed."
                            ),
                            "confidence": 0.9,
                            "supporting_evidence": [
                                "session lifecycle spans the external call with no try/finally",
                                "pool exhaustion in the logs matches one leaked connection "
                                "per stripe timeout",
                            ],
                        }
                    ],
                }
            ),
        ],
        "patch_engineer": [
            tool_call_completion("read_source", {"path": "app/services/booking_service.py"}),
            json_completion(
                {
                    "reasoning": (
                        "Wrap the session in an async context manager so every exit "
                        "path -- including the Stripe timeout -- releases the "
                        "connection back to the pool."
                    ),
                    "affected_files": ["app/services/booking_service.py"],
                    "diff": _DEMO_DIFF,
                    "confidence": 0.85,
                    "risks": [
                        "commit now happens inside the context manager scope; verify "
                        "no caller relied on reading the booking after an implicit "
                        "rollback"
                    ],
                }
            ),
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
