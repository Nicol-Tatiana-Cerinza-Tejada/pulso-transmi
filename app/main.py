from contextlib import asynccontextmanager
from collections import defaultdict, deque
import base64
from datetime import datetime, timedelta, timezone
import json
import uuid

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.competition import ParticipantIdentity, authenticate, receipt, submit
from app.contracts import SubmissionInput
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
    version="0.3.0",
    description="API pública del reto MLOps Pulso TransMi.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["X-Request-ID"],
)

rate_windows: dict[int, deque[datetime]] = defaultdict(deque)


@app.middleware("http")
async def public_headers(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    if request.url.path == "/v1/submissions" and request.method == "POST":
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return JSONResponse(status_code=415, content={"error": {"code": "unsupported_media_type", "message": "Content-Type must be application/json", "request_id": request.state.request_id}})
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > get_settings().submission_max_bytes
            except ValueError:
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_content_length", "message": "Content-Length must be an integer", "request_id": request.state.request_id}})
            if too_large:
                return JSONResponse(status_code=413, content={"error": {"code": "payload_too_large", "message": "Submission payload exceeds the configured limit", "request_id": request.state.request_id}})
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path in {"/v1/meta", "/v1/stations"} or request.url.path.startswith("/v1/downloads/"):
        response.headers["Cache-Control"] = "public, max-age=300"
    elif request.url.path == "/v1/leaderboard":
        response.headers["Cache-Control"] = "public, max-age=30"
    elif request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def pool(request: Request) -> asyncpg.Pool:
    if request.app.state.pool is None:
        raise HTTPException(status_code=503, detail={"code": "database_unavailable", "message": "Database pool unavailable"})
    return request.app.state.pool


async def participant(
    request: Request, authorization: str | None = Header(default=None)
) -> ParticipantIdentity:
    return await authenticate(pool(request), authorization)


def enforce_submission_rate(identity: ParticipantIdentity) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    window = rate_windows[identity.key_id]
    cutoff = now - timedelta(minutes=1)
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= settings.submission_rate_limit_per_minute:
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Too many submission requests"})
    window.append(now)


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
        "mode": "starter-and-competition-stream",
        "dataset": starter.metadata,
        "links": {
            "stations": "/v1/stations",
            "observations": "/v1/observations",
            "context": "/v1/context",
            "stream_observations": "/v1/stream/observations",
            "downloads": "/v1/downloads/{filename}",
            "openapi": "/openapi.json",
            "clock": "/v1/clock",
            "current_cycle": "/v1/forecast-cycles/current",
            "submissions": "/v1/submissions",
            "leaderboard": "/v1/leaderboard",
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


def decode_stream_cursor(cursor: str | None) -> tuple[datetime, datetime, str] | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        released_at, observed_at, station_id = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode()
        )
        return datetime.fromisoformat(released_at), datetime.fromisoformat(observed_at), station_id
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Invalid stream cursor"}) from exc


def encode_stream_cursor(released_at: datetime, observed_at: datetime, station_id: str) -> str:
    raw = json.dumps(
        [released_at.isoformat(), observed_at.isoformat(), station_id], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@app.get("/v1/stream/observations", tags=["competition"])
async def stream_observations(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, object]:
    position = decode_stream_cursor(cursor)
    async with pool(request).acquire() as connection:
        rows = await connection.fetch(
            """
            select o.station_id,o.observed_at,o.value as demand,o.released_at
            from competition.observations o
            join competition.scenario_clock c on c.scenario_id=o.scenario_id
            where c.state in ('running','completed')
              and ($1::timestamptz is null or
                   (o.released_at,o.observed_at,o.station_id)
                     > ($1::timestamptz,$2::timestamptz,$3::text))
            order by o.released_at,o.observed_at,o.station_id
            limit $4
            """,
            position[0] if position else None,
            position[1] if position else None,
            position[2] if position else None,
            limit + 1,
        )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_stream_cursor(
            last["released_at"], last["observed_at"], last["station_id"]
        )
    return {
        "data": [dict(row) for row in page],
        "count": len(page),
        "next_cursor": next_cursor,
        "server_time": datetime.now(timezone.utc),
    }


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


@app.get("/v1/clock", tags=["competition"])
async def competition_clock(request: Request) -> dict[str, object]:
    async with pool(request).acquire() as connection:
        row = await connection.fetchrow(
            """
            select ps.code, c.virtual_now, c.tick_number, c.state, c.last_tick_at
            from competition.scenario_clock c
            join competition.public_scenarios ps on ps.id=c.scenario_id
            where c.state in ('running','paused')
            order by ps.competition_start desc limit 1
            """
        )
    if row is None:
        return {"state": "waiting", "server_time": datetime.now(timezone.utc)}
    return {**dict(row), "server_time": datetime.now(timezone.utc)}


@app.get("/v1/forecast-cycles/current", tags=["competition"])
async def current_cycle(request: Request) -> dict[str, object]:
    async with pool(request).acquire() as connection:
        cycle = await connection.fetchrow(
            """
            select id, public_id, origin_at, data_cutoff, opens_at, closes_at,
                   target_start_at, target_end_at, state
            from competition.forecast_cycles
            where state='open' and now() < closes_at
            order by opens_at desc limit 1
            """
        )
        if cycle is None:
            raise HTTPException(status_code=404, detail={"code": "no_open_cycle", "message": "There is no open forecast cycle"})
        targets = await connection.fetch(
            """
            select station_id, target_at, horizon_steps * 15 as horizon_minutes
            from competition.cycle_targets where cycle_id=$1
            order by station_id, target_at
            """,
            cycle["id"],
        )
    return {
        "cycle_id": cycle["public_id"], "state": cycle["state"],
        "origin_at": cycle["origin_at"], "data_cutoff": cycle["data_cutoff"],
        "opens_at": cycle["opens_at"], "closes_at": cycle["closes_at"],
        "expected_predictions": len(targets),
        "targets": [dict(row) for row in targets],
    }


@app.get("/v1/me", tags=["participants"])
async def me(identity: ParticipantIdentity = Depends(participant)) -> dict[str, object]:
    return {"participant_id": identity.public_id, "display_name": identity.display_name, "kind": identity.kind}


@app.post("/v1/submissions", tags=["submissions"], status_code=201)
async def create_submission(
    payload: SubmissionInput,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    identity: ParticipantIdentity = Depends(participant),
) -> Response:
    enforce_submission_rate(identity)
    result, replayed = await submit(
        pool(request), identity, payload, idempotency_key,
        request.state.request_id, get_settings().submission_max_attempts,
    )
    return JSONResponse(status_code=200 if replayed else 201, content=jsonable(result))


def jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


@app.get("/v1/submissions/{submission_id}", tags=["submissions"])
async def submission_receipt(
    submission_id: str,
    request: Request,
    identity: ParticipantIdentity = Depends(participant),
) -> dict[str, object]:
    return await receipt(pool(request), identity, submission_id)


@app.get("/v1/leaderboard", tags=["competition"])
async def leaderboard(
    request: Request,
    window: str = Query(default="cumulative", pattern="^(cumulative|rolling_24h)$"),
) -> dict[str, object]:
    async with pool(request).acquire() as connection:
        rows = await connection.fetch(
            """
            select display_name, kind, eligible, accuracy, raw_wape,
                   accuracy_at_20, coverage, rank, calculated_at
            from competition.leaderboard_latest
            where window_type=$1
            order by rank nulls last, accuracy desc, display_name
            """,
            window,
        )
    return {"window": window, "data": [dict(row) for row in rows], "count": len(rows)}
