"""Métrica oficial de Pulso TransMi."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd


def _canonical(frame: pd.DataFrame, value_name: str) -> pd.DataFrame:
    value_column = value_name if value_name in frame.columns else "value"
    required = {"station_id", "target_at", value_column}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas: {sorted(missing)}")
    result = frame[["station_id", "target_at", value_column]].copy()
    result = result.rename(columns={value_column: value_name})
    if result.duplicated(["station_id", "target_at"]).any():
        raise ValueError(f"Hay targets duplicados en {value_name}")
    return result


def align_targets(
    actuals: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    station_ids: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Alinea targets y asigna 0 a toda predicción ausente."""
    actual = _canonical(actuals, "actual")
    predicted = _canonical(predictions, "prediction")
    merged = actual.merge(
        predicted,
        on=["station_id", "target_at"],
        how="left",
        validate="one_to_one",
    )
    merged["prediction"] = merged["prediction"].fillna(0.0)
    if station_ids is not None:
        expected = set(station_ids)
        merged = merged[merged["station_id"].isin(expected)].copy()
    return merged


def station_metrics(aligned: pd.DataFrame) -> pd.DataFrame:
    """Calcula WAPE y accuracy por estación."""
    required = {"station_id", "actual", "prediction"}
    if not required.issubset(aligned.columns):
        raise ValueError(f"Se requieren columnas: {sorted(required)}")

    frame = aligned.copy()
    frame["absolute_error"] = (frame["actual"] - frame["prediction"]).abs()

    def summarize(group: pd.DataFrame) -> pd.Series:
        actual_sum = float(group["actual"].sum())
        error_sum = float(group["absolute_error"].sum())
        if actual_sum == 0:
            wape = 0.0 if error_sum == 0 else float("inf")
        else:
            wape = error_sum / actual_sum
        accuracy = 0.0 if not np.isfinite(wape) else 100.0 * max(0.0, 1.0 - wape)
        return pd.Series(
            {
                "actual_sum": actual_sum,
                "absolute_error": error_sum,
                "wape": wape,
                "accuracy": accuracy,
                "targets": len(group),
            }
        )

    return frame.groupby("station_id", sort=True).apply(summarize, include_groups=False).reset_index()


def official_accuracy(
    actuals: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    station_ids: Iterable[str] | None = None,
) -> float:
    """Promedio no ponderado de accuracy por estación."""
    aligned = align_targets(actuals, predictions, station_ids=station_ids)
    if aligned.empty:
        return float("nan")
    return float(station_metrics(aligned)["accuracy"].mean())


def evaluate(
    actuals: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    station_ids: Iterable[str] | None = None,
) -> tuple[float, pd.DataFrame]:
    """Devuelve ``(accuracy_oficial, detalle_por_estacion)``."""
    aligned = align_targets(actuals, predictions, station_ids=station_ids)
    details = station_metrics(aligned)
    return (float(details["accuracy"].mean()), details)
