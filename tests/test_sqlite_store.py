import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, get_ident
from uuid import uuid4

import pytest

from app.schemas.telemetry import SensorStatus, TelemetryReading
from app.services.sqlite_store import (
    CheckpointError,
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


def test_deleting_final_record_is_detected_by_checkpoint(store):
    readings = [make_reading() for _ in range(3)]
    for reading in readings:
        store.ingest(reading)

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM telemetry_events WHERE event_id = ?",
            (str(readings[2].event_id),),
        )

    result = store.verify_chain()

    assert result["valid"] is False
    assert result["records_checked"] == 2
    assert any(
        "Checkpoint does not match" in error
        for error in result["errors"]
    )


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


def test_new_empty_store_creates_genesis_checkpoint(tmp_path):
    store = SQLiteTelemetryStore(tmp_path / "genesis.db")

    result = store.verify_chain()

    assert result["valid"] is True
    assert result["checkpoint"]["present"] is True
    assert result["checkpoint"]["valid"] is True
    assert result["checkpoint"]["sequence_id"] == 0


def test_checkpoint_updates_after_ingestion(store):
    store.ingest(make_reading())

    result = store.verify_chain()

    assert result["valid"] is True
    assert result["checkpoint"]["sequence_id"] == 1
    assert result["checkpoint"]["valid"] is True


def test_checkpoint_survives_store_recreation(tmp_path):
    database_path = tmp_path / "persistent_checkpoint.db"

    first_store = SQLiteTelemetryStore(database_path)
    first_store.ingest(make_reading())

    second_store = SQLiteTelemetryStore(database_path)
    result = second_store.verify_chain()

    assert result["valid"] is True
    assert result["checkpoint"]["present"] is True
    assert result["checkpoint"]["sequence_id"] == 1


def test_missing_checkpoint_is_not_automatically_recreated(tmp_path):
    database_path = tmp_path / "missing_checkpoint.db"
    first_store = SQLiteTelemetryStore(database_path)
    first_store.ingest(make_reading())

    first_store.checkpoint_path.unlink()

    second_store = SQLiteTelemetryStore(database_path)
    result = second_store.verify_chain()

    assert result["valid"] is False
    assert result["checkpoint"]["present"] is False
    assert any("Checkpoint is missing" in error for error in result["errors"])

    with pytest.raises(CheckpointError):
        second_store.ingest(make_reading())

    assert second_store.get_count() == 1


def test_malformed_checkpoint_is_reported(store):
    store.ingest(make_reading())
    store.checkpoint_path.write_text("{invalid json", encoding="utf-8")

    result = store.verify_chain()

    assert result["valid"] is False
    assert any("checkpoint" in error.lower() for error in result["errors"])


