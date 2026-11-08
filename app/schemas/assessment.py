from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AssessmentStatus(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    SENSOR_FAULT = "SENSOR_FAULT"
    MONITORING_UNAVAILABLE = "MONITORING_UNAVAILABLE"


class HazardAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    sensor_id: str
    assessed_at: datetime
    status: AssessmentStatus
    reasons: list[str] = Field(default_factory=list)
    triggered_rules: list[str] = Field(default_factory=list)
    telemetry_age_seconds: float = Field(ge=0)
    evaluated_readings: dict[str, float | None]
    disclaimer: str = (
        "Prototype assessment only. Not certified safety equipment and "
        "not authorization for confined-space entry."
    )
