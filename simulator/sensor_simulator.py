
import argparse
import json
import random
import time

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "http://127.0.0.1:8000/api/v1/telemetry"

SCENARIOS = [
    "normal",
    "rising_h2s",
    "oxygen_depletion",
    "sudden_gas_spike",
    "disconnected",
    "stale",
    "corrupted",
]


def generate_payload(
    scenario: str,
    index: int,
    sensor_id: str,
    rng: random.Random,
) -> dict:

    timestamp = datetime.now(timezone.utc)

    h2s = round(rng.uniform(0.1, 2.0), 2)
    methane = round(rng.uniform(0.5, 5.0), 2)
    oxygen = round(rng.uniform(20.5, 20.9), 2)

    sensor_status = "CONNECTED"

    if scenario == "rising_h2s":
        h2s = round(0.5 + index * 2.5, 2)

    elif scenario == "oxygen_depletion":
        oxygen = round(max(5.0, 20.9 - index * 1.5), 2)

    elif scenario == "sudden_gas_spike":
        h2s = 80.0
        methane = 45.0

    elif scenario == "disconnected":
        sensor_status = "DISCONNECTED"
        h2s = None
        methane = None
        oxygen = None

    elif scenario == "stale":
        timestamp -= timedelta(minutes=15)

    payload = {
        "event_id": str(uuid4()),
        "sensor_id": sensor_id,
        "timestamp": timestamp.isoformat(),
        "location": {
            "site_id": "SIMULATED_SITE_001",
            "zone_id": "CONFINED_ZONE_A",
            "latitude": 18.5204,
            "longitude": 73.8567,
        },
        "sensor_status": sensor_status,
        "h2s_ppm": h2s,
        "methane_lel_pct": methane,
        "oxygen_pct": oxygen,
    }

    if scenario == "corrupted":
        payload["oxygen_pct"] = -15.0
        payload["methane_lel_pct"] = 150.0

    return payload


def send_payload(payload: dict) -> None:

    body = json.dumps(payload).encode("utf-8")

    request = Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=5) as response:
            result = response.read().decode("utf-8")
            print(
                f"HTTP {response.status}: {result}"
            )

    except HTTPError as exc:
        error_body = exc.read().decode("utf-8")

        print(
            f"HTTP {exc.code}: {error_body}"
        )

    except URLError as exc:
        print(
            f"Connection failed: {exc.reason}"
        )


def main():

    parser = argparse.ArgumentParser(
        description="Confined-space sensor simulator"
    )

    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default="normal",
    )

    parser.add_argument(
        "--count",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--sensor-id",
        default="SIM-SENSOR-001",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count must be at least 1")

    if args.interval < 0:
        parser.error("--interval cannot be negative")

    rng = random.Random(args.seed)

    print(
        f"Starting simulator: scenario={args.scenario}"
    )

    for index in range(args.count):

        payload = generate_payload(
            scenario=args.scenario,
            index=index,
            sensor_id=args.sensor_id,
            rng=rng,
        )

        print(
            "\nGenerated event:",
            json.dumps(payload, indent=2),
        )

        send_payload(payload)

        if index < args.count - 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
