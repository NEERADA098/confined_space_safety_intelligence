from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_telemetry_store
from app.main import app
from app.services.sqlite_store import SQLiteTelemetryStore


@pytest.fixture
def api_client(tmp_path):
    store = SQLiteTelemetryStore(tmp_path / "api_test.db")

    app.dependency_overrides[get_telemetry_store] = lambda: store

    try:
        with TestClient(app) as client:
            yield client, store
    finally:
        app.dependency_overrides.clear()


def make_payload(event_id=None, *, h2s=1.0):
    return {
        "event_id": str(event_id or uuid4()),
        "sensor_id": "TEST-SENSOR-001",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "location": {
            "site_id": "TEST-SITE",
            "zone_id": "ZONE-A",
            "latitude": 18.5204,
            "longitude": 73.8567,
        },
        "sensor_status": "CONNECTED",
        "h2s_ppm": h2s,
        "methane_lel_pct": 2.0,
        "oxygen_pct": 20.9,
    }


def test_health_endpoint(api_client):
    client, _ = api_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_readiness_endpoint(api_client):
    client, _ = api_client

    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "available"
    assert body["checkpoint"] == "valid"
    assert body["ready"] is True


def test_readiness_fails_when_checkpoint_is_missing(api_client):
    client, store = api_client
    store.checkpoint_path.unlink()

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "not_ready"
    assert response.json()["detail"]["checkpoint"] == "missing"


def test_readiness_fails_when_checkpoint_does_not_match_database(api_client):
    client, store = api_client

    client.post("/api/v1/telemetry", json=make_payload())

    checkpoint = store._read_checkpoint()
    checkpoint["sequence_id"] = 0
    checkpoint["record_hash"] = store.GENESIS_HASH
    store.checkpoint_path.write_text(
        __import__("json").dumps(checkpoint),
        encoding="utf-8",
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["checkpoint"] == "mismatch"


def test_new_telemetry_is_accepted(api_client):
    client, _ = api_client

    response = client.post(
        "/api/v1/telemetry",
        json=make_payload(),
    )

    assert response.status_code == 202
    assert response.json()["result"] == "accepted"


def test_identical_duplicate_is_ignored(api_client):
    client, _ = api_client
    payload = make_payload()

    first = client.post("/api/v1/telemetry", json=payload)
    second = client.post("/api/v1/telemetry", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["result"] == "duplicate"


def test_conflicting_event_returns_409(api_client):
    client, _ = api_client
    event_id = uuid4()

    client.post(
        "/api/v1/telemetry",
        json=make_payload(event_id, h2s=1.0),
    )

    response = client.post(
        "/api/v1/telemetry",
        json=make_payload(event_id, h2s=7.0),
    )

    assert response.status_code == 409


def test_telemetry_can_be_retrieved(api_client):
    client, _ = api_client

    client.post(
        "/api/v1/telemetry",
        json=make_payload(),
    )

    response = client.get("/api/v1/telemetry")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert len(response.json()["readings"]) == 1


def test_normal_telemetry_receives_normal_assessment(api_client):
    client, _ = api_client
    payload = make_payload()

    client.post("/api/v1/telemetry", json=payload)

    response = client.get(
        f"/api/v1/telemetry/{payload['event_id']}/assessment"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NORMAL"


def test_audit_integrity_endpoint(api_client):
    client, _ = api_client

    client.post(
        "/api/v1/telemetry",
        json=make_payload(),
    )

    response = client.get("/api/v1/telemetry/integrity")

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["records_checked"] == 1
    assert response.json()["errors"] == []


def test_unknown_event_assessment_returns_404(api_client):
    client, _ = api_client

    response = client.get(
        f"/api/v1/telemetry/{uuid4()}/assessment"
    )

    assert response.status_code == 404


def test_telemetry_survives_store_recreation(api_client):
    client, store = api_client
    payload = make_payload()

    client.post("/api/v1/telemetry", json=payload)

    recreated_store = SQLiteTelemetryStore(store.database_path)

    assert recreated_store.get_count() == 1
    assert recreated_store.get_by_id(
        UUID(payload["event_id"])
    ) is not None


def test_telemetry_pagination_returns_latest_records_and_total_count(api_client):
    client, _ = api_client
    payloads = [make_payload() for _ in range(4)]

    for payload in payloads:
        response = client.post("/api/v1/telemetry", json=payload)
        assert response.status_code == 202

    response = client.get("/api/v1/telemetry?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 4
    assert len(body["readings"]) == 2
    assert [item["event_id"] for item in body["readings"]] == [
        payloads[2]["event_id"],
        payloads[3]["event_id"],
    ]

def test_checkpoint_failure_returns_503_without_persisting_event(
    api_client,
    monkeypatch,
):
    client, store = api_client
    payload = make_payload()

    def fail_checkpoint(_chain_head):
        raise OSError("Simulated checkpoint write failure")

    monkeypatch.setattr(store, "_write_checkpoint", fail_checkpoint)

    response = client.post(
        "/api/v1/telemetry",
        json=payload,
    )

    assert response.status_code == 503
    assert "checkpoint" in response.json()["detail"].lower()
    assert store.get_count() == 0

    # The failed ingestion must leave the original checkpoint and chain valid.
    integrity_response = client.get("/api/v1/telemetry/integrity")

    assert integrity_response.status_code == 200
    assert integrity_response.json()["valid"] is True
    assert integrity_response.json()["records_checked"] == 0
