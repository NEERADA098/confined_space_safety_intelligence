import logging

from fastapi import Depends, FastAPI, HTTPException, status

from app.api.routes import get_telemetry_store, router
from app.services.sqlite_store import SQLiteTelemetryStore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Confined-Space Safety Intelligence Platform",
    description=(
        "Software-first environmental telemetry ingestion "
        "and monitoring prototype."
    ),
    version="0.1.0",
)

app.include_router(router)


@app.get("/health", tags=["System"])
def health_check():
    """Liveness: confirms that the API process is responding."""
    return {
        "status": "healthy",
        "service": "telemetry-ingestion-api",
        "version": "0.1.0",
    }


@app.get("/ready", tags=["System"])
def readiness_check(
    store: SQLiteTelemetryStore = Depends(get_telemetry_store),
):
    """
    Readiness: checks database access and checkpoint/head agreement.

    This does not replace full audit-chain verification.
    """
    try:
        result = store.check_readiness()
    except Exception:
        logger.exception("readiness_check_failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage readiness check failed",
        )

    if not result["ready"]:
        logger.error(
            "service_not_ready database=%s checkpoint=%s reason=%s",
            result.get("database"),
            result.get("checkpoint"),
            result.get("reason"),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                **result,
            },
        )

    return {
        "status": "ready",
        "service": "telemetry-ingestion-api",
        **result,
    }
