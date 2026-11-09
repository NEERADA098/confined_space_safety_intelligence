
import hashlib
import json
import os
import sqlite3
import tempfile
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


class CheckpointError(RuntimeError):
    pass


class SQLiteTelemetryStore:
    GENESIS_HASH = "0" * 64


    def __init__(
        self,
        database_path: str | Path | None = None,
        checkpoint_path: str | Path | None = None,
    ):
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

        if checkpoint_path is None:
            checkpoint_path = os.getenv(
                "TELEMETRY_CHECKPOINT_PATH",
                f"{self.database_path}.audit-checkpoint.json",
            )

        self.checkpoint_path = Path(checkpoint_path)

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.checkpoint_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()
        self._initialize_checkpoint()


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

    def _get_chain_head(self) -> dict:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT sequence_id, record_hash
                FROM telemetry_events
                ORDER BY sequence_id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return {
                "sequence_id": 0,
                "record_hash": self.GENESIS_HASH,
            }

        return {
            "sequence_id": row["sequence_id"],
            "record_hash": row["record_hash"],
        }

    def _write_checkpoint(self, chain_head: dict) -> None:
        checkpoint = {
            "format_version": 1,
            "sequence_id": chain_head["sequence_id"],
            "record_hash": chain_head["record_hash"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        temporary_path = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.checkpoint_path.parent,
                prefix=f".{self.checkpoint_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

                json.dump(
                    checkpoint,
                    temporary_file,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(
                temporary_path,
                self.checkpoint_path,
            )

        finally:
            if (
                temporary_path is not None
                and temporary_path.exists()
            ):
                temporary_path.unlink()

    def _read_checkpoint(self) -> dict | None:
        try:
            with self.checkpoint_path.open(
                "r",
                encoding="utf-8",
            ) as checkpoint_file:
                checkpoint = json.load(checkpoint_file)

        except FileNotFoundError:
            return None

        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Checkpoint file is unreadable or malformed"
            ) from exc

        if not isinstance(checkpoint, dict):
            raise ValueError("Checkpoint must contain a JSON object")

        if checkpoint.get("format_version") != 1:
            raise ValueError("Unsupported checkpoint format version")

        sequence_id = checkpoint.get("sequence_id")
        record_hash = checkpoint.get("record_hash")

        if type(sequence_id) is not int or sequence_id < 0:
            raise ValueError("Checkpoint sequence_id is invalid")

        if (
            not isinstance(record_hash, str)
            or len(record_hash) != 64
            or any(character not in "0123456789abcdef" for character in record_hash)
        ):
            raise ValueError("Checkpoint record_hash is invalid")

        return checkpoint

    def _initialize_checkpoint(self) -> None:
        if self.checkpoint_path.exists():
            return

        chain_head = self._get_chain_head()

        # Only a genuinely empty database can safely start at genesis.
        # A non-empty database without a checkpoint needs explicit trust
        # establishment; never silently adopt its current head.
        if chain_head["sequence_id"] == 0:
            self._write_checkpoint(chain_head)

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

            integrity = self.verify_chain()

            if not integrity["valid"]:
                raise CheckpointError(
                    "Telemetry ingestion blocked because audit integrity "
                    "verification failed: "
                    + "; ".join(integrity["errors"])
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

            # Update the checkpoint while BEGIN IMMEDIATE still holds
            # the SQLite writer lock. This prevents another writer from
            # observing a database head ahead of the checkpoint.
            try:
                self._write_checkpoint(
                    {
                        "sequence_id": sequence_id,
                        "record_hash": record_hash,
                    }
                )
            except Exception as exc:
                raise CheckpointError(
                    "Could not update the audit checkpoint. "
                    "The SQLite transaction was rolled back."
                ) from exc

            try:
                connection.commit()
            except Exception as exc:
                raise CheckpointError(
                    "The audit checkpoint was updated, but the SQLite "
                    "commit failed. Verify integrity and explicitly recover "
                    "the checkpoint if necessary."
                ) from exc

            return IngestionResult.ACCEPTED

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def get_all(self, limit: int | None = None) -> list[TelemetryReading]:
        query = (
            "SELECT payload_json FROM telemetry_events "
            "ORDER BY sequence_id DESC"
        )
        parameters = ()
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be at least 1")
            query += " LIMIT ?"
            parameters = (limit,)

        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [
            TelemetryReading.model_validate_json(row["payload_json"])
            for row in reversed(rows)
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

    def _verify_hash_chain(self) -> dict:
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

        chain_head = (
            {
                "sequence_id": rows[-1]["sequence_id"],
                "record_hash": rows[-1]["record_hash"],
            }
            if rows
            else {
                "sequence_id": 0,
                "record_hash": self.GENESIS_HASH,
            }
        )

        return {
            "valid": len(errors) == 0,
            "records_checked": len(rows),
            "errors": errors,
            "chain_head": chain_head,
        }

    def establish_checkpoint(self) -> dict:
        """Explicitly trust the current head of a verified hash chain."""
        connection = self._connect()

        try:
            # Prevent concurrent ingestion while verifying and writing
            # the replacement checkpoint.
            connection.execute("BEGIN IMMEDIATE")

            chain_result = self._verify_hash_chain()

            if not chain_result["valid"]:
                raise CheckpointError(
                    "Cannot establish checkpoint because the hash chain "
                    "is invalid: "
                    + "; ".join(chain_result["errors"])
                )

            chain_head = chain_result["chain_head"]

            try:
                self._write_checkpoint(chain_head)
            except OSError as exc:
                raise CheckpointError(
                    "Could not write the audit checkpoint"
                ) from exc

            connection.commit()

            return {
                "established": True,
                "sequence_id": chain_head["sequence_id"],
                "record_hash": chain_head["record_hash"],
            }

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def verify_chain(self) -> dict:
        chain_result = self._verify_hash_chain()
        errors = list(chain_result["errors"])
        chain_head = chain_result["chain_head"]

        checkpoint_status = {
            "present": False,
            "valid": False,
            "sequence_id": None,
            "record_hash": None,
        }

        try:
            checkpoint = self._read_checkpoint()

            if checkpoint is None:
                errors.append(
                    "Checkpoint is missing; explicit trust establishment required"
                )
            else:
                checkpoint_status = {
                    "present": True,
                    "valid": False,
                    "sequence_id": checkpoint["sequence_id"],
                    "record_hash": checkpoint["record_hash"],
                }

                if (
                    checkpoint["sequence_id"] != chain_head["sequence_id"]
                    or checkpoint["record_hash"] != chain_head["record_hash"]
                ):
                    errors.append(
                        "Checkpoint does not match the current database head"
                    )
                elif chain_result["valid"]:
                    checkpoint_status["valid"] = True

        except ValueError as exc:
            errors.append(str(exc))

        return {
            "valid": len(errors) == 0,
            "records_checked": chain_result["records_checked"],
            "errors": errors,
            "checkpoint": checkpoint_status,
        }
