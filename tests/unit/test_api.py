"""API tests against the injected AnalysisService seam and a real bus.

Read endpoints (incidents/graph/investigation/sources) are thin repository
pass-throughs; they are exercised by the CI integration flow test against a
real database rather than mocked here.
"""

import asyncio
import json
from uuid import UUID

from fastapi.testclient import TestClient

from aegis.api.app import create_app
from aegis.api.bus import InProcessEventBus, TopicPublisher
from aegis.api.schemas import AnalyzeRequest
from aegis.investigation.progress import ProgressEvent, ProgressKind

INCIDENT_ID = UUID("11111111-1111-1111-1111-111111111111")
INVESTIGATION_ID = UUID("22222222-2222-2222-2222-222222222222")


class StubService:
    """Records requests and replays a two-event progress stream."""

    def __init__(self, bus: InProcessEventBus) -> None:
        self._bus = bus
        self.requests: list[AnalyzeRequest] = []

    async def start_analysis(self, request: AnalyzeRequest) -> tuple[UUID, UUID]:
        self.requests.append(request)
        asyncio.get_running_loop().create_task(self._emit())
        return INCIDENT_ID, INVESTIGATION_ID

    async def _emit(self) -> None:
        publisher = TopicPublisher(self._bus, INCIDENT_ID)
        await asyncio.sleep(0.05)  # let the websocket handler subscribe
        await publisher.publish(
            ProgressEvent(
                investigation_id=INVESTIGATION_ID,
                kind=ProgressKind.INVESTIGATION_STARTED,
                message="investigation started",
                progress=0.0,
            )
        )
        await publisher.publish(
            ProgressEvent(
                investigation_id=INVESTIGATION_ID,
                kind=ProgressKind.INVESTIGATION_COMPLETED,
                message="root cause: session leak",
                progress=1.0,
            )
        )


def make_client() -> tuple[TestClient, StubService]:
    bus = InProcessEventBus()
    stub = StubService(bus)
    app = create_app(analysis_service=stub, bus=bus)
    return TestClient(app), stub


def test_analyze_returns_202_with_ids_and_ws_path() -> None:
    client, stub = make_client()
    with client:
        response = client.post("/api/v1/incidents/analyze", json={"seed": 42})

    assert response.status_code == 202
    body = response.json()
    assert body["incident_id"] == str(INCIDENT_ID)
    assert body["investigation_id"] == str(INVESTIGATION_ID)
    assert body["websocket"] == f"/ws/incidents/{INCIDENT_ID}"
    assert stub.requests[0].seed == 42


def test_analyze_rejects_unknown_fields() -> None:
    client, _ = make_client()
    with client:
        response = client.post("/api/v1/incidents/analyze", json={"nonsense": True})

    assert response.status_code == 422


def test_websocket_streams_typed_events_until_terminal() -> None:
    client, _ = make_client()
    with client, client.websocket_connect(f"/ws/incidents/{INCIDENT_ID}") as websocket:
        client.post("/api/v1/incidents/analyze", json={})

        first = json.loads(websocket.receive_text())
        second = json.loads(websocket.receive_text())

    assert first["type"] == "investigation.started"
    assert first["progress"] == 0.0
    assert second["type"] == "investigation.completed"
    assert second["progress"] == 1.0
    assert second["investigation_id"] == str(INVESTIGATION_ID)
    assert "session leak" in second["message"]


def test_read_endpoints_report_503_without_configured_repositories() -> None:
    client, _ = make_client()  # stub mode wires no database repositories
    with client:
        response = client.get("/api/v1/incidents")

    assert response.status_code == 503


def test_healthz() -> None:
    client, _ = make_client()
    with client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_request_id_is_issued_or_echoed_and_requests_are_counted() -> None:
    client, _ = make_client()
    with client:
        fresh = client.get("/healthz")
        echoed = client.get("/healthz", headers={"x-request-id": "req-abc"})
        metrics = client.get("/metrics").text

    assert fresh.headers["x-request-id"]
    assert echoed.headers["x-request-id"] == "req-abc"
    assert 'http_requests_total{method="GET",route="/healthz",status="200"}' in metrics
