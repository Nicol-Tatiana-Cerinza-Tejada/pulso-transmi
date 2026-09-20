"""Colector incremental del stream de observaciones de Pulso TransMi."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .api_client import PulsoTransmiClient
from .db import SupabaseDB


BATCH_SIZE = 1000


def to_utc_iso(value: str) -> str:
    """Convierte un timestamp ISO-8601 con offset a UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp sin zona horaria: {value!r}")
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_observations(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "station_id": row["station_id"],
            "ts": to_utc_iso(str(row["observed_at"])),
            "value": row["demand"],
            "released_at": to_utc_iso(str(row["released_at"]))
            if row.get("released_at")
            else None,
            "source": "pulso-transmi-api-stream",
        }
        for row in rows
    ]


def collect_once(
    api: PulsoTransmiClient,
    db: SupabaseDB,
    *,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    """Ejecuta un ciclo del colector sin propagar errores de negocio al caller.

    El cursor confirmado se lee antes de consultar el stream. Cada página se
    persiste primero; solo cuando termina el upsert se adopta ``next_cursor``
    como cursor confirmado. El upsert por PK hace segura la repetición tras un
    fallo entre ambas operaciones.
    """
    if not 1 <= batch_size <= 5000:
        raise ValueError("batch_size debe estar entre 1 y 5000")

    run_id: int | None = None
    confirmed_cursor: str | None = None
    try:
        run_id = db.start_collector_run()
        confirmed_cursor = db.latest_confirmed_cursor()
        checkpoint = confirmed_cursor
        clock = api.clock()
        if clock.get("state") == "waiting":
            db.finish_collector_run(
                run_id,
                status="succeeded",
                rows_new=0,
                cursor=confirmed_cursor,
            )
            return {"status": "waiting", "rows": 0, "cursor": confirmed_cursor}

        before_count = db.observation_count()
        pages = 0
        rows_processed = 0

        while True:
            page = api.stream_observations(cursor=checkpoint, limit=batch_size)
            raw_rows = list(page.get("data") or [])
            rows = normalize_observations(raw_rows)
            if rows:
                db.upsert_observations(rows)
                rows_processed += len(rows)

            next_cursor = page.get("next_cursor")
            pages += 1
            if not next_cursor:
                break
            if next_cursor == checkpoint:
                raise RuntimeError("El API devolvió el mismo cursor dos veces")
            # El checkpoint se mueve únicamente después del upsert anterior.
            checkpoint = next_cursor

        after_count = db.observation_count()
        rows_new = max(0, after_count - before_count)
        db.finish_collector_run(
            run_id,
            status="succeeded",
            rows_new=rows_new,
            cursor=checkpoint,
        )
        return {
            "status": "succeeded",
            "rows": rows_processed,
            "rows_new": rows_new,
            "pages": pages,
            "cursor": checkpoint,
        }
    except Exception as exc:
        try:
            if run_id is not None:
                db.finish_collector_run(
                    run_id,
                    status="failed",
                    rows_new=0,
                    cursor=confirmed_cursor,
                    error=str(exc)[:4000],
                )
        except Exception:
            # Un error de Supabase al registrar el fallo no debe tumbar el job.
            pass
        return {"status": "failed", "rows": 0, "cursor": confirmed_cursor, "error": str(exc)}


def main() -> int:
    try:
        with PulsoTransmiClient() as api:
            result = collect_once(api, SupabaseDB())
        print(result)
        return 0 if result["status"] in {"succeeded", "waiting"} else 1
    except Exception as exc:
        print(f"collector error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
