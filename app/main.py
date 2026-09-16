from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request

from app.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
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
    version="0.1.0",
    description="Plataforma central del reto MLOps Pulso TransMi.",
    lifespan=lifespan,
)


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pulso-transmi-api"}


@app.get("/ready", tags=["operations"])
async def ready(request: Request) -> dict[str, str]:
    if request.app.state.pool is None:
        raise HTTPException(status_code=503, detail="database pool unavailable")
    async with request.app.state.pool.acquire() as connection:
        await connection.fetchval("select 1")
    return {"status": "ready", "database": "connected"}


@app.get("/v1/meta", tags=["public"])
async def meta(request: Request) -> dict[str, object]:
    if request.app.state.pool is None:
        raise HTTPException(status_code=503, detail="database pool unavailable")
    async with request.app.state.pool.acquire() as connection:
        station_count = await connection.fetchval("select count(*) from catalog.stations")
        scenario = await connection.fetchrow(
            """
            select code, state
            from sim.scenarios
            where state in ('validated', 'scheduled', 'running', 'frozen')
            order by id desc
            limit 1
            """
        )
    return {
        "project": "Pulso TransMi",
        "station_count": station_count,
        "active_scenario": dict(scenario) if scenario else None,
    }
