
from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SensorStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class SensorLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1, max_length=100)
    zone_id: str = Field(min_length=1, max_length=100)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class TelemetryReading(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    event_id: UUID

    sensor_id: str = Field(
        min_length=1,
        max_length=100,
    )

    timestamp: datetime

    location: SensorLocation

    sensor_status: SensorStatus

    h2s_ppm: float | None = Field(
        default=None,
        ge=0,
    )

    methane_lel_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    oxygen_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "Timestamp must include timezone information"
            )

        return value

    @model_validator(mode="after")
    def validate_sensor_measurements(self):

        measurements = [
            self.h2s_ppm,
            self.methane_lel_pct,
            self.oxygen_pct,
        ]

        if self.sensor_status == SensorStatus.CONNECTED:
            if any(value is None for value in measurements):
                raise ValueError(
                    "Connected sensors must provide all gas measurements"
                )

        if self.sensor_status == SensorStatus.DISCONNECTED:
            if any(value is not None for value in measurements):
                raise ValueError(
                    "Disconnected sensors cannot report gas measurements"
                )

        return self
