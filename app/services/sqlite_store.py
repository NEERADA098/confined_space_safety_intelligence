
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import UUID

from app.schemas.telemetry import TelemetryReading


class IngestionResult(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class EventConflictError(Exception):
    pass


class SQLiteTelemetryStore:
    GENESIS_HASH = "0" * 64

    def __init__(self, database_path: str | Path | None = None):
        if database_path is None:
            project_root = Path(__file__).resolve().parents[2]
            database_path = os.getenv(
                "TELEMETRY_DATABASE_PATH",
                str(project_root / "data" / "telemetry.db"),
            )

        if str(database_path) == ":memory:":
            raise ValueError(
                "In-memory SQLite databases are unsupported. "
                "Use a file-backed database because this store "
                "opens a separate connection for each operation."
            )

        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    sequence_id INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_telemetry_event_id
                ON telemetry_events(event_id)
                """
            )

    @staticmethod
    def _canonical_json(data: dict) -> str:
        return json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def _calculate_hash(cls, record: dict) -> str:
        canonical_record = cls._canonical_json(record)
        return hashlib.sha256(
            canonical_record.encode("utf-8")
        ).hexdigest()

    def ingest(self, reading: TelemetryReading) -> IngestionResult:
        payload_json = self._canonical_json(
            reading.model_dump(mode="json")
        )

        connection = self._connect()

        try:
            # Serialize concurrent writers before checking the event ID.
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT payload_json
                FROM telemetry_events
                WHERE event_id = ?
                """,
                (str(reading.event_id),),
            ).fetchone()

            if existing is not None:
                existing_reading = TelemetryReading.model_validate_json(
                    existing["payload_json"]
                )

                if existing_reading == reading:
                    connection.commit()
                    return IngestionResult.DUPLICATE

                raise EventConflictError(
                    "Event ID already exists with different content"
                )

            latest = connection.execute(
                """
                SELECT sequence_id, record_hash
                FROM telemetry_events
                ORDER BY sequence_id DESC
                LIMIT 1
                """
            ).fetchone()

            sequence_id = (
                latest["sequence_id"] + 1 if latest else 1
            )

            previous_hash = (
                latest["record_hash"]
                if latest
                else self.GENESIS_HASH
            )

            received_at = datetime.now(timezone.utc).isoformat()

            record = {
                "sequence_id": sequence_id,
                "event_id": str(reading.event_id),
                "payload_json": payload_json,
                "received_at": received_at,
                "previous_hash": previous_hash,
            }

            record_hash = self._calculate_hash(record)

            connection.execute(
                """
                INSERT INTO telemetry_events (
                    sequence_id,
                    event_id,
                    payload_json,
                    received_at,
                    previous_hash,
                    record_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence_id,
                    str(reading.event_id),
                    payload_json,
                    received_at,
                    previous_hash,
                    record_hash,
                ),
            )

            connection.commit()
            return IngestionResult.ACCEPTED

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def get_all(self) -> list[TelemetryReading]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM telemetry_events
                ORDER BY sequence_id ASC
                """
            ).fetchall()

        return [
            TelemetryReading.model_validate_json(row["payload_json"])
            for row in rows
        ]

    def get_count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM telemetry_events"
            ).fetchone()

        return row["total"]

    def get_by_id(self, event_id: UUID) -> TelemetryReading | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM telemetry_events
                WHERE event_id = ?
                """,
                (str(event_id),),
            ).fetchone()

        if row is None:
            return None

        return TelemetryReading.model_validate_json(
            row["payload_json"]
        )

    def verify_chain(self) -> dict:
        errors = []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT sequence_id, event_id, payload_json,
                       received_at, previous_hash, record_hash
                FROM telemetry_events
                ORDER BY sequence_id ASC
                """
            ).fetchall()

        expected_previous_hash = self.GENESIS_HASH

        for expected_sequence, row in enumerate(rows, start=1):
            if row["sequence_id"] != expected_sequence:
                errors.append(
                    f"Sequence discontinuity at record "
                    f"{row['sequence_id']}"
                )

            if row["previous_hash"] != expected_previous_hash:
                errors.append(
                    f"Previous-hash mismatch at record "
                    f"{row['sequence_id']}"
                )

            record = {
                "sequence_id": row["sequence_id"],
                "event_id": row["event_id"],
                "payload_json": row["payload_json"],
                "received_at": row["received_at"],
                "previous_hash": row["previous_hash"],
            }

            calculated_hash = self._calculate_hash(record)

            if calculated_hash != row["record_hash"]:
                errors.append(
                    f"Record hash mismatch at record "
                    f"{row['sequence_id']}"
                )

            expected_previous_hash = row["record_hash"]

        return {
            "valid": len(errors) == 0,
            "records_checked": len(rows),
            "errors": errors,
        }
