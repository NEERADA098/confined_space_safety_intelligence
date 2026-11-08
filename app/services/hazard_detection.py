from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.schemas.assessment import AssessmentStatus, HazardAssessment
from app.schemas.telemetry import SensorStatus, TelemetryReading


@dataclass(frozen=True)
class HazardConfig:
    """
    Project policy values, kept in one place so they can be reviewed
    and changed without editing the decision logic.
    """
    stale_after_seconds: int = 60
    future_timestamp_tolerance_seconds: int = 5

    # Project early-warning policy; not a legal exposure limit.
    h2s_warning_ppm: float = 5.0

    # Conservative instantaneous tripwire based on the NIOSH 10 ppm
    # 10-minute ceiling. One instantaneous sample does NOT establish
    # a 10-minute exposure average.
    h2s_critical_ppm: float = 10.0

    # OSHA confined-space hazardous-atmosphere criterion: >10% LFL.
    methane_critical_pct: float = 10.0

    # OSHA oxygen-deficient/enriched boundaries.
    oxygen_low_pct: float = 19.5
    oxygen_high_pct: float = 23.5


DEFAULT_CONFIG = HazardConfig()


def assess_telemetry(
    reading: TelemetryReading,
    *,
    now: datetime | None = None,
    config: HazardConfig = DEFAULT_CONFIG,
) -> HazardAssessment:
    """
    Assess one telemetry event.

    Data quality is checked before gas values are interpreted. A stale,
    future-dated, or disconnected sensor cannot produce NORMAL.
    """
    assessed_at = now or datetime.now(timezone.utc)

    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("Assessment time must be timezone-aware")

    timestamp = reading.timestamp
    age_seconds = (assessed_at - timestamp).total_seconds()

    # Keep age non-negative in the response for future-dated samples.
    response_age = max(0.0, age_seconds)

    readings = {
        "h2s_ppm": reading.h2s_ppm,
        "methane_lel_pct": reading.methane_lel_pct,
        "oxygen_pct": reading.oxygen_pct,
    }

    # A disconnected sensor is a valid telemetry event, but it cannot
    # provide a current atmospheric assessment.
    if reading.sensor_status == SensorStatus.DISCONNECTED:
        return HazardAssessment(
            event_id=reading.event_id,
            sensor_id=reading.sensor_id,
            assessed_at=assessed_at,
            status=AssessmentStatus.SENSOR_FAULT,
            reasons=["Sensor reports DISCONNECTED; no current gas measurements are available."],
            triggered_rules=["SENSOR_DISCONNECTED"],
            telemetry_age_seconds=response_age,
            evaluated_readings=readings,
        )

    # Future timestamps beyond tolerance are treated as unavailable.
    if age_seconds < -config.future_timestamp_tolerance_seconds:
        return HazardAssessment(
            event_id=reading.event_id,
            sensor_id=reading.sensor_id,
            assessed_at=assessed_at,
            status=AssessmentStatus.MONITORING_UNAVAILABLE,
            reasons=["Telemetry timestamp is too far in the future; freshness cannot be established."],
            triggered_rules=["FUTURE_TIMESTAMP"],
            telemetry_age_seconds=response_age,
            evaluated_readings=readings,
        )

    if age_seconds > config.stale_after_seconds:
        return HazardAssessment(
            event_id=reading.event_id,
            sensor_id=reading.sensor_id,
            assessed_at=assessed_at,
            status=AssessmentStatus.MONITORING_UNAVAILABLE,
            reasons=[
                f"Telemetry is stale: age {age_seconds:.1f}s exceeds "
                f"the configured {config.stale_after_seconds}s limit."
            ],
            triggered_rules=["STALE_TELEMETRY"],
            telemetry_age_seconds=response_age,
            evaluated_readings=readings,
        )

    # Defensive check. Phase 1's Pydantic model normally prevents this
    # for CONNECTED readings, but the assessment layer must still fail closed.
    if any(value is None for value in readings.values()):
        missing = [name for name, value in readings.items() if value is None]
        return HazardAssessment(
            event_id=reading.event_id,
            sensor_id=reading.sensor_id,
            assessed_at=assessed_at,
            status=AssessmentStatus.MONITORING_UNAVAILABLE,
            reasons=[f"Required measurements are missing: {', '.join(missing)}."],
            triggered_rules=["MISSING_MEASUREMENTS"],
            telemetry_age_seconds=response_age,
            evaluated_readings=readings,
        )

    reasons: list[str] = []
    rules: list[str] = []
    critical = False
    warning = False

    h2s = reading.h2s_ppm
    methane = reading.methane_lel_pct
    oxygen = reading.oxygen_pct

    if h2s >= config.h2s_critical_ppm:
        critical = True
        rules.append("H2S_INSTANTANEOUS_CRITICAL")
        reasons.append(
            f"H2S is {h2s:.2f} ppm, at or above the configured "
            f"{config.h2s_critical_ppm:.2f} ppm instantaneous tripwire."
        )
    elif h2s >= config.h2s_warning_ppm:
        warning = True
        rules.append("H2S_WARNING")
        reasons.append(
            f"H2S is {h2s:.2f} ppm, at or above the project "
            f"warning level of {config.h2s_warning_ppm:.2f} ppm."
        )

    if methane > config.methane_critical_pct:
        critical = True
        rules.append("METHANE_ABOVE_10_PERCENT_LFL")
        reasons.append(
            f"Methane is {methane:.2f}% LEL, above the configured "
            f"{config.methane_critical_pct:.2f}% LEL threshold."
        )

    if oxygen < config.oxygen_low_pct:
        critical = True
        rules.append("OXYGEN_DEFICIENT")
        reasons.append(
            f"Oxygen is {oxygen:.2f}%, below {config.oxygen_low_pct:.2f}%."
        )
    elif oxygen > config.oxygen_high_pct:
        critical = True
        rules.append("OXYGEN_ENRICHED")
        reasons.append(
            f"Oxygen is {oxygen:.2f}%, above {config.oxygen_high_pct:.2f}%."
        )

    if critical:
        status = AssessmentStatus.CRITICAL
    elif warning:
        status = AssessmentStatus.WARNING
    else:
        status = AssessmentStatus.NORMAL
        reasons.append("No configured instantaneous hazard threshold was crossed.")

    return HazardAssessment(
        event_id=reading.event_id,
        sensor_id=reading.sensor_id,
        assessed_at=assessed_at,
        status=status,
        reasons=reasons,
        triggered_rules=rules,
        telemetry_age_seconds=response_age,
        evaluated_readings=readings,
    )
