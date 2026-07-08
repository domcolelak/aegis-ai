"""Full API flow against real PostgreSQL: analyze -> poll -> read results.

Runs the entire vertical slice through the HTTP surface with the scripted
LLM provider: ingestion, process-pool parsing, detection, correlation,
graph, investigation, persistence, and incident memory.
"""

import os
import time

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient

from aegis.api.app import create_app
from aegis.core.config import Settings

DATABASE_URL = os.environ.get("AEGIS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="AEGIS_TEST_DATABASE_URL not set"),
]


@pytest.fixture(scope="session")
def api_database() -> str:
    assert DATABASE_URL is not None
    os.environ["AEGIS_DATABASE_URL"] = DATABASE_URL
    command.upgrade(AlembicConfig("alembic.ini"), "head")
    return DATABASE_URL


def test_analyze_flow_end_to_end(api_database: str) -> None:
    settings = Settings(
        database_url=api_database,
        llm_provider="scripted",
        embedding_provider="hashing",
        parser_workers=2,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        accepted = client.post("/api/v1/incidents/analyze", json={"synthetic": True})
        assert accepted.status_code == 202
        incident_id = accepted.json()["incident_id"]

        deadline = time.monotonic() + 90
        status = "analyzing"
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/incidents/{incident_id}").json()["status"]
            if status in {"completed", "failed"}:
                break
            time.sleep(0.5)
        assert status == "completed"

        investigation = client.get(f"/api/v1/incidents/{incident_id}/investigation").json()
        assert investigation["assessment"] is not None
        assert "leak" in investigation["assessment"]["root_cause"]
        assert {finding["agent"] for finding in investigation["findings"]} == {
            "log_analyst",
            "database_investigator",
        }

        graph = client.get(f"/api/v1/incidents/{incident_id}/graph").json()
        assert len(graph["edges"]) > 50

        incidents = client.get("/api/v1/incidents").json()
        assert incident_id in {item["incident_id"] for item in incidents}

        created = client.post(
            "/api/v1/sources", json={"source_id": "booking-api.log", "log_format": "plain"}
        )
        assert created.status_code == 201
        sources = client.get("/api/v1/sources").json()
        assert "booking-api.log" in {source["source_id"] for source in sources}
