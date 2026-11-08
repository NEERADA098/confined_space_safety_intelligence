
from enum import Enum
from uuid import UUID

from app.schemas.telemetry import TelemetryReading


class IngestionResult(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class EventConflictError(Exception):
    pass


class InMemoryTelemetryStore:

    def __init__(self):
        self._events: dict[UUID, TelemetryReading] = {}

    def ingest(
        self,
        reading: TelemetryReading,
    ) -> IngestionResult:

        existing = self._events.get(reading.event_id)

        if existing is not None:

            if existing == reading:
                return IngestionResult.DUPLICATE

            raise EventConflictError(
                "Event ID already exists with different content"
            )

        self._events[reading.event_id] = reading

        return IngestionResult.ACCEPTED

    def get_all(self) -> list[TelemetryReading]:

        return list(self._events.values())

    def get_count(self) -> int:

        return len(self._events)


telemetry_store = InMemoryTelemetryStore()
