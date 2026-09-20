"""Migra el histórico público de Pulso TransMi a Supabase.

Uso desde la raíz del repositorio:

    python scripts/migrate_historic.py

Requiere ``SUPABASE_URL`` y ``SUPABASE_SECRET_KEY`` (o la variable legacy
``SUPABASE_SERVICE_ROLE_KEY``). El API histórico es público; ``PULSO_API_URL``
es opcional y permite cambiar el despliegue.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api_client import PulsoTransmiClient  # noqa: E402
from src.db import SupabaseDB  # noqa: E402


BATCH_SIZE = 1000
EXPECTED_ROWS = 51_840


def to_utc_iso(value: str) -> str:
    """Normaliza un timestamp ISO-8601 con offset a UTC."""
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp sin zona horaria: {value!r}")
    return parsed.astimezone(timezone.utc).isoformat()


def station_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "station_id": row["station_id"],
            "station_name": row["station_name"],
            "corridor": row.get("corridor"),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "source_metadata": {"source": "pulso-transmi-api"},
            "active": True,
        }
        for row in payload.get("data", [])
    ]


def observation_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Mapea el contrato del API (observed_at/demand) al esquema local (ts/value)."""
    return [
        {
            "station_id": row["station_id"],
            "ts": to_utc_iso(row["observed_at"]),
            "value": row["demand"],
            "released_at": to_utc_iso(row["released_at"])
            if row.get("released_at")
            else None,
            "source": "pulso-transmi-api",
        }
        for row in payload.get("data", [])
    ]


def table_count(db: SupabaseDB, table: str) -> int:
    response = db.client.table(table).select("station_id", count="exact", head=True).execute()
    return int(response.count or 0)


def open_collector_run(db: SupabaseDB) -> int:
    response = (
        db.client.table("collector_runs")
        .insert({"status": "running", "rows_new": 0})
        .select("id")
        .execute()
    )
    if not response.data:
        raise RuntimeError("Supabase no devolvió el id de collector_runs")
    return int(response.data[0]["id"])


def close_collector_run(
    db: SupabaseDB,
    run_id: int,
    *,
    status: str,
    rows_new: int,
    cursor: str | None,
    error: str | None = None,
) -> None:
    values: dict[str, Any] = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "rows_new": rows_new,
        "cursor": cursor,
        "error": error,
    }
    db.client.table("collector_runs").update(values).eq("id", run_id).execute()


def print_station_counts(counts: Counter[str]) -> int:
    total = sum(counts.values())
    print("Conteo histórico por estación:")
    for station_id in sorted(counts):
        print(f"  {station_id}: {counts[station_id]}")
    result = "OK" if total == EXPECTED_ROWS else "ERROR"
    print(f"TOTAL: {total} (esperado: {EXPECTED_ROWS}) [{result}]")
    return total


def main() -> None:
    db = SupabaseDB()
    run_id = open_collector_run(db)
    current_cursor: str | None = None
    rows_seen = 0
    counts: Counter[str] = Counter()

    try:
        before_count = table_count(db, "observations")

        with PulsoTransmiClient() as api:
            stations_payload = api.stations()
            stations = station_rows(stations_payload)
            if not stations:
                raise RuntimeError("El API no devolvió estaciones")
            db.upsert("stations", stations, on_conflict="station_id")
            print(f"Estaciones sincronizadas: {len(stations)}")

            while True:
                page = api.observations(cursor=current_cursor, limit=BATCH_SIZE)
                rows = observation_rows(page)
                if rows:
                    db.upsert_observations(rows)
                    rows_seen += len(rows)
                    counts.update(row["station_id"] for row in rows)

                next_cursor = page.get("next_cursor")
                print(f"Página: {len(rows)} filas; acumuladas: {rows_seen}")
                if not next_cursor:
                    break
                if next_cursor == current_cursor:
                    raise RuntimeError("El API devolvió el mismo cursor dos veces")
                current_cursor = next_cursor

        total = print_station_counts(counts)
        if total != EXPECTED_ROWS:
            raise RuntimeError(
                f"El histórico descargado tiene {total} filas; se esperaban {EXPECTED_ROWS}"
            )

        after_count = table_count(db, "observations")
        rows_new = max(0, after_count - before_count)
        close_collector_run(
            db,
            run_id,
            status="succeeded",
            rows_new=rows_new,
            cursor=current_cursor,
        )
        print(f"Migración completada. Filas nuevas en Supabase: {rows_new}")
    except Exception as exc:
        close_collector_run(
            db,
            run_id,
            status="failed",
            rows_new=0,
            cursor=current_cursor,
            error=str(exc)[:4000],
        )
        raise


if __name__ == "__main__":
    main()
