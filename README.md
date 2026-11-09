# Confined-Space Safety Intelligence & Incident Logging Platform

A Python backend prototype for receiving confined-space sensor telemetry, detecting potential atmospheric hazards, and maintaining a persistent, hash-chained audit log.

> **Safety disclaimer:** This is a prototype using simulated telemetry. It is not certified, field-validated, or suitable for authorizing confined-space entry. It does not replace calibrated gas detectors, competent personnel, permits, or established safety procedures. A NORMAL assessment only means no configured threshold was crossed in the evaluated reading.

## Problem

Confined spaces can expose workers to hazardous atmospheric conditions, including toxic gases, oxygen deficiency, and combustible gases. Delayed detection, disconnected sensors, stale readings, and inadequate incident records can complicate monitoring.

This project demonstrates a backend architecture for receiving sensor readings, evaluating configurable hazard rules, and preserving telemetry records in a tamper-evident audit chain.

## Implemented Features

- **Telemetry ingestion:** FastAPI endpoints receive sensor readings validated using Pydantic.
- **Input validation:** Rejects invalid measurement values and inconsistent sensor states.
- **Hazard detection:** Applies deterministic, explainable rules to identify potential atmospheric hazards.
- **Persistent storage:** Stores telemetry events in SQLite.
- **Duplicate handling:** Ignores identical duplicate events and rejects conflicting events with the same event ID.
- **Hash-chained audit log:** Uses SHA-256 hashes and previous-record references to make modifications detectable.
- **Integrity verification:** Checks record sequence, hash consistency, and chain linkage.
- **Assessment retrieval:** Calculates a hazard assessment for a stored telemetry event when requested.

## Architecture

    Synthetic Sensor Simulator
              |
              v
         FastAPI API
              |
              v
       Pydantic Validation
              |
              v
      SQLite Telemetry Store
              |
       +------+------+
       |             |
       v             v
    Persistent    SHA-256 Hash
    Telemetry        Chain
       |             |
       v             v
    Hazard       Integrity
    Detection    Verification
       |
       v
    Explainable
    Assessment

Hazard assessments are calculated when the assessment endpoint is requested. Separate assessment records are not currently persisted.

## Hazard Detection Rules

The current engine uses configurable instantaneous thresholds.

| Parameter | Condition | Assessment |
|---|---|---|
| Hydrogen sulfide (H2S) | >= 5 ppm | WARNING |
| Hydrogen sulfide (H2S) | >= 10 ppm | CRITICAL |
| Methane | > 10% LEL | CRITICAL |
| Oxygen | < 19.5% | CRITICAL |
| Oxygen | > 23.5% | CRITICAL |
| Reading age | > 60 seconds | MONITORING_UNAVAILABLE |
| Future timestamp | More than 5 seconds ahead | MONITORING_UNAVAILABLE |
| Disconnected sensor | Sensor disconnected | SENSOR_FAULT |

Additional behavior:

- Missing measurements produce a SENSOR_FAULT assessment.
- Stale or invalid telemetry is not evaluated as a valid current atmospheric reading.
- Critical conditions take precedence over warnings.
- Monitoring failures are not interpreted as evidence of safety.
- Assessment results include reasons and triggered rule identifiers.

**Important:** These are prototype software thresholds, not a complete regulatory or occupational-safety specification. The H2S critical threshold is an instantaneous tripwire, not a time-weighted exposure limit.

## API Endpoints

Base path: `/api/v1`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Application health check |
| POST | `/api/v1/telemetry` | Submit a telemetry event |
| GET | `/api/v1/telemetry` | Retrieve stored telemetry events |
| GET | `/api/v1/telemetry/integrity` | Verify audit-chain integrity |
| GET | `/api/v1/telemetry/{event_id}/assessment` | Calculate an assessment for a stored event |

### Ingestion responses

- `202 Accepted` — Event accepted and committed to SQLite.
- `202 Accepted` — Identical duplicate event ignored.
- `409 Conflict` — An existing event ID was submitted with different content.
- `422 Unprocessable Entity` — Request payload failed validation.
- `503 Service Unavailable` — Audit verification or checkpoint persistence failed; ingestion was not confirmed. Reconcile the event and audit state before retrying.

The telemetry retrieval endpoint supports a `limit` query parameter, defaulting to 100 and allowing values from 1 to 1000.

Interactive API documentation is available at `/docs` when the application is running.

## Technology Stack

- Python
- FastAPI
- Pydantic
- SQLite
- SHA-256 hashing (`hashlib`)
- Pytest
- HTTPX2 for API integration testing

## Getting Started

### Prerequisites

