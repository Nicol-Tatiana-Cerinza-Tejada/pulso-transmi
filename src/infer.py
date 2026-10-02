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
from .recent_models import observations_wide, predict_recent
from .train import (
    LAG_STEPS,
    ROLLING_WINDOWS,
    SHORT_TREND_WINDOW,
    encode_features,
    encode_station_features,
    level_adjustment_factor,
)


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


def latest_value_at_or_before(series: pd.Series, timestamp: pd.Timestamp, *, station_id: str) -> float:
    """Devuelve el último dato disponible sin cruzar el corte temporal."""
    available = series.loc[:timestamp]
    if available.empty:
        raise RuntimeError(
            f"No hay observaciones anteriores a {timestamp.isoformat()} para station_id={station_id}"
        )
    return float(available.iloc[-1])


def routed_baseline_value(
    history: pd.DataFrame,
    station_id: str,
    target_at: pd.Timestamp,
    cutoff: pd.Timestamp,
    method: str,
) -> float:
    series = history[history["station_id"].astype(str) == station_id].set_index("ts")["value"].sort_index()
    available = series[series.index <= cutoff]
    if available.empty:
        return 0.0
    if method == "seasonal_naive":
        seasonal = series.get(target_at - pd.Timedelta(days=7))
        if seasonal is not None and math.isfinite(float(seasonal)):
            return max(0.0, float(seasonal))
    if method == "moving_average":
        return max(0.0, float(available.tail(4).mean()))
    return max(0.0, float(available.iloc[-1]))


def predict_champion(
    artifact: dict[str, Any],
    history: pd.DataFrame,
    targets: list[dict[str, Any]],
    cutoff: pd.Timestamp,
) -> np.ndarray:
    """Predice targets en el orden recibido usando el artefacto champion."""
    target_features, positions = build_target_features(history, targets, cutoff)
    predictions = np.full(len(targets), np.nan, dtype=float)
    station_models = artifact.get("station_models", {})
    station_feature_columns = artifact.get("station_feature_columns", {})
    level_adjustment_enabled = bool(
        artifact.get("metadata", {}).get("level_adjustment", {}).get("enabled", False)
    )
    for horizon, indexes in positions.items():
        if not indexes:
            continue
        if station_models:
            values: list[float] = []
            for index in indexes:
                station_id = str(targets[index]["station_id"])
                model = station_models.get(station_id, {}).get(str(horizon))
                columns = station_feature_columns.get(station_id, {}).get(str(horizon))
                if model is None or columns is None:
                    raise RuntimeError(f"El champion no tiene modelo para {station_id} +{horizon}")
                matrix, _ = encode_station_features(target_features.iloc[[index]], columns)
                values.append(float(model.predict(matrix)[0]))
            values = np.asarray(values, dtype=float)
        else:
            matrix, _ = encode_features(target_features.iloc[indexes], artifact["feature_columns"])
            values = np.asarray(artifact["models"][horizon].predict(matrix), dtype=float)
        for index, value in zip(indexes, values, strict=True):
            station_id = str(targets[index]["station_id"])
            route = artifact.get("metadata", {}).get("station_routes", {}).get(station_id, {}).get(str(horizon), "lightgbm")
            if route != "lightgbm":
                value = routed_baseline_value(
                    history, station_id, utc(targets[index]["target_at"]), cutoff, route
                )
            elif level_adjustment_enabled:
                value = float(value) * float(target_features.iloc[index]["level_factor"])
            if not math.isfinite(float(value)):
                raise RuntimeError(f"Predicción no finita para target {targets[index]}")
            predictions[index] = max(0.0, float(value))
    if not np.isfinite(predictions).all():
        raise RuntimeError("El champion no produjo una predicción para cada target")
    return predictions


def _recent_frame_predictions(
    history: pd.DataFrame,
    origin: pd.Timestamp,
    *,
    window_h: int,
    kind: str,
) -> dict[tuple[str, int], float]:
    frame = predict_recent(observations_wide(history), origin, window_h, [kind])
    return {
        (str(row.station_id), int(row.horizon) * 15): float(row.prediction)
        for row in frame.itertuples()
    }


def _persist_predictions(
    history: pd.DataFrame,
    origin: pd.Timestamp,
    stations: list[str],
) -> dict[tuple[str, int], float]:
    frame = history[history["ts"] <= origin]
    values: dict[tuple[str, int], float] = {}
    for station in stations:
        series = frame[frame["station_id"].astype(str) == station].sort_values("ts")["value"]
        if series.empty:
            continue
        for horizon in (15, 30, 45, 60):
            values[(station, horizon)] = max(0.0, float(series.iloc[-1]))
    return values


