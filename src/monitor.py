"""Monitorización de performance, data drift y operación."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from .db import SupabaseDB
from .evaluate import fetch_all


PERFORMANCE_ACCURACY_THRESHOLD = 80.0
PERFORMANCE_CONSECUTIVE_CYCLES = 3
DATA_PSI_THRESHOLD = 0.20
LOOKBACK_HOURS = 24


def psi(reference: pd.Series, recent: pd.Series, bins: int = 10) -> float:
    """Population Stability Index usando cuantiles de la referencia."""
    reference = pd.to_numeric(reference, errors="coerce").dropna().to_numpy(float)
    recent = pd.to_numeric(recent, errors="coerce").dropna().to_numpy(float)
    if not len(reference) or not len(recent):
        return 0.0
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0 if np.allclose(reference.mean(), recent.mean()) else float("inf")
    edges[0], edges[-1] = -np.inf, np.inf
    ref_pct = np.histogram(reference, bins=edges)[0].astype(float)
    recent_pct = np.histogram(recent, bins=edges)[0].astype(float)
    ref_pct = np.maximum(ref_pct / ref_pct.sum(), 1e-6)
    recent_pct = np.maximum(recent_pct / recent_pct.sum(), 1e-6)
    return float(np.sum((recent_pct - ref_pct) * np.log(recent_pct / ref_pct)))


def open_signal(db: SupabaseDB, signal_type: str) -> bool:
    response = (
        db.client.table("drift_signals")
        .select("id")
        .eq("signal_type", signal_type)
        .eq("status", "open")
        .limit(1)
        .execute()
    )
    return bool(response.data)


def write_signal(db: SupabaseDB, signal_type: str, *, score: float, reference: float | None, current: float | None, details: dict[str, Any]) -> bool:
    if open_signal(db, signal_type):
        return False
    db.insert(
        "drift_signals",
        {
            "signal_type": signal_type,
            "score": score,
            "reference_value": reference,
            "current_value": current,
            "details": details,
            "status": "open",
        },
    )
    return True


def performance_signal(db: SupabaseDB) -> bool:
    metrics = fetch_all(db, "metrics", "cycle_id,station_id,calculated_at,accuracy")
    if metrics.empty:
        return False
    metrics["calculated_at"] = pd.to_datetime(metrics["calculated_at"], utc=True)
    cycles = metrics.groupby("cycle_id").agg(
        accuracy=("accuracy", "mean"), calculated_at=("calculated_at", "max")
    ).sort_values("calculated_at")
    recent = cycles.tail(PERFORMANCE_CONSECUTIVE_CYCLES)
    if len(recent) < PERFORMANCE_CONSECUTIVE_CYCLES or not (recent["accuracy"] < PERFORMANCE_ACCURACY_THRESHOLD).all():
        return False
    return write_signal(
        db,
        "performance_drift",
        score=float(PERFORMANCE_ACCURACY_THRESHOLD - recent["accuracy"].iloc[-1]),
        reference=PERFORMANCE_ACCURACY_THRESHOLD,
        current=float(recent["accuracy"].iloc[-1]),
        details={"cycles": list(recent.index), "consecutive": PERFORMANCE_CONSECUTIVE_CYCLES},
    )


def data_signal(db: SupabaseDB) -> bool:
    observations = fetch_all(db, "observations", "station_id,ts,value")
    if observations.empty:
        return False
    observations["ts"] = pd.to_datetime(observations["ts"], utc=True)
    end = observations["ts"].max()
    recent_start = end - timedelta(hours=24)
    reference_start = recent_start - timedelta(days=28)
    reference = observations[(observations["ts"] >= reference_start) & (observations["ts"] < recent_start)]["value"]
    recent = observations[observations["ts"] >= recent_start]["value"]
    score = psi(reference, recent)
    if score < DATA_PSI_THRESHOLD:
        return False
    return write_signal(
        db,
        "data_drift",
        score=score,
        reference=0.0,
        current=score,
        details={"feature": "observations.value", "reference_days": 28, "recent_hours": 24, "threshold": DATA_PSI_THRESHOLD},
    )


def operational_signal(db: SupabaseDB) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    runs = fetch_all(db, "collector_runs", "id,started_at,status,error")
    failed_runs = 0
    if not runs.empty:
        runs["started_at"] = pd.to_datetime(runs["started_at"], utc=True)
        failed_runs = int(((runs["started_at"] >= cutoff) & (runs["status"] == "failed")).sum())
    try:
        receipts = fetch_all(db, "submission_receipts", "participant_id,cycle_id,status,created_at")
    except Exception as exc:
        if "submission_receipts" not in str(exc) or "PGRST205" not in str(exc):
            raise
        print("Aviso: submission_receipts aún no existe; se omiten submissions pendientes.")
        receipts = pd.DataFrame()
    predictions = fetch_all(db, "predictions", "cycle_id,created_at")
    pending = 0
    missing_cycles: set[str] = set()
    if not receipts.empty:
        receipts["created_at"] = pd.to_datetime(receipts["created_at"], utc=True)
        pending = int(((receipts["created_at"] >= cutoff) & (receipts["status"] == "pending")).sum())
    if not predictions.empty:
        predictions["created_at"] = pd.to_datetime(predictions["created_at"], utc=True)
        recent_cycles = set(predictions.loc[predictions["created_at"] >= cutoff, "cycle_id"])
        accepted_cycles = set(receipts.loc[receipts["status"] == "accepted", "cycle_id"]) if not receipts.empty else set()
        missing_cycles = recent_cycles - accepted_cycles
    total = failed_runs + pending + len(missing_cycles)
    if total == 0:
        return False
    return write_signal(
        db,
        "operational_failure",
        score=float(total),
        reference=0.0,
        current=float(total),
        details={
            "failed_collector_runs": failed_runs,
            "pending_submissions": pending,
            "cycles_without_accepted_submission": sorted(missing_cycles),
            "lookback_hours": LOOKBACK_HOURS,
        },
    )


def monitor(db: SupabaseDB) -> dict[str, bool]:
    result = {
        "performance_drift": performance_signal(db),
        "data_drift": data_signal(db),
        "operational_failure": operational_signal(db),
    }
    print(result)
    return result


def main() -> int:
    argparse.ArgumentParser().parse_args()
    try:
        monitor(SupabaseDB())
        return 0
    except Exception as exc:
        print(f"monitor error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
