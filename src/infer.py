"""Genera y envía predicciones para el ciclo abierto actual."""

from __future__ import annotations

import argparse
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .api_client import PulsoTransmiClient, PulsoTransmiError
from .baselines import FREQUENCY, HORIZONS
from .db import SupabaseDB
from .train import encode_features


def utc(value: str | datetime) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def load_champion(db: SupabaseDB) -> dict[str, Any]:
    response = (
        db.client.table("model_versions")
        .select("*")
        .eq("status", "champion")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise RuntimeError("No existe un modelo champion en model_versions")
    return dict(response.data[0])


def load_observations_until(db: SupabaseDB, cutoff: pd.Timestamp, page_size: int = 1000) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            db.client.table("observations")
            .select("station_id,ts,value")
            .lte("ts", cutoff.isoformat())
            .order("ts")
            .order("station_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = list(response.data or [])
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    if not rows:
        raise RuntimeError(f"No hay observations hasta data_cutoff={cutoff.isoformat()}")
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame


def build_target_features(history: pd.DataFrame, targets: list[dict[str, Any]], cutoff: pd.Timestamp) -> tuple[pd.DataFrame, dict[int, list[int]]]:
    """Construye una fila por target usando únicamente history <= cutoff."""
    history = history[history["ts"] <= cutoff].copy()
    series_by_station = {
        str(station): group.set_index("ts")["value"].sort_index()
        for station, group in history.groupby("station_id")
    }
    rows: list[dict[str, Any]] = []
    positions: dict[int, list[int]] = {horizon: [] for horizon in HORIZONS}
    for target in targets:
        station_id = str(target["station_id"])
        target_at = utc(target["target_at"])
        horizon = int((target_at - cutoff).total_seconds() // 60)
        if horizon not in positions:
            raise RuntimeError(f"Horizonte no soportado por el champion: +{horizon} minutos")
        series = series_by_station.get(station_id)
        if series is None:
            raise RuntimeError(f"No hay histórico para station_id={station_id}")
        features: dict[str, Any] = {
            "station_id": station_id,
            "target_at": target_at,
            "target_hour": target_at.hour,
            "target_weekday": target_at.dayofweek,
        }
        for lag in (1, 2, 4, 96, 672):
            timestamp = cutoff - lag * FREQUENCY
            if timestamp not in series.index:
                raise RuntimeError(f"Falta lag_{lag} para station_id={station_id}")
            features[f"lag_{lag}"] = float(series.loc[timestamp])
        history_to_cutoff = series[series.index <= cutoff]
        for window in (4, 96, 672):
            if len(history_to_cutoff) < window:
                raise RuntimeError(f"Falta historia para rolling_mean_{window} de {station_id}")
            features[f"rolling_mean_{window}"] = float(history_to_cutoff.tail(window).mean())
        positions[horizon].append(len(rows))
        rows.append(features)
    return pd.DataFrame(rows), positions


def stable_idempotency_key(participant_id: str, cycle_id: str, attempt: int) -> str:
    raw = f"{participant_id}|{cycle_id}|{attempt}".encode()
    return f"ptm-{hashlib.sha256(raw).hexdigest()[:56]}"


def existing_attempt(db: SupabaseDB, participant_id: str, cycle_id: str) -> dict[str, Any] | None:
    response = (
        db.client.table("submission_receipts")
        .select("*")
        .eq("participant_id", participant_id)
        .eq("cycle_id", cycle_id)
        .order("attempt", desc=True)
        .limit(1)
        .execute()
    )
    return dict(response.data[0]) if response.data else None


def save_predictions(db: SupabaseDB, cycle_id: str, model_version: str, predictions: list[dict[str, Any]]) -> None:
    rows = [
        {
            "cycle_id": cycle_id,
            "station_id": item["station_id"],
            "target_at": utc(item["target_at"]).isoformat(),
            "value": item["value"],
            "model_version": model_version,
            "submission_id": None,
        }
        for item in predictions
    ]
    db.upsert("predictions", rows, on_conflict="cycle_id,station_id,target_at")


def run_inference(
    api: PulsoTransmiClient,
    db: SupabaseDB,
    *,
    bucket: str = "model-artifacts",
) -> dict[str, Any]:
    clock = api.clock()
    if clock.get("state") == "waiting":
        return {"status": "waiting"}
    try:
        cycle = api.current_cycle()
    except PulsoTransmiError as exc:
        if exc.status_code == 404:
            return {"status": "no_open_cycle"}
        raise
    if cycle.get("state") not in (None, "open"):
        return {"status": "no_open_cycle"}

    cycle_id = str(cycle["cycle_id"])
    cutoff = utc(cycle["data_cutoff"])
    targets = list(cycle.get("targets") or [])
    closes_at = utc(cycle["closes_at"])
    if not targets:
        return {"status": "no_open_cycle"}

    champion = load_champion(db)
    try:
        import joblib
        import io
        artifact = joblib.load(io.BytesIO(db.download_artifact(bucket, champion["artifact_path"].split("/", 1)[-1])))
    except Exception as exc:
        raise RuntimeError(f"No se pudo cargar el champion desde Storage: {exc}") from exc

    history = load_observations_until(db, cutoff)
    target_features, positions = build_target_features(history, targets, cutoff)
    predictions: list[dict[str, Any] | None] = [None] * len(targets)
    for horizon, indexes in positions.items():
        if not indexes:
            continue
        matrix, _ = encode_features(target_features.iloc[indexes], artifact["feature_columns"])
        values = np.asarray(artifact["models"][horizon].predict(matrix), dtype=float)
        for index, value in zip(indexes, values, strict=True):
            if not math.isfinite(float(value)):
                raise RuntimeError(f"Predicción no finita para target {targets[index]}")
            predictions[index] = {
                "station_id": str(targets[index]["station_id"]),
                "target_at": utc(targets[index]["target_at"]).isoformat(),
                "value": max(0.0, float(value)),
            }
    if any(item is None for item in predictions):
        raise RuntimeError("No se generó una predicción para cada target del ciclo")
    final_predictions = [item for item in predictions if item is not None]
    if len({(item["station_id"], item["target_at"]) for item in final_predictions}) != len(targets):
        raise RuntimeError("Hay targets duplicados o faltantes en las predicciones")

    participant = api.me()
    participant_id = str(participant.get("public_id") or participant.get("participant_id") or participant.get("id"))
    if participant_id in {"None", ""}:
        raise RuntimeError("/v1/me no devolvió un identificador de participante")
    attempt_record = existing_attempt(db, participant_id, cycle_id)
    if attempt_record and attempt_record["status"] == "accepted":
        return {"status": "already_submitted", "submission_id": attempt_record["submission_id"]}
    attempt = int(attempt_record["attempt"]) if attempt_record else 1
    if attempt > 3:
        return {"status": "attempt_limit_reached"}

    model = champion["version"]
    payload = {
        "schema_version": "1.0",
        "cycle_id": cycle_id,
        "client_run_id": f"infer-{participant_id}-{cycle_id}-{attempt}",
        "data_cutoff": cutoff.isoformat(),
        "model": {
            "version": model,
            "trained_at": champion.get("created_at"),
            "training_data_end": champion["data_cutoff"],
            "git_commit": champion.get("git_commit"),
        },
        "predictions": final_predictions,
    }
    key = attempt_record["idempotency_key"] if attempt_record else stable_idempotency_key(participant_id, cycle_id, attempt)
    if attempt_record:
        payload = attempt_record["payload"]
        final_predictions = payload["predictions"]
        model = payload["model"]["version"]
    else:
        db.insert(
            "submission_receipts",
            {
                "participant_id": participant_id,
                "cycle_id": cycle_id,
                "attempt": attempt,
                "idempotency_key": key,
                "payload": payload,
                "status": "pending",
            },
        )
    save_predictions(db, cycle_id, model, final_predictions)

    receipt = api.create_submission(payload, idempotency_key=key)
    submission_id = receipt.get("submission_id")
    db.client.table("submission_receipts").update(
        {"status": "accepted", "submission_id": submission_id, "receipt": receipt, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("participant_id", participant_id).eq("cycle_id", cycle_id).eq("attempt", attempt).execute()
    db.client.table("predictions").update({"submission_id": submission_id}).eq("cycle_id", cycle_id).is_("submission_id", "null").execute()
    return {"status": "accepted", "submission_id": submission_id, "attempt": attempt, "closes_at": closes_at.isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default="model-artifacts")
    args = parser.parse_args()
    try:
        with PulsoTransmiClient() as api:
            result = run_inference(api, SupabaseDB(), bucket=args.bucket)
        print(result)
        return 0 if result["status"] in {"waiting", "no_open_cycle", "already_submitted", "accepted"} else 1
    except Exception as exc:
        print(f"inference error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
