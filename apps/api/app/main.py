from fastapi import FastAPI, HTTPException

from apps.api.app.core.database import check_database

APP_NAME = "bet-analytics"
APP_VERSION = "0.2.0"

app = FastAPI(title=APP_NAME, version=APP_VERSION)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db", tags=["system"])
def database_health() -> dict[str, str]:
    try:
        check_database()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "connected"}


@app.get("/version", tags=["system"])
def version() -> dict[str, str]:
    return {"name": APP_NAME, "version": APP_VERSION}
