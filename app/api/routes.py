from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.schemas.assessment import HazardAssessment
from app.schemas.telemetry import TelemetryReading
from app.services.hazard_detection import assess_telemetry
from app.services.sqlite_store import (
    EventConflictError,
    IngestionResult,
    SQLiteTelemetryStore,
)


router = APIRouter(
    prefix="/api/v1/telemetry",
    tags=["Telemetry"],
)


# One shared store instance, backed by the persistent SQLite database.
telemetry_store = SQLiteTelemetryStore()


def get_telemetry_store() -> SQLiteTelemetryStore:
    return telemetry_store


StoreDependency = Annotated[
    SQLiteTelemetryStore,
    Depends(get_telemetry_store),
]


class IngestionResponse(BaseModel):
    event_id: str
    result: str
    message: str


@router.post(
    "",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_telemetry(
    reading: TelemetryReading,
    store: StoreDependency,
):
    try:
        result = store.ingest(reading)
    except EventConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return IngestionResponse(
        event_id=str(reading.event_id),
        result=result.value,
        message=(
            "Telemetry accepted"
            if result == IngestionResult.ACCEPTED
            else "Duplicate event ignored"
        ),
    )


@router.get("")
def get_telemetry(
    store: StoreDependency,
    limit: int = Query(default=100, ge=1, le=1000),
):
    readings = store.get_all()

    return {
        "count": len(readings),
        "readings": readings[-limit:],
    }


@router.get(
    "/integrity",
    tags=["Audit Integrity"],
)
def get_audit_integrity(
    store: StoreDependency,
):
    return store.verify_chain()


@router.get(
    "/{event_id}/assessment",
    response_model=HazardAssessment,
    tags=["Hazard Assessment"],
)
def get_telemetry_assessment(
    event_id: UUID,
    store: StoreDependency,
):
    reading = store.get_by_id(event_id)

    if reading is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telemetry event not found",
        )

    return assess_telemetry(reading)
