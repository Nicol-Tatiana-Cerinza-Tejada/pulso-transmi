"""Monitorización de performance, data drift y operación."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from .db import SupabaseDB
from .evaluate import fetch_all
from .metrics import cycle_timestamps


PERFORMANCE_ACCURACY_THRESHOLD = 80.0
PERFORMANCE_CONSECUTIVE_CYCLES = 3
DATA_PSI_THRESHOLD = 0.20
LOOKBACK_HOURS = 24
COLLECTOR_STALE_MINUTES = 45
MIN_EVALUATION_COVERAGE = 0.95


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


def open_signal(db: SupabaseDB, signal_type: str, station_id: str | None = None) -> bool:
    response = (
        db.client.table("drift_signals")
        .select("id,station_id")
        .eq("signal_type", signal_type)
        .eq("status", "open")
        .execute()
    )
    return any(row.get("station_id") == station_id for row in (response.data or []))


def resolve_signals(
    db: SupabaseDB,
    signal_type: str,
    active_stations: set[str | None],
    *,
    evaluated_stations: set[str | None] | None = None,
) -> int:
    """Cierra señales abiertas cuya condición ya no se cumple.

    Sin este paso una señal queda abierta para siempre y ``write_signal`` no
    vuelve a registrar el mismo problema cuando reaparece.
    """
    response = (
        db.client.table("drift_signals")
        .select("id,station_id")
        .eq("signal_type", signal_type)
        .eq("status", "open")
        .execute()
    )
    stale_ids = [
        row["id"]
        for row in (response.data or [])
        if row.get("station_id") not in active_stations
        and (evaluated_stations is None or row.get("station_id") in evaluated_stations)
    ]
    if stale_ids:
        db.client.table("drift_signals").update({"status": "resolved"}).in_("id", stale_ids).execute()
    return len(stale_ids)


def write_signal(
    db: SupabaseDB,
    signal_type: str,
    *,
    station_id: str | None = None,
    score: float,
    reference: float | None,
    current: float | None,
    details: dict[str, Any],
    window_start: pd.Timestamp | None = None,
    window_end: pd.Timestamp | None = None,
) -> bool:
    if open_signal(db, signal_type, station_id):
        return False
    db.insert(
        "drift_signals",
        {
            "station_id": station_id,
            "signal_type": signal_type,
            "score": score,
            "reference_value": reference,
            "current_value": current,
            "window_start": window_start.isoformat() if window_start is not None else None,
            "window_end": window_end.isoformat() if window_end is not None else None,
            "details": details,
            "status": "open",
        },
    )
    return True


def performance_signal(db: SupabaseDB) -> bool:
    metrics = fetch_all(db, "metrics", "cycle_id,station_id,calculated_at,accuracy,coverage")
    if metrics.empty:
        return False
    metrics["calculated_at"] = pd.to_datetime(metrics["calculated_at"], utc=True, format="ISO8601")
    metrics["station_id"] = metrics["station_id"].astype(str)
    metrics["coverage"] = pd.to_numeric(metrics["coverage"], errors="coerce")
    # Cobertura baja significa entrega incompleta, no necesariamente mal modelo.
    # operational_signal reporta esa situación por separado.
    metrics = metrics[metrics["coverage"] >= MIN_EVALUATION_COVERAGE].copy()
    if metrics.empty:
        return False
    # calculated_at se reescribe en cada evaluación; el orden real es el del ciclo.
    metrics["cycle_at"] = cycle_timestamps(metrics["cycle_id"])
    detected = False
    active: set[str | None] = set()
    evaluated: set[str | None] = set()
    for station_id, station_metrics in metrics.groupby("station_id"):
        cycles = station_metrics.groupby("cycle_id").agg(
            accuracy=("accuracy", "mean"),
            calculated_at=("calculated_at", "max"),
            cycle_at=("cycle_at", "max"),
        ).reset_index().sort_values(["cycle_at", "cycle_id"]).set_index("cycle_id")
        recent = cycles.tail(PERFORMANCE_CONSECUTIVE_CYCLES)
        if len(recent) < PERFORMANCE_CONSECUTIVE_CYCLES:
            continue
        evaluated.add(station_id)
        if not (recent["accuracy"] < PERFORMANCE_ACCURACY_THRESHOLD).all():
            continue
        active.add(station_id)
        detected = write_signal(
            db,
            "performance_drift",
            station_id=station_id,
            score=float(PERFORMANCE_ACCURACY_THRESHOLD - recent["accuracy"].iloc[-1]),
            reference=PERFORMANCE_ACCURACY_THRESHOLD,
            current=float(recent["accuracy"].iloc[-1]),
            window_start=recent["calculated_at"].iloc[0],
            window_end=recent["calculated_at"].iloc[-1],
            details={"station_id": station_id, "cycles": list(recent.index), "consecutive": PERFORMANCE_CONSECUTIVE_CYCLES},
        ) or detected
    resolve_signals(db, "performance_drift", active, evaluated_stations=evaluated)
    return detected


def data_signal(db: SupabaseDB) -> bool:
    observations = fetch_all(db, "observations", "station_id,ts,value")
    if observations.empty:
        return False
    observations["ts"] = pd.to_datetime(observations["ts"], utc=True, format="ISO8601")
    end = observations["ts"].max()
    recent_start = end - timedelta(hours=24)
    reference_start = recent_start - timedelta(days=28)
    detected = False
    active: set[str | None] = set()
    evaluated: set[str | None] = set()
    for station_id, station_observations in observations.groupby("station_id"):
        reference = station_observations[(station_observations["ts"] >= reference_start) & (station_observations["ts"] < recent_start)]["value"]
        recent = station_observations[station_observations["ts"] >= recent_start]["value"]
        score = psi(reference, recent)
        evaluated.add(str(station_id))
        if score < DATA_PSI_THRESHOLD:
            continue
        active.add(str(station_id))
        detected = write_signal(
            db,
            "data_drift",
            station_id=str(station_id),
            score=score,
            reference=0.0,
            current=score,
            window_start=recent_start,
            window_end=end,
            details={"station_id": str(station_id), "feature": "observations.value", "reference_days": 28, "recent_hours": 24, "threshold": DATA_PSI_THRESHOLD},
        ) or detected
    resolve_signals(db, "data_drift", active, evaluated_stations=evaluated)
    return detected


def operational_signal(db: SupabaseDB) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    runs = fetch_all(db, "collector_runs", "id,started_at,finished_at,status,error")
    failed_runs = 0
    collector_stale = False
    if not runs.empty:
        runs["started_at"] = pd.to_datetime(runs["started_at"], utc=True, format="ISO8601")
        runs["finished_at"] = pd.to_datetime(runs["finished_at"], utc=True, format="ISO8601", errors="coerce")
        failed_runs = int(((runs["started_at"] >= cutoff) & (runs["status"] == "failed")).sum())
        latest_finished = runs["finished_at"].dropna().max()
        collector_stale = bool(
            pd.notna(latest_finished)
            and latest_finished < pd.Timestamp.now(tz="UTC") - timedelta(minutes=COLLECTOR_STALE_MINUTES)
        )
    else:
        collector_stale = True
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
        receipts["created_at"] = pd.to_datetime(receipts["created_at"], utc=True, format="ISO8601")
        pending = int(((receipts["created_at"] >= cutoff) & (receipts["status"] == "pending")).sum())
    if not predictions.empty:
        predictions["created_at"] = pd.to_datetime(predictions["created_at"], utc=True, format="ISO8601")
        recent_cycles = set(predictions.loc[predictions["created_at"] >= cutoff, "cycle_id"])
        accepted_cycles = set(receipts.loc[receipts["status"] == "accepted", "cycle_id"]) if not receipts.empty else set()
        missing_cycles = recent_cycles - accepted_cycles
    total = failed_runs + pending + len(missing_cycles) + int(collector_stale)
    if total == 0:
        resolve_signals(db, "operational_failure", set())
        return False
    return write_signal(
        db,
        "operational_failure",
        score=float(total),
        reference=0.0,
        current=float(total),
        details={
            "failed_collector_runs": failed_runs,
            "collector_stale": collector_stale,
            "collector_stale_minutes": COLLECTOR_STALE_MINUTES,
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
