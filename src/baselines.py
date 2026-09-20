"""Baselines y validación temporal para demanda de Pulso TransMi."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from .metrics import official_accuracy


HORIZONS = (15, 30, 45, 60)
FREQUENCY = pd.Timedelta(minutes=15)
WEEK = pd.Timedelta(days=7)


def prepare_observations(frame: pd.DataFrame) -> pd.DataFrame:
    """Normaliza observations.csv o un DataFrame equivalente."""
    if "ts" in frame.columns:
        timestamp = "ts"
    elif "observed_at" in frame.columns:
        timestamp = "observed_at"
    elif "target_at" in frame.columns:
        timestamp = "target_at"
    else:
        timestamp = "observed_at"
    value = "value" if "value" in frame.columns else "demand"
    required = {"station_id", timestamp, value}
    if not required.issubset(frame.columns):
        raise ValueError(f"Faltan columnas: {sorted(required.difference(frame.columns))}")
    result = frame[["station_id", timestamp, value]].rename(
        columns={timestamp: "target_at", value: "value"}
    ).copy()
    result["target_at"] = pd.to_datetime(result["target_at"], utc=True)
    result["value"] = pd.to_numeric(result["value"], errors="raise")
    return result.sort_values(["station_id", "target_at"]).reset_index(drop=True)


def _lookup(history: pd.DataFrame, targets: pd.DataFrame, method: str, window: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for target in targets.itertuples(index=False):
        station_history = history[
            (history["station_id"] == target.station_id)
            & (history["target_at"] < target.target_at)
        ].sort_values("target_at")
        if station_history.empty:
            value = 0.0
        elif method == "naive":
            value = float(station_history.iloc[-1]["value"])
        elif method == "seasonal_naive":
            previous_week = station_history[station_history["target_at"] == target.target_at - WEEK]
            value = float(previous_week.iloc[-1]["value"]) if not previous_week.empty else float(station_history.iloc[-1]["value"])
        elif method == "moving_average":
            value = float(station_history.tail(window)["value"].mean())
        else:
            raise ValueError(f"Método desconocido: {method}")
        rows.append(
            {
                "station_id": target.station_id,
                "target_at": target.target_at,
                "value": max(0.0, value),
            }
        )
    return pd.DataFrame(rows)


def temporal_backtest(
    observations: pd.DataFrame,
    *,
    test_origins: int = 96,
    moving_window: int = 4,
) -> pd.DataFrame:
    """Evalúa con orígenes consecutivos y targets exclusivamente futuros."""
    history = prepare_observations(observations)
    timestamps = history["target_at"].drop_duplicates().sort_values()
    if len(timestamps) <= max(test_origins + 4, 4):
        raise ValueError("No hay suficiente historia para la validación temporal")

    last_origin = timestamps.iloc[-5]
    origins = timestamps[timestamps <= last_origin].tail(test_origins)
    methods: dict[str, Callable[[pd.DataFrame, pd.DataFrame, str, int], pd.DataFrame]] = {
        "naive": _lookup,
        "seasonal_naive": _lookup,
        "moving_average": _lookup,
    }
    result: list[pd.DataFrame] = []
    for origin in origins:
        for horizon_index, horizon in enumerate(HORIZONS, start=1):
            target_time = origin + horizon_index * FREQUENCY
            actual = history[history["target_at"] == target_time][
                ["station_id", "target_at", "value"]
            ].rename(columns={"value": "value"})
            if actual.empty:
                continue
            train = history[history["target_at"] <= origin]
            for method in methods:
                predictions = _lookup(train, actual, method, moving_window)
                result.append(
                    actual.assign(horizon_minutes=horizon, method=method).merge(
                        predictions,
                        on=["station_id", "target_at"],
                        suffixes=("_actual", "_prediction"),
                    )
                )

    if not result:
        raise ValueError("La validación temporal no produjo targets")
    return pd.concat(result, ignore_index=True)


def comparison_table(backtest: pd.DataFrame) -> pd.DataFrame:
    """Imprime y devuelve accuracy oficial agrupada por baseline y horizonte."""
    rows = []
    for (method, horizon), group in backtest.groupby(["method", "horizon_minutes"]):
        actuals = group[["station_id", "target_at", "value_actual"]].rename(columns={"value_actual": "value"})
        predictions = group[["station_id", "target_at", "value_prediction"]].rename(columns={"value_prediction": "value"})
        rows.append({"method": method, "horizon_minutes": horizon, "accuracy": official_accuracy(actuals, predictions)})
    table = pd.DataFrame(rows).sort_values(["method", "horizon_minutes"])
    print(table.to_string(index=False, formatters={"accuracy": "{:.4f}".format}))
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/starter/observations.csv"))
    parser.add_argument("--origins", type=int, default=96)
    args = parser.parse_args()
    observations = pd.read_csv(args.data, dtype={"station_id": "string"})
    comparison_table(temporal_backtest(observations, test_origins=args.origins))


if __name__ == "__main__":
    main()
