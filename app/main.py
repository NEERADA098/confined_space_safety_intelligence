
from fastapi import FastAPI

from app.api.routes import router


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

    return {
        "status": "healthy",
        "service": "telemetry-ingestion-api",
        "version": "0.1.0",
    }
