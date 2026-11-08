from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.schemas.assessment import AssessmentStatus
from app.schemas.telemetry import SensorStatus, TelemetryReading
from app.services.hazard_detection import assess_telemetry


NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def make_reading(
    *,
    h2s=1.0,
    methane=1.0,
    oxygen=20.9,
    timestamp=None,
    sensor_status=SensorStatus.CONNECTED,
):
    disconnected = sensor_status == SensorStatus.DISCONNECTED
    return TelemetryReading(
        event_id=uuid4(),
        sensor_id="TEST-SENSOR-001",
        timestamp=timestamp or NOW,
        location={
            "site_id": "TEST_SITE",
            "zone_id": "TEST_ZONE",
            "latitude": 18.5204,
            "longitude": 73.8567,
        },
        sensor_status=sensor_status,
        h2s_ppm=None if disconnected else h2s,
        methane_lel_pct=None if disconnected else methane,
        oxygen_pct=None if disconnected else oxygen,
    )


def test_normal_reading():
    result = assess_telemetry(make_reading(), now=NOW)
    assert result.status == AssessmentStatus.NORMAL
    assert result.triggered_rules == []


def test_h2s_warning():
    result = assess_telemetry(make_reading(h2s=5.0), now=NOW)
    assert result.status == AssessmentStatus.WARNING
    assert "H2S_WARNING" in result.triggered_rules


def test_h2s_critical_tripwire():
    result = assess_telemetry(make_reading(h2s=10.0), now=NOW)
    assert result.status == AssessmentStatus.CRITICAL
    assert "H2S_INSTANTANEOUS_CRITICAL" in result.triggered_rules


def test_methane_above_10_percent_lel_is_critical():
    result = assess_telemetry(make_reading(methane=10.1), now=NOW)
    assert result.status == AssessmentStatus.CRITICAL
    assert "METHANE_ABOVE_10_PERCENT_LFL" in result.triggered_rules


@pytest.mark.parametrize("oxygen", [19.4, 23.6])
def test_oxygen_outside_configured_range_is_critical(oxygen):
    result = assess_telemetry(make_reading(oxygen=oxygen), now=NOW)
    assert result.status == AssessmentStatus.CRITICAL


def test_exact_oxygen_boundaries_are_not_outside_range():
    low = assess_telemetry(make_reading(oxygen=19.5), now=NOW)
    high = assess_telemetry(make_reading(oxygen=23.5), now=NOW)
    assert low.status == AssessmentStatus.NORMAL
    assert high.status == AssessmentStatus.NORMAL


def test_disconnected_sensor_is_sensor_fault():
    reading = make_reading(sensor_status=SensorStatus.DISCONNECTED)
    result = assess_telemetry(reading, now=NOW)
    assert result.status == AssessmentStatus.SENSOR_FAULT
    assert "SENSOR_DISCONNECTED" in result.triggered_rules


def test_stale_reading_is_monitoring_unavailable():
    reading = make_reading(timestamp=NOW - timedelta(seconds=61))
    result = assess_telemetry(reading, now=NOW)
    assert result.status == AssessmentStatus.MONITORING_UNAVAILABLE
    assert "STALE_TELEMETRY" in result.triggered_rules


def test_future_dated_reading_is_monitoring_unavailable():
    reading = make_reading(timestamp=NOW + timedelta(seconds=10))
    result = assess_telemetry(reading, now=NOW)
    assert result.status == AssessmentStatus.MONITORING_UNAVAILABLE
    assert "FUTURE_TIMESTAMP" in result.triggered_rules


def test_stale_high_gas_reading_is_not_treated_as_current_measurement():
    reading = make_reading(
        h2s=50.0,
        timestamp=NOW - timedelta(minutes=5),
    )
    result = assess_telemetry(reading, now=NOW)
    assert result.status == AssessmentStatus.MONITORING_UNAVAILABLE
    assert "H2S_INSTANTANEOUS_CRITICAL" not in result.triggered_rules


def test_multiple_hazards_are_all_reported():
    reading = make_reading(h2s=12.0, methane=15.0, oxygen=18.0)
    result = assess_telemetry(reading, now=NOW)
    assert result.status == AssessmentStatus.CRITICAL
    assert len(result.triggered_rules) == 3
