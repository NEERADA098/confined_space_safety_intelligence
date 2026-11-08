from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.schemas.assessment import HazardAssessment
from app.schemas.telemetry import TelemetryReading
from app.services.hazard_detection import assess_telemetry
from app.services.ingestion import (
    EventConflictError,
    IngestionResult,
    InMemoryTelemetryStore,
    telemetry_store,
)


router = APIRouter(
    prefix="/api/v1/telemetry",
    tags=["Telemetry"],
)


def get_telemetry_store() -> InMemoryTelemetryStore:
    return telemetry_store


StoreDependency = Annotated[
    InMemoryTelemetryStore,
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
