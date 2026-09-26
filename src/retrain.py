"""Snapshot + entrenamiento candidato + promoción controlada."""

from __future__ import annotations

import argparse
from typing import Any

import pandas as pd

from .snapshot import create_snapshot
from .db import SupabaseDB
from .train import train_and_register


def regression_status(db: SupabaseDB, *, drop_points: float = 3.0, minimum_accuracy: float = 80.0) -> dict[str, Any]:
    """Detecta degradación reciente usando métricas ya reveladas."""
    response = (
        db.client.table("metrics")
        .select("cycle_id,calculated_at,accuracy")
        .order("calculated_at")
        .execute()
    )
    frame = pd.DataFrame(response.data or [])
    if frame.empty:
        return {"should_retrain": False, "reason": "sin métricas reveladas"}
    frame["calculated_at"] = pd.to_datetime(frame["calculated_at"], utc=True)
    cycles = (
        frame.groupby("cycle_id")
        .agg(accuracy=("accuracy", "mean"), calculated_at=("calculated_at", "max"))
        .sort_values("calculated_at")
    )
    if len(cycles) < 4:
        return {"should_retrain": False, "reason": "menos de cuatro ciclos evaluados"}
    recent = float(cycles.tail(2)["accuracy"].mean())
    reference = float(cycles.iloc[-8:-2]["accuracy"].mean()) if len(cycles) >= 8 else float(cycles.iloc[:-2]["accuracy"].mean())
    drop = reference - recent
    should = recent < minimum_accuracy or drop >= drop_points
    return {
        "should_retrain": should,
        "reason": "accuracy bajo el mínimo" if recent < minimum_accuracy else "caída significativa" if should else "sin caída significativa",
        "recent_accuracy": recent,
        "reference_accuracy": reference,
        "drop_points": drop,
        "threshold_points": drop_points,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-bucket", default="dataset-snapshots")
    parser.add_argument("--model-bucket", default="model-artifacts")
    parser.add_argument("--origins", type=int, default=96)
    parser.add_argument("--only-if-regressed", action="store_true")
    parser.add_argument("--drop-points", type=float, default=3.0)
    args = parser.parse_args()
    db = SupabaseDB()
    if args.only_if_regressed:
        status = regression_status(db, drop_points=args.drop_points)
        print({"retrain_gate": status})
        if not status["should_retrain"]:
            return 0
    snapshot = create_snapshot(db, bucket=args.snapshot_bucket)
    result = train_and_register(
        snapshot["frame"],
        db=db,
        bucket=args.model_bucket,
        test_origins=args.origins,
        dataset_snapshot=snapshot,
    )
    print({"snapshot_id": snapshot["snapshot_id"], **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
