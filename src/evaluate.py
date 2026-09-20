"""Evalúa targets revelados y consulta el leaderboard oficial."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .api_client import PulsoTransmiClient
from .db import SupabaseDB
from .metrics import align_targets, station_metrics


def fetch_all(db: SupabaseDB, table: str, columns: str, page_size: int = 1000) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            db.client.table(table)
            .select(columns)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = list(response.data or [])
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows)


def materialize_actuals(db: SupabaseDB, predictions: pd.DataFrame) -> int:
    """Copia a actuals los targets ya revelados en observations."""
    if predictions.empty:
        return 0
    observations = fetch_all(db, "observations", "station_id,ts,value")
    if observations.empty:
        return 0
    observations["ts"] = pd.to_datetime(observations["ts"], utc=True)
    predictions["target_at"] = pd.to_datetime(predictions["target_at"], utc=True)
    observed = observations.rename(columns={"ts": "target_at", "value": "actual_value"})
    joined = predictions.merge(observed, on=["station_id", "target_at"], how="inner")
    if joined.empty:
        return 0
    rows = joined.assign(
        value=joined["actual_value"],
        observed_at=joined["target_at"].map(lambda value: value.isoformat()),
    )[["cycle_id", "station_id", "target_at", "value", "observed_at"]]
    rows["target_at"] = rows["target_at"].map(lambda value: value.isoformat())
    db.upsert("actuals", rows.to_dict("records"), on_conflict="cycle_id,station_id,target_at")
    return len(rows)


def evaluate(db: SupabaseDB, api: PulsoTransmiClient) -> dict[str, Any]:
    predictions = fetch_all(
        db, "predictions", "cycle_id,station_id,target_at,value,submission_id"
    )
    materialized = materialize_actuals(db, predictions)
    actuals = fetch_all(db, "actuals", "cycle_id,station_id,target_at,value")
    if actuals.empty:
        return {"status": "no_actuals", "materialized_actuals": materialized, "cycles": 0}

    actuals["target_at"] = pd.to_datetime(actuals["target_at"], utc=True)
    if predictions.empty:
        predictions = pd.DataFrame(columns=["cycle_id", "station_id", "target_at", "value", "submission_id"])
    else:
        predictions["target_at"] = pd.to_datetime(predictions["target_at"], utc=True)
    records: list[dict[str, Any]] = []
    for cycle_id, cycle_actuals in actuals.groupby("cycle_id"):
        cycle_predictions = (
            predictions[predictions["cycle_id"] == cycle_id]
            if not predictions.empty
            else predictions
        )
        aligned = align_targets(cycle_actuals, cycle_predictions)
        details = station_metrics(aligned)
        prediction_keys = set(
            zip(cycle_predictions["station_id"], cycle_predictions["target_at"])
        ) if not cycle_predictions.empty else set()
        for row in details.to_dict("records"):
            station_id = row["station_id"]
            station_actuals = cycle_actuals[cycle_actuals["station_id"] == station_id]
            matched = sum(
                (station_id, target) in prediction_keys
                for target in station_actuals["target_at"]
            )
            records.append(
                {
                    "cycle_id": cycle_id,
                    "station_id": station_id,
                    "calculated_at": datetime.now(timezone.utc).isoformat(),
                    "accuracy": float(row["accuracy"]),
                    "wape": float(row["wape"]),
                    "coverage": matched / len(station_actuals) if len(station_actuals) else 0.0,
                    "predictions_count": matched,
                    "actuals_count": len(station_actuals),
                }
            )
    if records:
        db.upsert("metrics", records, on_conflict="cycle_id,station_id")

    leaderboards = {
        "cumulative": api.leaderboard(window="cumulative"),
        "rolling_24h": api.leaderboard(window="rolling_24h"),
    }
    print(f"Ciclos evaluados: {actuals['cycle_id'].nunique()}")
    print(f"Métricas guardadas: {len(records)}")
    print(f"Leaderboard cumulative: {leaderboards['cumulative']}")
    print(f"Leaderboard rolling_24h: {leaderboards['rolling_24h']}")
    return {
        "status": "evaluated",
        "materialized_actuals": materialized,
        "cycles": int(actuals["cycle_id"].nunique()),
        "leaderboards": leaderboards,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    try:
        with PulsoTransmiClient() as api:
            result = evaluate(SupabaseDB(), api)
        print(result)
        return 0
    except Exception as exc:
        print(f"evaluate error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
