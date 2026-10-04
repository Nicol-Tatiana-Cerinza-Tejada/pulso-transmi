"""Colector incremental del stream de observaciones de Pulso TransMi."""

from __future__ import annotations

import argparse
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .api_client import PulsoTransmiClient, PulsoTransmiError
from .db import SupabaseDB


# Páginas más pequeñas reducen los timeouts cuando el API libera mucho backlog.
# Repetir una página es seguro: observations usa upsert y el cursor solo avanza
# después de confirmar la escritura.
BATCH_SIZE = 250


def to_utc_iso(value: str) -> str:
    """Convierte un timestamp ISO-8601 con offset a UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp sin zona horaria: {value!r}")
    return parsed.astimezone(timezone.utc).isoformat()


def observation_value(row: Mapping[str, Any]) -> int | None:
    """Lee la demanda en el contrato v1 (``demand``) o v2 (``measurement``).

    En v2 ``measurement.value`` es texto decimal o ``null`` con
    ``quality == "missing"``. Un faltante no es cero: se devuelve ``None`` y la
    fila no se guarda, de modo que los modelos lo traten como hueco.
    """
    if "measurement" in row or row.get("schema_version") == 2:
        measurement = row.get("measurement") or {}
        raw = measurement.get("value")
        if measurement.get("quality") == "missing" or raw is None:
            return None
        if measurement.get("unit", "passengers") != "passengers":
            raise ValueError(f"Unidad no soportada: {measurement.get('unit')!r}")
        value = float(Decimal(str(raw)))
    else:
        value = float(row["demand"])
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Demanda inválida para {row.get('station_id')}: {value!r}")
    return int(round(value))


def normalize_observations(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        value = observation_value(row)
        if value is None:
            continue
        version = int(row.get("schema_version") or 1)
        normalized.append(
            {
                "station_id": row["station_id"],
                "ts": to_utc_iso(str(row["observed_at"])),
                "value": value,
                "released_at": to_utc_iso(str(row["released_at"]))
                if row.get("released_at")
                else None,
                "source": "pulso-transmi-api-stream" if version == 1 else f"pulso-transmi-api-stream-v{version}",
            }
        )
    return normalized


def count_missing(rows: list[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if observation_value(row) is None)


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
        rows_missing = 0
        schema_versions: set[int] = set()
        cursor_reset = False

        while True:
            try:
                page = api.stream_observations(cursor=checkpoint, limit=batch_size)
            except PulsoTransmiError as exc:
                detail = exc.detail if isinstance(exc.detail, Mapping) else {}
                nested_detail = detail.get("detail")
                if isinstance(nested_detail, Mapping):
                    detail = nested_detail
                if (
                    not cursor_reset
                    and checkpoint is not None
                    and exc.status_code == 400
                    and detail.get("code") == "invalid_cursor"
                ):
                    # El API puede expirar/rechazar checkpoints antiguos. El
                    # stream completo es seguro de releer porque el upsert usa
                    # (station_id, ts); el cursor nuevo solo se confirma al final.
                    checkpoint = None
                    cursor_reset = True
                    continue
                raise
            raw_rows = list(page.get("data") or [])
            rows = normalize_observations(raw_rows)
            rows_missing += len(raw_rows) - len(rows)
            schema_versions.update(int(row.get("schema_version") or 1) for row in raw_rows)
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
            "rows_missing": rows_missing,
            "schema_versions": sorted(schema_versions),
            "pages": pages,
            "cursor": checkpoint,
            "cursor_reset": cursor_reset,
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    try:
        with PulsoTransmiClient() as api:
            result = collect_once(api, SupabaseDB(), batch_size=args.batch_size)
        print(result)
        return 0 if result["status"] in {"succeeded", "waiting"} else 1
    except Exception as exc:
        print(f"collector error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
