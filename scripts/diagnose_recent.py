"""Diagnóstico de modelos recientes sobre observaciones ya reveladas.

Es de solo lectura: no crea submissions, no modifica modelos y nunca usa
observaciones posteriores al origen evaluado.
"""

from __future__ import annotations

import io
from pathlib import Path

import joblib
import pandas as pd

from src.db import SupabaseDB
from src.infer import predict_champion, selector_predict
from src.metrics import official_accuracy
from src.recent_models import observations_wide, predict_recent
from src.train import observations_from_supabase


HORIZONS = (15, 30, 45, 60)


def targets_for(history: pd.DataFrame, origin: pd.Timestamp) -> list[dict[str, str]]:
    stations = sorted(history["station_id"].astype(str).unique())
    return [
        {"station_id": station, "target_at": (origin + pd.Timedelta(minutes=horizon)).isoformat()}
        for station in stations
        for horizon in HORIZONS
        if origin + pd.Timedelta(minutes=horizon) in set(history["ts"])
    ]


def score(history: pd.DataFrame, targets: list[dict[str, str]], values: list[float]) -> float:
    actual = history.copy()
    actual["station_id"] = actual["station_id"].astype(str)
    lookup = {(str(row.station_id), row.ts): float(row.value) for row in actual.itertuples()}
    actual_rows = []
    prediction_rows = []
    for target, value in zip(targets, values, strict=True):
        key = (str(target["station_id"]), pd.Timestamp(target["target_at"]))
        if key not in lookup or pd.isna(value):
            continue
        actual_rows.append({"station_id": key[0], "target_at": key[1], "value": lookup[key]})
        prediction_rows.append({"station_id": key[0], "target_at": key[1], "value": value})
    return official_accuracy(pd.DataFrame(actual_rows), pd.DataFrame(prediction_rows))


def main() -> int:
    db = SupabaseDB()
    champion_row = (
        db.client.table("model_versions")
        .select("*")
        .eq("status", "champion")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data[0]
    )
    bucket, path = champion_row["artifact_path"].split("/", 1)
    artifact = joblib.load(io.BytesIO(db.download_artifact(bucket, path)))
    observations = observations_from_supabase(db)
    observations["station_id"] = observations["station_id"].astype(str)
    hourly = sorted(
        timestamp
        for timestamp in observations["ts"].drop_duplicates()
        if timestamp.minute == 0 and timestamp + pd.Timedelta(minutes=60) <= observations["ts"].max()
    )[-36:]
    rows: list[dict[str, object]] = []
    wide = observations_wide(observations)
    for origin in hourly:
        history = observations[observations["ts"] <= origin].copy()
        targets = targets_for(observations, origin)
        if not targets:
            continue
        champion = predict_champion(artifact, history, targets, origin)
        recent = predict_recent(wide, origin, 12, ["cross_ar", "own_ar", "pooled_ar"])
        recent_lookup = {
            (str(row.station_id), int(row.horizon) * 15, str(row.kind)): float(row.prediction)
            for row in recent.itertuples()
        }
        persist = []
        weekly = []
        for target in targets:
            series = history[history["station_id"] == target["station_id"]].sort_values("ts")["value"]
            persist.append(float(series.iloc[-1]))
            target_at = pd.Timestamp(target["target_at"])
            previous_week = history[
                (history["station_id"] == target["station_id"])
                & (history["ts"] == target_at - pd.Timedelta(days=7))
            ]["value"]
            weekly.append(float(previous_week.iloc[-1]) if not previous_week.empty else float("nan"))
        selector, _ = selector_predict(
            history,
            targets,
            origin,
            lambda h, t, c: predict_champion(artifact, h, t, c),
        )
        rows.append(
            {
                "origin": origin,
                "champion": score(observations, targets, champion),
                "persist": score(observations, targets, persist),
                "seasonal_naive": score(observations, targets, weekly),
                "cross_ar_12h": score(observations, targets, [recent_lookup.get((str(t["station_id"]), int((pd.Timestamp(t["target_at"]) - origin).total_seconds() // 60), "cross_ar"), float("nan")) for t in targets]),
                "own_ar_12h": score(observations, targets, [recent_lookup.get((str(t["station_id"]), int((pd.Timestamp(t["target_at"]) - origin).total_seconds() // 60), "own_ar"), float("nan")) for t in targets]),
                "pooled_ar_12h": score(observations, targets, [recent_lookup.get((str(t["station_id"]), int((pd.Timestamp(t["target_at"]) - origin).total_seconds() // 60), "pooled_ar"), float("nan")) for t in targets]),
                "selector": score(observations, targets, selector),
            }
        )
    result = pd.DataFrame(rows)
    print("Últimos 36 orígenes")
    print(result.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print("\nPromedio últimos 6")
    print(result.tail(6).drop(columns=["origin"]).mean().to_string(float_format=lambda value: f"{value:.2f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
