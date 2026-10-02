"""Modelos de ventana corta para adaptarse al drift entre estaciones.

Estos modelos se ajustan en memoria durante una inferencia. Solo reciben datos
con timestamp menor o igual al origen, por lo que no pueden leer el futuro.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


FREQUENCY = pd.Timedelta(minutes=15)
HORIZONS = (1, 2, 3, 4)
LAGS = tuple(range(8))
KINDS = ("cross_ar", "own_ar", "pooled_ar")


def _wide_frame(wide: pd.DataFrame) -> pd.DataFrame:
    frame = wide.copy()
    if "ts" in frame.columns:
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        frame = frame.set_index("ts")
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    if frame.empty:
        raise ValueError("wide no contiene observaciones")
    full_index = pd.date_range(frame.index.min(), frame.index.max(), freq=FREQUENCY)
    return frame.reindex(full_index).ffill()


def observations_wide(history: pd.DataFrame) -> pd.DataFrame:
    """Convierte observations normalizadas a una matriz temporal por estación."""
    required = {"station_id", "ts", "value"}
    if not required.issubset(history.columns):
        raise ValueError(f"Faltan columnas: {sorted(required - set(history.columns))}")
    frame = history[["station_id", "ts", "value"]].copy()
    frame["station_id"] = frame["station_id"].astype(str)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame.pivot_table(index="ts", columns="station_id", values="value", aggfunc="last").sort_index()


def _station_examples(
    frame: pd.DataFrame,
    origin: pd.Timestamp,
    window_h: int,
    horizon: int,
    station_index: int,
    kind: str,
) -> tuple[np.ndarray, np.ndarray]:
    stations = [str(value) for value in frame.columns]
    end = origin - horizon * FREQUENCY
    start = origin - pd.Timedelta(hours=window_h)
    rows: list[np.ndarray] = []
    targets: list[float] = []
    times = frame.index[(frame.index >= start) & (frame.index <= end)]
    for q in times:
        block = frame.loc[:q].tail(8)
        target_at = q + horizon * FREQUENCY
        if len(block) < 8 or block.isna().any().any() or target_at not in frame.index:
            continue
        values = block.to_numpy(dtype=float)
        y = float(frame.iloc[frame.index.get_loc(target_at), station_index])
        if not np.isfinite(y):
            continue
        if kind == "cross_ar":
            row = values[::-1].reshape(-1)
            scale = 1.0
        elif kind == "own_ar":
            row = values[::-1, station_index]
            scale = 1.0
        elif kind == "pooled_ar":
            scale = max(float(values[:, station_index].mean()), 1.0)
            row = values[::-1].reshape(-1) / scale
        else:
            raise ValueError(f"Modelo reciente desconocido: {kind}")
        rows.append(np.asarray(row, dtype=float))
        targets.append(y / scale)
    if not rows:
        return np.empty((0, 0)), np.empty((0,))
    return np.vstack(rows), np.asarray(targets, dtype=float)


def _fit_predictions(
    frame: pd.DataFrame,
    origin: pd.Timestamp,
    window_h: int,
    kind: str,
) -> list[dict[str, Any]]:
    stations = [str(value) for value in frame.columns]
    output: list[dict[str, Any]] = []
    current = frame.loc[:origin].tail(8)
    if len(current) < 8 or current.isna().any().any():
        return output
    current_values = current.to_numpy(dtype=float)
    current_scale = np.maximum(current_values.mean(axis=0), 1.0)
    for horizon in HORIZONS:
        if kind == "pooled_ar":
            examples = [_station_examples(frame, origin, window_h, horizon, index, kind) for index in range(len(stations))]
            available = [(x, y) for x, y in examples if len(y)]
            if not available:
                continue
            x_all = np.vstack([x for x, _ in available])
            y_all = np.concatenate([y for _, y in available])
            model = make_pipeline(StandardScaler(), Ridge(alpha=0.2))
            model.fit(x_all, y_all)
            for station_index, station in enumerate(stations):
                x_current = current_values[::-1].reshape(1, -1) / current_scale[station_index]
                prediction = float(model.predict(x_current)[0]) * current_scale[station_index]
                output.append({"kind": kind, "station_id": station, "horizon": horizon, "prediction": max(0.0, prediction)})
            continue
        for station_index, station in enumerate(stations):
            x_all, y = _station_examples(frame, origin, window_h, horizon, station_index, kind)
            if not len(y):
                continue
            model = make_pipeline(StandardScaler(), Ridge(alpha=0.2))
            model.fit(x_all, y)
            x_current = current_values[::-1].reshape(1, -1) if kind == "cross_ar" else current_values[::-1, station_index].reshape(1, -1)
            output.append({"kind": kind, "station_id": station, "horizon": horizon, "prediction": max(0.0, float(model.predict(x_current)[0]))})
    return output


def predict_recent(
    wide: pd.DataFrame,
    origin: pd.Timestamp,
    window_h: int,
    kinds: Iterable[str],
) -> pd.DataFrame:
    """Predice +15..+60 usando únicamente una ventana corta hasta origin."""
    origin = pd.Timestamp(origin)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    frame = _wide_frame(wide)
    frame = frame.loc[:origin]
    rows: list[dict[str, Any]] = []
    for kind in kinds:
        if kind not in KINDS:
            raise ValueError(f"Modelo reciente desconocido: {kind}")
        rows.extend(_fit_predictions(frame, origin, window_h, kind))
    return pd.DataFrame(rows, columns=["kind", "station_id", "horizon", "prediction"])
