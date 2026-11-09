import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.schemas.telemetry import SensorStatus, TelemetryReading
from app.services.sqlite_store import (
    EventConflictError,
    IngestionResult,
    SQLiteTelemetryStore,
)


def make_reading(*, event_id=None, h2s=1.0):
    return TelemetryReading(
        event_id=event_id or uuid4(),
        sensor_id="TEST-SENSOR-001",
        timestamp=datetime.now(timezone.utc),
        location={
            "site_id": "TEST-SITE",
            "zone_id": "ZONE-A",
            "latitude": 18.5204,
            "longitude": 73.8567,
        },
        sensor_status=SensorStatus.CONNECTED,
        h2s_ppm=h2s,
        methane_lel_pct=2.0,
        oxygen_pct=20.9,
    )


@pytest.fixture
def store(tmp_path):
    return SQLiteTelemetryStore(tmp_path / "test.db")


def test_new_event_is_accepted(store):
    reading = make_reading()

    result = store.ingest(reading)

    assert result == IngestionResult.ACCEPTED
    assert store.get_count() == 1


def test_identical_event_is_ignored_as_duplicate(store):
    reading = make_reading()

    first = store.ingest(reading)
    second = store.ingest(reading)

    assert first == IngestionResult.ACCEPTED
    assert second == IngestionResult.DUPLICATE
    assert store.get_count() == 1


def test_same_event_id_with_different_content_is_rejected(store):
    event_id = uuid4()

    store.ingest(make_reading(event_id=event_id, h2s=1.0))

    with pytest.raises(EventConflictError):
        store.ingest(make_reading(event_id=event_id, h2s=7.0))

    assert store.get_count() == 1


def test_event_can_be_retrieved_by_id(store):
    reading = make_reading()
    store.ingest(reading)

    retrieved = store.get_by_id(reading.event_id)

    assert retrieved == reading


def test_event_survives_store_recreation(tmp_path):
    database_path = tmp_path / "persistent.db"
    reading = make_reading()

    first_store = SQLiteTelemetryStore(database_path)
    first_store.ingest(reading)

    second_store = SQLiteTelemetryStore(database_path)

    assert second_store.get_count() == 1
    assert second_store.get_by_id(reading.event_id) == reading


def test_hash_chain_verifies_multiple_records(store):
    store.ingest(make_reading())
    store.ingest(make_reading())
    store.ingest(make_reading())

    result = store.verify_chain()

    assert result["valid"] is True
    assert result["records_checked"] == 3
    assert result["errors"] == []


def test_payload_tampering_is_detected(store):
    reading = make_reading()
    store.ingest(reading)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            UPDATE telemetry_events
            SET payload_json = ?
            WHERE event_id = ?
            """,
            ('{"tampered":true}', str(reading.event_id)),
        )

    result = store.verify_chain()

    assert result["valid"] is False
    assert any("Record hash mismatch" in error for error in result["errors"])


def test_in_memory_database_is_rejected():
    with pytest.raises(ValueError, match="In-memory SQLite databases are unsupported"):
        SQLiteTelemetryStore(":memory:")


def test_get_all_with_limit_returns_latest_records_in_chronological_order(store):
    readings = [make_reading() for _ in range(4)]
    for reading in readings:
        store.ingest(reading)

    result = store.get_all(limit=2)

    assert [item.event_id for item in result] == [
        readings[2].event_id,
        readings[3].event_id,
    ]


def test_get_all_without_limit_returns_all_records(store):
    readings = [make_reading() for _ in range(3)]
    for reading in readings:
        store.ingest(reading)

    assert store.get_all() == readings


def test_get_all_rejects_limit_below_one(store):
    with pytest.raises(ValueError, match="limit must be at least 1"):
        store.get_all(limit=0)


def test_deleting_middle_record_is_detected(store):
    readings = [make_reading() for _ in range(3)]
    for reading in readings:
        store.ingest(reading)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM telemetry_events WHERE event_id = ?",
            (str(readings[1].event_id),),
        )

    result = store.verify_chain()

    assert result["valid"] is False
    assert result["records_checked"] == 2
    assert any("Sequence discontinuity" in error for error in result["errors"])
    assert any("Previous-hash mismatch" in error for error in result["errors"])


def test_deleting_final_record_is_not_detected_without_checkpoint(store):
    readings = [make_reading() for _ in range(3)]
    for reading in readings:
        store.ingest(reading)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM telemetry_events WHERE event_id = ?",
            (str(readings[2].event_id),),
        )

    result = store.verify_chain()

    assert result["valid"] is True
    assert result["records_checked"] == 2


def test_empty_audit_chain_is_valid(store):
    result = store.verify_chain()

    assert result["valid"] is True
    assert result["records_checked"] == 0
    assert result["errors"] == []


def test_received_at_tampering_is_detected(store):
    reading = make_reading()
    store.ingest(reading)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE telemetry_events SET received_at = ? WHERE event_id = ?",
            ("2099-01-01T00:00:00+00:00", str(reading.event_id)),
        )

    result = store.verify_chain()

    assert result["valid"] is False
    assert any("Record hash mismatch" in error for error in result["errors"])
