from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.settings import get_settings
from app.starter_store import InvalidCursor, StarterStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.starter = StarterStore.load(settings.starter_data_dir)
    app.state.pool = None
    if not settings.skip_db_startup:
        app.state.pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    yield
    if app.state.pool is not None:
        await app.state.pool.close()


app = FastAPI(
    title="Pulso TransMi API",
    version="0.2.0",
    description="API pública del reto MLOps Pulso TransMi.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def public_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "public, max-age=300"
    return response


def store(request: Request) -> StarterStore:
    return request.app.state.starter


def validate_range(start: datetime | None, end: datetime | None) -> None:
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.utcoffset() is None:
            raise HTTPException(status_code=422, detail=f"{name} must include a timezone offset")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start must be before or equal to end")


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pulso-transmi-api"}


@app.get("/ready", tags=["operations"])
async def ready(request: Request) -> dict[str, object]:
    if request.app.state.pool is None:
        raise HTTPException(status_code=503, detail="database pool unavailable")
    async with request.app.state.pool.acquire() as connection:
        await connection.fetchval("select 1")
    starter = store(request)
    return {
        "status": "ready",
        "database": "connected",
        "dataset": starter.metadata["dataset"],
        "observations": starter.metadata["observation_rows"],
    }


@app.get("/v1/meta", tags=["public"])
async def meta(request: Request) -> dict[str, object]:
    starter = store(request)
    return {
        "project": "Pulso TransMi",
        "api_version": app.version,
        "mode": "static-starter",
        "dataset": starter.metadata,
        "links": {
            "stations": "/v1/stations",
            "observations": "/v1/observations",
            "context": "/v1/context",
            "downloads": "/v1/downloads/{filename}",
            "openapi": "/openapi.json",
        },
    }


@app.get("/v1/stations", tags=["public"])
async def stations(request: Request) -> dict[str, object]:
    items = store(request).stations
    return {"data": items, "count": len(items)}


@app.get("/v1/observations", tags=["public"])
async def observations(
    request: Request,
    station_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, object]:
    validate_range(start, end)
    try:
        page = store(request).observation_page(
            station_id=station_id, start=start, end=end, cursor=cursor, limit=limit
        )
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": page.data, "count": len(page.data), "next_cursor": page.next_cursor}


@app.get("/v1/context", tags=["public"])
async def context(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, object]:
    validate_range(start, end)
    try:
        page = store(request).context_page(start=start, end=end, cursor=cursor, limit=limit)
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"data": page.data, "count": len(page.data), "next_cursor": page.next_cursor}


@app.get("/v1/downloads/{filename}", tags=["public"])
async def download(request: Request, filename: str) -> FileResponse:
    try:
        path, digest = store(request).download(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    media_type = "application/json" if filename.endswith(".json") else "text/csv"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"ETag": f'"sha256:{digest}"', "Cache-Control": "public, max-age=3600"},
    )