def test_checkpoint_mismatch_blocks_ingestion(store):
    store.ingest(make_reading())

    store.checkpoint_path.write_text(
        """
        {
            "format_version": 1,
            "sequence_id": 0,
            "record_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "updated_at": "2026-11-09T00:00:00+00:00"
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(CheckpointError):
        store.ingest(make_reading())

    assert store.get_count() == 1


def test_explicit_checkpoint_establishment_recovers_missing_checkpoint(store):
    store.ingest(make_reading())
    store.checkpoint_path.unlink()

    with pytest.raises(CheckpointError):
        store.ingest(make_reading())

    recovery = store.establish_checkpoint()

    assert recovery["established"] is True
    assert recovery["sequence_id"] == 1
    assert store.verify_chain()["valid"] is True


def test_invalid_hash_chain_cannot_be_checkpointed(store):
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

    store.checkpoint_path.unlink()

    with pytest.raises(CheckpointError, match="hash chain is invalid"):
        store.establish_checkpoint()


def test_checkpoint_write_failure_rolls_back_ingestion(
    store,
    monkeypatch,
):
    reading = make_reading()
    original_checkpoint = store.checkpoint_path.read_bytes()

    def fail_checkpoint(_chain_head):
        raise OSError("Simulated checkpoint write failure")

    monkeypatch.setattr(store, "_write_checkpoint", fail_checkpoint)

    with pytest.raises(
        CheckpointError,
        match="SQLite transaction was rolled back",
    ):
        store.ingest(reading)

    assert store.get_count() == 0
    assert store.checkpoint_path.read_bytes() == original_checkpoint
    assert store.verify_chain()["valid"] is True


def test_concurrent_ingestion_preserves_audit_integrity(store):
    readings = [make_reading() for _ in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(store.ingest, readings))

    assert all(
        result == IngestionResult.ACCEPTED
        for result in results
    )
    assert store.get_count() == 8

    integrity = store.verify_chain()

    assert integrity["valid"] is True
    assert integrity["records_checked"] == 8


def test_concurrent_ingestion_across_store_instances(tmp_path):
    database_path = tmp_path / "shared.db"
    checkpoint_path = tmp_path / "shared.audit-checkpoint.json"

    first_store = SQLiteTelemetryStore(
        database_path,
        checkpoint_path,
    )
    second_store = SQLiteTelemetryStore(
        database_path,
        checkpoint_path,
    )

    readings = [make_reading() for _ in range(8)]
    stores = [first_store, second_store] * 4

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda pair: pair[0].ingest(pair[1]),
                zip(stores, readings),
            )
        )

    assert all(
        result == IngestionResult.ACCEPTED
        for result in results
    )
    assert first_store.get_count() == 8

    integrity = second_store.verify_chain()

    assert integrity["valid"] is True
    assert integrity["records_checked"] == 8


def test_sqlite_connection_uses_full_synchronous_mode(store):
    connection = store._connect()

    try:
        synchronous = connection.execute(
            "PRAGMA synchronous"
        ).fetchone()[0]
    finally:
        connection.close()

    # SQLite reports 2 for FULL synchronous mode.
    assert synchronous == 2


def test_integrity_verification_waits_for_checkpoint_update(
    store,
    monkeypatch,
):
    reading = make_reading()

    checkpoint_started = Event()
    allow_checkpoint = Event()
    verification_lock_attempted = Event()
    verification_lock_acquired = Event()

    original_write_checkpoint = store._write_checkpoint
    original_connect = store._connect
    verification_thread_id = None

    def delayed_checkpoint(chain_head):
        checkpoint_started.set()

        if not allow_checkpoint.wait(timeout=5):
            raise TimeoutError("Test checkpoint release timed out")

        original_write_checkpoint(chain_head)

    class ConnectionProxy:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, parameters=()):
            is_verification_lock = (
                get_ident() == verification_thread_id
                and sql.strip().upper() == "BEGIN IMMEDIATE"
            )

            if is_verification_lock:
                verification_lock_attempted.set()

            result = self._connection.execute(sql, parameters)

            if is_verification_lock:
                verification_lock_acquired.set()

            return result

        def __getattr__(self, name):
            return getattr(self._connection, name)

    def tracking_connect():
        connection = original_connect()

        if get_ident() == verification_thread_id:
            return ConnectionProxy(connection)

        return connection

    monkeypatch.setattr(
        store,
        "_write_checkpoint",
        delayed_checkpoint,
    )
    monkeypatch.setattr(store, "_connect", tracking_connect)

    with ThreadPoolExecutor(max_workers=2) as executor:
        ingestion_future = executor.submit(store.ingest, reading)

        assert checkpoint_started.wait(timeout=5)

        def run_verification():
            nonlocal verification_thread_id
            verification_thread_id = get_ident()
            return store.verify_chain()

        verification_future = executor.submit(run_verification)

        # Verification has reached the SQLite lock request.
        assert verification_lock_attempted.wait(timeout=5)

        # SQLite must not grant the writer lock while ingestion is paused
        # after writing its checkpoint but before committing its transaction.
        assert verification_lock_acquired.wait(timeout=0.2) is False

        allow_checkpoint.set()

        assert ingestion_future.result(timeout=10) == (
            IngestionResult.ACCEPTED
        )

        # Once ingestion commits and releases its lock, verification proceeds.
        assert verification_lock_acquired.wait(timeout=5)

        integrity = verification_future.result(timeout=10)

    assert integrity["valid"] is True
    assert integrity["records_checked"] == 1


def test_sqlite_commit_failure_requires_explicit_checkpoint_recovery(
    store,
    monkeypatch,
):
    reading = make_reading()

    original_connect = store._connect
    fail_commit = True

    class CommitFailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def commit(self):
            nonlocal fail_commit

            if fail_commit:
                fail_commit = False
                raise sqlite3.OperationalError(
                    "Simulated SQLite commit failure"
                )

            return self.connection.commit()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        store,
        "_connect",
        lambda: CommitFailingConnection(original_connect()),
    )

    with pytest.raises(CheckpointError, match="SQLite commit failed"):
        store.ingest(reading)

    monkeypatch.setattr(store, "_connect", original_connect)

    # The database transaction rolled back, but the checkpoint was replaced.
    assert store.get_count() == 0

    integrity = store.verify_chain()
    assert integrity["valid"] is False
    assert any(
        "Checkpoint does not match" in error
        for error in integrity["errors"]
    )

    # Recovery must be explicit and must trust the verified database head.
    recovery = store.establish_checkpoint()

    assert recovery["established"] is True
    assert recovery["sequence_id"] == 0
    assert store.verify_chain()["valid"] is True
