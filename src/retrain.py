"""Snapshot + entrenamiento candidato + promoción controlada."""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .snapshot import create_snapshot
from .db import SupabaseDB
from .metrics import cycle_timestamps
from .train import train_and_register


def regression_status(db: SupabaseDB, *, drop_points: float = 3.0, minimum_accuracy: float = 85.0) -> dict[str, Any]:
    """Detecta degradación global o localizada usando métricas ya reveladas."""
    frame = pd.DataFrame(db.select_all("metrics", "cycle_id,station_id,calculated_at,accuracy,coverage"))
    if frame.empty:
        return {"should_retrain": False, "reason": "sin métricas reveladas"}
    frame["calculated_at"] = pd.to_datetime(frame["calculated_at"], utc=True, format="ISO8601")
    frame["cycle_at"] = cycle_timestamps(frame["cycle_id"])
    frame["station_id"] = frame["station_id"].astype(str)
    frame["coverage"] = pd.to_numeric(frame["coverage"], errors="coerce")
    # Una métrica con cobertura baja refleja targets ausentes; no debe
    # provocar que se promueva o entrene un modelo por un fallo operativo.
    frame = frame[frame["coverage"] >= 0.95].copy()
    if frame.empty:
        return {"should_retrain": False, "reason": "sin métricas con cobertura suficiente"}
    station_reasons: list[dict[str, Any]] = []
    for station_id, station_frame in frame.groupby("station_id"):
        cycles = (
            station_frame.groupby("cycle_id")
            .agg(accuracy=("accuracy", "mean"), cycle_at=("cycle_at", "max"))
            .reset_index()
            .sort_values(["cycle_at", "cycle_id"])
        )
        if len(cycles) < 4:
            continue
        recent = float(cycles.tail(2)["accuracy"].mean())
        reference = float(cycles.iloc[-8:-2]["accuracy"].mean()) if len(cycles) >= 8 else float(cycles.iloc[:-2]["accuracy"].mean())
        drop = reference - recent
        if recent < minimum_accuracy or drop >= drop_points:
            station_reasons.append(
                {
                    "station_id": station_id,
                    "recent_accuracy": recent,
                    "reference_accuracy": reference,
                    "drop_points": drop,
                }
            )

    latest_model_response = (
        db.client.table("model_versions")
        .select("created_at")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    latest_model_at = pd.Timestamp("1970-01-01", tz="UTC")
    if latest_model_response.data:
        latest_model_at = pd.Timestamp(latest_model_response.data[0]["created_at"])
        if latest_model_at.tzinfo is None:
            latest_model_at = latest_model_at.tz_localize("UTC")
        else:
            latest_model_at = latest_model_at.tz_convert("UTC")

    drift_response = (
        db.client.table("drift_signals")
        .select("id,station_id,signal_type,score,detected_at")
        .eq("status", "open")
        .in_("signal_type", ["performance_drift", "data_drift"])
        .execute()
    )
    open_drift = []
    for signal in drift_response.data or []:
        detected_at = pd.Timestamp(signal["detected_at"])
        if detected_at.tzinfo is None:
            detected_at = detected_at.tz_localize("UTC")
        if detected_at > latest_model_at:
            open_drift.append(signal)
    latest_metric_at = frame["calculated_at"].max()
    new_metric_evidence = latest_metric_at > latest_model_at
    should = bool((station_reasons or open_drift) and new_metric_evidence)
    reference_cycles = (
        frame.groupby("cycle_id")
        .agg(accuracy=("accuracy", "mean"), cycle_at=("cycle_at", "max"))
        .reset_index()
        .sort_values(["cycle_at", "cycle_id"])["accuracy"]
    )
    recent = float(reference_cycles.tail(2).mean())
    reference = float(reference_cycles.iloc[-8:-2].mean()) if len(reference_cycles) >= 8 else float(reference_cycles.iloc[:-2].mean())
    drop = reference - recent
    return {
        "should_retrain": should,
        "reason": "drift abierto" if open_drift else "caída localizada por estación" if station_reasons else "sin caída significativa",
        "new_metric_evidence": new_metric_evidence,
        "recent_accuracy": recent,
        "reference_accuracy": reference,
        "drop_points": drop,
        "threshold_points": drop_points,
        "affected_stations": station_reasons,
        "open_drift_signals": open_drift,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-bucket", default="dataset-snapshots")
    parser.add_argument("--model-bucket", default="model-artifacts")
    parser.add_argument("--origins", type=int, default=96)
    parser.add_argument("--only-if-regressed", action="store_true")
    parser.add_argument("--drop-points", type=float, default=3.0)
    parser.add_argument("--promotion-cooldown-hours", type=float, default=6.0)
    args = parser.parse_args()
    db = SupabaseDB()
    run_id = f"retrain-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    if args.only_if_regressed:
        status = regression_status(db, drop_points=args.drop_points)
        print({"retrain_gate": status})
        if not status["should_retrain"]:
            db.record_pipeline_event(
                {
                    "pipeline": "retrain",
                    "run_id": run_id,
                    "status": "skipped",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "details": {"reason": status["reason"], **status},
                }
            )
            return 0
    db.record_pipeline_event(
        {"pipeline": "retrain", "run_id": run_id, "status": "running", "details": {"trigger": "scheduled_or_manual"}}
    )
    try:
        snapshot = create_snapshot(db, bucket=args.snapshot_bucket)
        result = train_and_register(
            snapshot["frame"],
            db=db,
            bucket=args.model_bucket,
            test_origins=args.origins,
            dataset_snapshot=snapshot,
            promotion_cooldown_hours=args.promotion_cooldown_hours,
        )
    except Exception as exc:
        db.client.table("pipeline_events").update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)[:4000],
            }
        ).eq("run_id", run_id).execute()
        raise
    db.client.table("pipeline_events").update(
        {
            "status": "succeeded",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "model_version": result.get("version"),
            "details": {"snapshot_id": snapshot["snapshot_id"], "promoted": result.get("promoted")},
        }
    ).eq("run_id", run_id).execute()
    print({"snapshot_id": snapshot["snapshot_id"], **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