- Python 3.11 or later
- Git

### Clone the repository

    git clone https://github.com/NEERADA098/confined_space_safety_intelligence.git
    cd confined_space_safety_intelligence

### Create and activate a virtual environment

    python3 -m venv .venv
    source .venv/bin/activate

### Install development dependencies

    python -m pip install -r requirements-dev.txt

### Run the API

    uvicorn app.main:app --reload

The API is available at `http://127.0.0.1:8000`.

Interactive documentation: `http://127.0.0.1:8000/docs`.

### Run tests

    python -m pytest -v

The test suite covers hazard detection, SQLite persistence, audit-chain integrity, concurrency, failure recovery, and API integration.

## Database and Audit Integrity

The application uses a file-backed SQLite database.

Default database location: `data/telemetry.db`.

A custom location can be configured using the `TELEMETRY_DATABASE_PATH` environment variable.

Each stored telemetry event is associated with a SHA-256 record hash and the preceding record's hash. Verification recalculates record hashes and checks sequence and linkage consistency.

The application also maintains a local JSON audit-checkpoint sidecar containing the latest sequence number and record hash. The default path is the database filename followed by `.audit-checkpoint.json`; a custom path can be configured using `TELEMETRY_CHECKPOINT_PATH`.

A new empty database receives a genesis checkpoint. A non-empty database with a missing checkpoint requires explicit trust establishment through `establish_checkpoint()`; it is never silently adopted.

The checkpoint is updated before the SQLite transaction commits, while the SQLite writer lock is held. If the database and checkpoint become inconsistent, integrity verification fails closed and ingestion is blocked until explicit checkpoint recovery. SQLite connections use a 30-second lock timeout and explicitly request FULL synchronous mode. Integrity verification and ingestion serialize through SQLite's writer lock. Each ingestion currently verifies the complete chain, so verification cost grows with the number of stored events; repeated ingestion therefore has quadratic cumulative verification work. This is a deliberate integrity-first prototype trade-off, not a high-throughput design.

The checkpoint is a separate local file, not an external trust anchor. An actor with sufficient access to both files could rewrite the database and checkpoint. Stronger audit guarantees require separately controlled remote or immutable checkpoint storage, signatures, and access controls.



Local database files are excluded from Git.

## Testing

| Test module | Coverage |
|---|---|
| `tests/test_hazard_detection.py` | Hazard thresholds, sensor faults, stale readings, and multiple hazards |
| `tests/test_sqlite_store.py` | Persistence, duplicate handling, conflicts, hash-chain integrity, concurrency, and recovery |
| `tests/test_api.py` | API ingestion, retrieval, assessment, integrity, and persistence |

Latest verified result after documentation and formatting cleanup: **52 tests passed**.

## Current Limitations

- Telemetry is generated using a simulator; physical sensor integration has not been validated.
- The project has not undergone field testing or safety certification.
- Hazard assessments are computed on request and are not stored as separate records.
- Offline synchronization is not implemented.
- A monitoring dashboard is not currently implemented.
- Authentication, authorization, and production security hardening remain future work.
- Hash-chain verification does not protect against a privileged actor rewriting the complete database.
- Simulator coordinates are placeholders, not verified confined-space site locations.

## Roadmap

- Integrate real sensor hardware and validate measurement handling.
- Implement offline buffering and synchronization.
- Add a monitoring dashboard and alert delivery.
- Add authentication, authorization, and operational security controls.
- Introduce externally anchored audit checkpoints.
- Add deployment and containerization support.
- Conduct controlled validation against applicable safety requirements.

## Repository Structure

    confined_space_safety_intelligence/
    ├── app/
    │   ├── api/
    │   │   └── routes.py
    │   ├── schemas/
    │   │   ├── assessment.py
    │   │   └── telemetry.py
    │   ├── services/
    │   │   ├── hazard_detection.py
    │   │   └── sqlite_store.py
    │   └── main.py
    ├── simulator/
    │   └── sensor_simulator.py
    ├── tests/
    │   ├── test_api.py
    │   ├── test_hazard_detection.py
    │   └── test_sqlite_store.py
    ├── requirements.txt
    ├── requirements-dev.txt
    └── .gitignore

---

**Project status:** Phase 1 — telemetry ingestion; Phase 2 — explainable hazard detection; Phase 3 — SQLite persistence and hash-chained audit logging; Phase 4 — database-level pagination; Phase 5 — audit-chain integrity edge-case tests; Phase 6 — local audit-checkpoint sidecar and explicit recovery; Phase 7 — transaction consistency, concurrency testing, and failure-recovery hardening.
