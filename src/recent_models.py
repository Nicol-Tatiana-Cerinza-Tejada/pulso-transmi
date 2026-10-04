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


def predict_seasonal(
    wide: pd.DataFrame,
    origin: pd.Timestamp,
    period: int,
    cycles: int = 1,
) -> pd.DataFrame:
    """Naive estacional: promedia el mismo punto de los ``cycles`` periodos previos.

    ``period`` está en intervalos de 15 minutos. Como el horizonte máximo
    (4 intervalos) es menor que cualquier periodo usado, todos los valores
    leídos son anteriores o iguales a ``origin``.
    """
    if period <= max(HORIZONS):
        raise ValueError("period debe ser mayor que el horizonte máximo")
    origin = pd.Timestamp(origin)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    frame = _wide_frame(wide).loc[:origin]
    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        target_at = origin + horizon * FREQUENCY
        lagged = [target_at - cycle * period * FREQUENCY for cycle in range(1, cycles + 1)]
        if any(timestamp not in frame.index for timestamp in lagged):
            continue
        values = frame.loc[lagged].mean(axis=0, skipna=False)
        for station, value in values.items():
            if np.isfinite(value):
                rows.append(
                    {
                        "kind": f"seasonal_{period}x{cycles}",
                        "station_id": str(station),
                        "horizon": horizon,
                        "prediction": max(0.0, float(value)),
                    }
                )
    return pd.DataFrame(rows, columns=["kind", "station_id", "horizon", "prediction"])


def detect_period(
    wide: pd.DataFrame,
    origin: pd.Timestamp,
    *,
    window_h: int = 24,
    min_period: int = 6,
    max_period: int = 48,
) -> int | None:
    """Estima el periodo dominante (en intervalos) con autocorrelación reciente.

    Usa solo datos hasta ``origin``. La ventana corta (24 h) se adapta en un
    día a un cambio de periodo; el ciclo diario lo cubre ``seasonal_1d``. Devuelve ``None`` si ninguna estación
    muestra un ciclo claro (autocorrelación media menor que 0,5).
    """
    origin = pd.Timestamp(origin)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    frame = _wide_frame(wide).loc[:origin]
    frame = frame.loc[frame.index > origin - pd.Timedelta(hours=window_h)].dropna(axis=1)
    if len(frame) < 2 * max_period or frame.empty:
        max_period = len(frame) // 2
    if max_period < min_period:
        return None
    centered = (frame - frame.mean()) / frame.std().replace(0, np.nan)
    centered = centered.dropna(axis=1)
    if centered.empty:
        return None
    scores: dict[int, float] = {}
    for lag in range(min_period, max_period + 1):
        score = float(centered.apply(lambda column: column.autocorr(lag)).mean())
        if np.isfinite(score):
            scores[lag] = score
    if not scores or max(scores.values()) < 0.5:
        return None
    # Los múltiplos del periodo tienen casi la misma autocorrelación; se
    # elige el periodo más corto cercano al máximo (el fundamental).
    best = max(scores.values())
    return min(lag for lag, score in scores.items() if score >= 0.9 * best)


def _recent_values(frame: pd.DataFrame, points: int) -> pd.DataFrame:
    return frame.tail(points).interpolate(limit_direction="both")


def predict_local_trend(
    wide: pd.DataFrame,
    origin: pd.Timestamp,
    points: int = 4,
    damping: float = 0.7,
) -> pd.DataFrame:
    """Último valor más la pendiente media reciente, amortiguada por horizonte.

    Útil cuando la demanda oscila con periodo irregular: sigue la fase actual
    sin depender de que el ciclo se repita igual.
    """
    origin = pd.Timestamp(origin)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    recent = _recent_values(_wide_frame(wide).loc[:origin], points + 1)
    if len(recent) < points + 1:
        return pd.DataFrame(columns=["kind", "station_id", "horizon", "prediction"])
    slope = recent.diff().iloc[1:].mean()
    last = recent.iloc[-1]
    rows = []
    for horizon in HORIZONS:
        factor = sum(damping**step for step in range(1, horizon + 1))
        for station in recent.columns:
            value = float(last[station] + slope[station] * factor)
            if np.isfinite(value):
                rows.append({"kind": f"trend_{points}_{damping}", "station_id": str(station), "horizon": horizon, "prediction": max(0.0, value)})
    return pd.DataFrame(rows, columns=["kind", "station_id", "horizon", "prediction"])


def predict_holt(
    wide: pd.DataFrame,
    origin: pd.Timestamp,
    alpha: float = 0.7,
    beta: float = 0.5,
    phi: float = 0.8,
    points: int = 24,
) -> pd.DataFrame:
    """Suavizado exponencial de Holt con tendencia amortiguada (ventana corta)."""
    origin = pd.Timestamp(origin)
    origin = origin.tz_localize("UTC") if origin.tzinfo is None else origin.tz_convert("UTC")
    recent = _recent_values(_wide_frame(wide).loc[:origin], points)
    if len(recent) < 3:
        return pd.DataFrame(columns=["kind", "station_id", "horizon", "prediction"])
    rows = []
    for station in recent.columns:
        values = recent[station].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            continue
        level, trend = values[0], values[1] - values[0]
        for value in values[1:]:
            previous = level
            level = alpha * value + (1 - alpha) * (level + phi * trend)
            trend = beta * (level - previous) + (1 - beta) * phi * trend
        for horizon in HORIZONS:
            factor = sum(phi**step for step in range(1, horizon + 1))
            rows.append({"kind": "holt", "station_id": str(station), "horizon": horizon, "prediction": max(0.0, float(level + trend * factor))})
    return pd.DataFrame(rows, columns=["kind", "station_id", "horizon", "prediction"])