def selector_predict(
    history: pd.DataFrame,
    targets: list[dict[str, Any]],
    cutoff: pd.Timestamp,
    champion_predict: Any,
) -> np.ndarray:
    """Elige por estación el modelo con menor WAPE en seis orígenes sombra."""
    stations = sorted({str(target["station_id"]) for target in targets})
    configs = {
        "cross_ar_12h": (12, "cross_ar"),
        "cross_ar_24h": (24, "cross_ar"),
        "own_ar_12h": (12, "own_ar"),
        "pooled_ar_24h": (24, "pooled_ar"),
    }
    current: dict[str, dict[tuple[str, int], float]] = {
        "champion": {
            (str(target["station_id"]), int((utc(target["target_at"]) - cutoff).total_seconds() // 60)): float(value)
            for target, value in zip(targets, champion_predict(history, targets, cutoff), strict=True)
        },
        "persist": _persist_predictions(history, cutoff, stations),
    }
    for name, (window_h, kind) in configs.items():
        try:
            current[name] = _recent_frame_predictions(history, cutoff, window_h=window_h, kind=kind)
        except Exception as exc:
            print({"selector_model_error": name, "error": str(exc)[:500]})
            current[name] = {}

    scores: dict[str, dict[str, list[float]]] = {
        name: {station: [] for station in stations}
        for name in ["champion", "persist", *configs]
    }
    for hours_ago in range(1, 7):
        origin = cutoff - pd.Timedelta(hours=hours_ago)
        shadow_targets = [
            {"station_id": station, "target_at": (origin + pd.Timedelta(minutes=horizon)).isoformat()}
            for station in stations
            for horizon in (15, 30, 45, 60)
            if origin + pd.Timedelta(minutes=horizon) <= cutoff
        ]
        if not shadow_targets:
            continue
        actual_lookup = {
            (str(row.station_id), utc(row.ts)): float(row.value)
            for row in history[history["ts"] <= cutoff].itertuples()
        }
        candidates: dict[str, dict[tuple[str, int], float]] = {
            "champion": {
                (str(target["station_id"]), int((utc(target["target_at"]) - origin).total_seconds() // 60)): float(value)
                for target, value in zip(shadow_targets, champion_predict(history, shadow_targets, origin), strict=True)
            },
            "persist": _persist_predictions(history, origin, stations),
        }
        for name, (window_h, kind) in configs.items():
            try:
                candidates[name] = _recent_frame_predictions(history, origin, window_h=window_h, kind=kind)
            except Exception:
                candidates[name] = {}
        for target in shadow_targets:
            station = str(target["station_id"])
            target_at = utc(target["target_at"])
            horizon = int((target_at - origin).total_seconds() // 60)
            actual = actual_lookup.get((station, target_at))
            if actual is None:
                continue
            for name, candidate_values in candidates.items():
                prediction = candidate_values.get((station, horizon))
                if prediction is not None:
                    scores[name][station].append((abs(actual - prediction), actual))

    choices: dict[str, str] = {}
    for station in stations:
        ranked: list[tuple[float, str]] = []
        for name, station_scores in scores.items():
            pairs = station_scores[station]
            if pairs and sum(actual for _, actual in pairs) > 0:
                ranked.append((sum(error for error, _ in pairs) / sum(actual for _, actual in pairs), name))
        choices[station] = min(ranked)[1] if ranked else "champion"
    print({"selector_choices": choices})
    result: list[float] = []
    for target, champion_value in zip(targets, current["champion"].values(), strict=True):
        station = str(target["station_id"])
        horizon = int((utc(target["target_at"]) - cutoff).total_seconds() // 60)
        chosen = current.get(choices[station], {}).get((station, horizon), champion_value)
        result.append(max(0.0, float(chosen)))
    return np.asarray(result, dtype=float)


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
        hour = target_at.hour
        weekday = target_at.dayofweek
        features: dict[str, Any] = {
            "station_id": station_id,
            "target_at": target_at,
            "target_hour": hour,
            "target_weekday": weekday,
            "target_hour_sin": float(np.sin(2 * np.pi * hour / 24)),
            "target_hour_cos": float(np.cos(2 * np.pi * hour / 24)),
            "target_weekday_sin": float(np.sin(2 * np.pi * weekday / 7)),
            "target_weekday_cos": float(np.cos(2 * np.pi * weekday / 7)),
            "level_factor": level_adjustment_factor(series, cutoff),
        }
        for lag in LAG_STEPS:
            timestamp = cutoff - lag * FREQUENCY
            features[f"lag_{lag}"] = latest_value_at_or_before(
                series, timestamp, station_id=station_id
            )
        history_to_cutoff = series[series.index <= cutoff]
        for window in ROLLING_WINDOWS:
            if len(history_to_cutoff) < window:
                raise RuntimeError(f"Falta historia para rolling_mean_{window} de {station_id}")
            features[f"rolling_mean_{window}"] = float(history_to_cutoff.tail(window).mean())
        if len(history_to_cutoff) < SHORT_TREND_WINDOW:
            raise RuntimeError(f"Falta historia para rolling_mean_16 de {station_id}")
        recent_16 = history_to_cutoff.tail(SHORT_TREND_WINDOW)
        recent_96 = history_to_cutoff.tail(96)
        recent_4 = history_to_cutoff.tail(4)
        features["rolling_mean_16"] = float(recent_16.mean())
        features["rolling_std_96"] = float(recent_96.std(ddof=0))
        features["trend_4_96"] = float(recent_4.mean() - recent_96.mean())
        features["trend_16_96"] = float(recent_16.mean() - recent_96.mean())
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


def save_predictions(
    db: SupabaseDB,
    cycle_id: str,
    model_version: str,
    predictions: list[dict[str, Any]],
    cutoff: pd.Timestamp,
) -> None:
    rows = [
        {
            "cycle_id": cycle_id,
            "station_id": item["station_id"],
            "target_at": utc(item["target_at"]).isoformat(),
            "value": item["value"],
            "horizon_minutes": int((utc(item["target_at"]) - cutoff).total_seconds() // 60),
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
    expected_stations = {str(target["station_id"]) for target in targets}
    latest_by_station = history.groupby(history["station_id"].astype(str))["ts"].max()
    stale = sorted(
        station
        for station in expected_stations
        if station not in latest_by_station.index
        or latest_by_station[station] < cutoff - pd.Timedelta(minutes=30)
    )
    if stale:
        try:
            from .collector import collect_once

            refresh = collect_once(api, db, batch_size=250)
            history = load_observations_until(db, cutoff)
            latest_by_station = history.groupby(history["station_id"].astype(str))["ts"].max()
            stale = sorted(
                station
                for station in expected_stations
                if station not in latest_by_station.index
                or latest_by_station[station] < cutoff - pd.Timedelta(minutes=30)
            )
            print({"freshness_refresh": refresh, "stale_stations": stale})
        except Exception as exc:
            print({"freshness_refresh_error": str(exc)[:500], "stale_stations": stale})
    if stale:
        try:
            db.record_pipeline_event(
                {
                    "pipeline": "infer",
                    "run_id": f"freshness-{cycle_id}",
                    "cycle_id": cycle_id,
                    "model_version": champion["version"],
                    "status": "succeeded",
                    "error": "Datos con más de 30 minutos de atraso: " + ",".join(stale),
                    "details": {"stale_stations": stale, "cutoff": cutoff.isoformat()},
                }
            )
        except Exception:
            pass

    champion_values = predict_champion(artifact, history, targets, cutoff)
    try:
        selected_values = selector_predict(
            history,
            targets,
            cutoff,
            lambda shadow_history, shadow_targets, shadow_cutoff: predict_champion(
                artifact, shadow_history, shadow_targets, shadow_cutoff
            ),
        )
    except Exception as exc:
        print({"selector_error": str(exc)[:1000], "fallback": "champion"})
        selected_values = champion_values
    final_predictions = [
        {
            "station_id": str(target["station_id"]),
            "target_at": utc(target["target_at"]).isoformat(),
            "value": max(0.0, float(value)),
        }
        for target, value in zip(targets, selected_values, strict=True)
    ]
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
    save_predictions(db, cycle_id, model, final_predictions, cutoff)

    try:
        receipt = api.create_submission(payload, idempotency_key=key)
    except Exception as exc:
        request_id = getattr(exc, "request_id", None)
        db.client.table("submission_receipts").update(
            {
                "status": "failed",
                "error": str(exc)[:4000],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("participant_id", participant_id).eq("cycle_id", cycle_id).eq("attempt", attempt).execute()
        try:
            db.record_pipeline_event(
                {
                    "pipeline": "infer",
                    "run_id": f"infer-{participant_id}-{cycle_id}-{attempt}",
                    "cycle_id": cycle_id,
                    "model_version": model,
                    "status": "failed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc)[:4000],
                    "request_id": request_id,
                    "details": {"stage": "create_submission", "attempt": attempt},
                }
            )
        except Exception:
            pass
        raise
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
            # El estado waiting es una salida válida y no debe obligar a
            # configurar Supabase cuando todavía no hay ciclo que procesar.
            if api.clock().get("state") == "waiting":
                result = {"status": "waiting"}
                print(result)
                return 0
            db = SupabaseDB()
            result = run_inference(api, db, bucket=args.bucket)
        print(result)
        return 0 if result["status"] in {"waiting", "no_open_cycle", "already_submitted", "accepted"} else 1
    except Exception as exc:
        print(f"inference error: {exc}")
        try:
            db = locals().get("db")
            if db is None:
                db = SupabaseDB()
            db.record_pipeline_event(
                {
                    "pipeline": "infer",
                    "run_id": f"infer-failed-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
                    "status": "failed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc)[:4000],
                    "request_id": getattr(exc, "request_id", None),
                    "details": {"stage": "run_inference"},
                }
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
