"""Entrenamiento temporal de modelos LightGBM y promoción controlada."""

from __future__ import annotations

import argparse
import io
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .baselines import (
    FREQUENCY,
    HORIZONS,
    prepare_observations,
    temporal_backtest,
    validation_cycle_origins,
)
from .db import SupabaseDB
from .metrics import official_accuracy, station_metrics


LAG_STEPS = (1, 2, 4, 8, 96, 672)
ROLLING_WINDOWS = (4, 96, 672)
SHORT_TREND_WINDOW = 16  # 4 horas a frecuencia de 15 minutos
MIN_PROMOTION_ACCURACY = 85.0
MIN_PROMOTION_MARGIN = 0.5
MIN_STATION_ACCURACY = 85.0
MAX_STATION_REGRESSION = 3.0
RECENCY_HALF_LIFE_DAYS = 14.0
LEVEL_WINDOW_POINTS = 8  # 2 horas a frecuencia de 15 minutos
LEVEL_MIN_FACTOR = 0.5
LEVEL_MAX_FACTOR = 2.0
MODEL_VARIANTS = {
    "stable": {"n_estimators": 260, "learning_rate": 0.035, "num_leaves": 15, "min_child_samples": 30},
    "standard": {"n_estimators": 350, "learning_rate": 0.04, "num_leaves": 31, "min_child_samples": 20},
}
FEATURE_NAMES = [
    *(f"lag_{steps}" for steps in LAG_STEPS),
    *(f"rolling_mean_{window}" for window in ROLLING_WINDOWS),
    "rolling_mean_16",
    "rolling_std_96",
    "trend_4_96",
    "trend_16_96",
    "target_hour",
    "target_weekday",
    "target_hour_sin",
    "target_hour_cos",
    "target_weekday_sin",
    "target_weekday_cos",
    "station_id",
]


def git_commit() -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        return value if len(value) == 40 else None
    except (OSError, subprocess.CalledProcessError):
        return None


def validation_origins(observations: pd.DataFrame, test_origins: int) -> pd.DatetimeIndex:
    return validation_cycle_origins(observations, test_origins)


def _station_series(history: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        str(station): group.set_index("target_at")["value"].sort_index()
        for station, group in history.groupby("station_id")
    }


def level_adjustment_factor(series: pd.Series, origin: pd.Timestamp) -> float:
    """Estima un cambio de nivel reciente sin consultar datos posteriores."""
    available = series.loc[:origin].sort_index()
    recent = available.tail(LEVEL_WINDOW_POINTS)
    pairs: list[tuple[float, float]] = []
    for timestamp, actual in recent.items():
        expected = series.get(timestamp - pd.Timedelta(days=1))
        if expected is None or not np.isfinite(float(expected)) or float(expected) <= 0:
            continue
        pairs.append((float(actual), float(expected)))
    if len(pairs) < 4:
        return 1.0
    actual_mean = float(np.mean([pair[0] for pair in pairs]))
    expected_mean = float(np.mean([pair[1] for pair in pairs]))
    if expected_mean <= 0:
        return 1.0
    return float(np.clip(actual_mean / expected_mean, LEVEL_MIN_FACTOR, LEVEL_MAX_FACTOR))


def feature_rows(
    history: pd.DataFrame,
    *,
    horizon_minutes: int,
    origins: pd.DatetimeIndex | None = None,
    max_origin: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Construye features usando exclusivamente valores hasta cada origen."""
    prepared = prepare_observations(history)
    timestamps = prepared["target_at"].drop_duplicates().sort_values()
    if origins is None:
        last_allowed = timestamps.iloc[-1] - pd.Timedelta(minutes=horizon_minutes)
        origins = pd.DatetimeIndex(timestamps[timestamps <= last_allowed])
    if max_origin is not None:
        origins = pd.DatetimeIndex(origins[origins <= max_origin])
    series_by_station = _station_series(prepared)
    rows: list[dict[str, Any]] = []
    horizon = pd.Timedelta(minutes=horizon_minutes)

    for origin in origins:
        target_at = origin + horizon
        for station_id, series in series_by_station.items():
            if target_at not in series.index:
                continue
            values: dict[str, float] = {}
            complete = True
            for lag in LAG_STEPS:
                timestamp = origin - lag * FREQUENCY
                if timestamp not in series.index:
                    complete = False
                    break
                values[f"lag_{lag}"] = float(series.loc[timestamp])
            if not complete:
                continue
            history_to_origin = series[series.index <= origin]
            for window in ROLLING_WINDOWS:
                values[f"rolling_mean_{window}"] = float(history_to_origin.tail(window).mean())
            recent_16 = history_to_origin.tail(SHORT_TREND_WINDOW)
            values["rolling_mean_16"] = float(recent_16.mean())
            recent_96 = history_to_origin.tail(96)
            recent_4 = history_to_origin.tail(4)
            values["rolling_std_96"] = float(recent_96.std(ddof=0))
            values["trend_4_96"] = float(recent_4.mean() - recent_96.mean())
            values["trend_16_96"] = float(recent_16.mean() - recent_96.mean())
            hour = target_at.hour
            weekday = target_at.dayofweek
            rows.append(
                {
                    "station_id": station_id,
                    "origin_at": origin,
                    "target_at": target_at,
                    "target_hour": hour,
                    "target_weekday": weekday,
                    "target_hour_sin": float(np.sin(2 * np.pi * hour / 24)),
                    "target_hour_cos": float(np.cos(2 * np.pi * hour / 24)),
                    "target_weekday_sin": float(np.sin(2 * np.pi * weekday / 7)),
                    "target_weekday_cos": float(np.cos(2 * np.pi * weekday / 7)),
                    "actual": float(series.loc[target_at]),
                    "level_factor": level_adjustment_factor(series, origin),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def encode_features(frame: pd.DataFrame, columns: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    base_columns = [
        *(f"lag_{steps}" for steps in LAG_STEPS),
        *(f"rolling_mean_{window}" for window in ROLLING_WINDOWS),
        "rolling_mean_16",
        "rolling_std_96",
        "trend_4_96",
        "trend_16_96",
        "target_hour",
        "target_weekday",
        "target_hour_sin",
        "target_hour_cos",
        "target_weekday_sin",
        "target_weekday_cos",
        "station_id",
    ]
    matrix = pd.get_dummies(frame[base_columns], columns=["station_id"], dtype=float)
    if columns is not None:
        matrix = matrix.reindex(columns=columns, fill_value=0.0)
        return matrix, columns
    return matrix, list(matrix.columns)


def encode_station_features(frame: pd.DataFrame, columns: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Codifica features para un modelo exclusivo de una estación."""
    base_columns = [
        *(f"lag_{steps}" for steps in LAG_STEPS),
        *(f"rolling_mean_{window}" for window in ROLLING_WINDOWS),
        "rolling_mean_16",
        "rolling_std_96",
        "trend_4_96",
        "trend_16_96",
        "target_hour",
        "target_weekday",
        "target_hour_sin",
        "target_hour_cos",
        "target_weekday_sin",
        "target_weekday_cos",
    ]
    matrix = frame[base_columns].copy()
    if columns is not None:
        matrix = matrix.reindex(columns=columns, fill_value=0.0)
        return matrix, columns
    return matrix, list(matrix.columns)


def train_lightgbm(
    train_features: pd.DataFrame,
    target: pd.Series,
    *,
    sample_weight: np.ndarray | None = None,
    variant: str = "standard",
):
    try:
        from lightgbm import LGBMRegressor
    except ImportError as exc:
        raise RuntimeError("Instala LightGBM con: pip install 'lightgbm>=4,<5'") from exc
    config = MODEL_VARIANTS.get(variant)
    if config is None:
        raise ValueError(f"Variante LightGBM desconocida: {variant}")
    model = LGBMRegressor(
        objective="regression",
        **config,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(train_features, target, sample_weight=sample_weight)
    return model


def best_station_model(
    train: pd.DataFrame,
    valid: pd.DataFrame,
) -> tuple[Any, list[str], str, np.ndarray]:
    """Selecciona la variante con mejor accuracy temporal para una estación."""
    best: tuple[Any, list[str], str, np.ndarray] | None = None
    best_accuracy = -float("inf")
    for variant in MODEL_VARIANTS:
        matrix, columns = encode_station_features(train)
        model = train_lightgbm(
            matrix,
            train["actual"],
            sample_weight=recency_weights(train),
            variant=variant,
        )
        valid_matrix, _ = encode_station_features(valid, columns)
        prediction = np.maximum(0.0, np.asarray(model.predict(valid_matrix), dtype=float))
        actuals = valid[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"})
        predictions = valid[["station_id", "target_at"]].assign(value=prediction)
        accuracy = official_accuracy(actuals, predictions)
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best = (model, columns, variant, prediction)
    if best is None:
        raise RuntimeError("No se pudo seleccionar una variante por estación")
    return best


def station_balance_weights(frame: pd.DataFrame) -> np.ndarray:
    """Aproxima el peso no ponderado por estación de la métrica oficial."""
    totals = frame.groupby("station_id")["actual"].sum().clip(lower=1.0)
    weights = frame["station_id"].map(lambda station: 1.0 / float(totals[station])).to_numpy()
    return weights / weights.mean()


def recency_weights(frame: pd.DataFrame, *, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> np.ndarray:
    """Da más peso a cambios recientes sin eliminar toda la historia."""
    reference = pd.to_datetime(frame["origin_at"], utc=True).max()
    age_days = (reference - pd.to_datetime(frame["origin_at"], utc=True)).dt.total_seconds() / 86400.0
    weights = np.exp(-np.log(2.0) * age_days.clip(lower=0.0) / half_life_days)
    weights = np.clip(weights.to_numpy(dtype=float), 0.35, 1.0)
    return weights / weights.mean()


def training_weights(frame: pd.DataFrame) -> np.ndarray:
    """Combina balance por estación con adaptación gradual al drift."""
    return station_balance_weights(frame) * recency_weights(frame)


def _accuracy_rows(frame: pd.DataFrame, group_columns: list[str], *, prediction_column: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(group_columns):
        if not isinstance(keys, tuple):
            keys = (keys,)
        actuals = group[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"})
        predictions = group[["station_id", "target_at", prediction_column]].rename(columns={prediction_column: "value"})
        rows.append({**dict(zip(group_columns, keys)), "accuracy": official_accuracy(actuals, predictions)})
    return pd.DataFrame(rows)


def select_station_routes(
    model_rows: pd.DataFrame,
    baseline_rows: pd.DataFrame,
) -> dict[str, dict[str, str]]:
    """Elige la mejor estrategia por estación y horizonte en backtesting."""
    candidates = pd.concat(
        [
            model_rows[["station_id", "target_at", "actual", "prediction", "horizon_minutes", "method"]],
            baseline_rows[["station_id", "target_at", "actual", "prediction", "horizon_minutes", "method"]],
        ],
        ignore_index=True,
    )
    scores = _accuracy_rows(
        candidates,
        ["station_id", "horizon_minutes", "method"],
        prediction_column="prediction",
    )
    priority = {"lightgbm": 0, "seasonal_naive": 1, "moving_average": 2, "naive": 3}
    scores["priority"] = scores["method"].map(priority).fillna(99)
    scores = scores.sort_values(
        ["station_id", "horizon_minutes", "accuracy", "priority"],
        ascending=[True, True, False, True],
    )
    routes: dict[str, dict[str, str]] = {}
    for row in scores.drop_duplicates(["station_id", "horizon_minutes"]).to_dict("records"):
        routes.setdefault(str(row["station_id"]), {})[str(int(row["horizon_minutes"]))] = str(row["method"])
    return routes


def apply_station_routes(
    model_rows: pd.DataFrame,
    baseline_rows: pd.DataFrame,
    routes: dict[str, dict[str, str]],
) -> pd.DataFrame:
    """Sustituye la predicción del modelo por el baseline elegido cuando aplica."""
    result = model_rows.copy()
    for (station_id, horizon), group in result.groupby(["station_id", "horizon_minutes"]):
        method = routes.get(str(station_id), {}).get(str(int(horizon)), "lightgbm")
        if method == "lightgbm":
            continue
        lookup = (
            baseline_rows[
                (baseline_rows["station_id"] == station_id)
                & (baseline_rows["horizon_minutes"] == horizon)
                & (baseline_rows["method"] == method)
            ]
            .set_index("target_at")["prediction"]
        )
        for index in group.index:
            value = lookup.get(result.at[index, "target_at"])
            if value is not None:
                result.at[index, "prediction"] = float(value)
                result.at[index, "method"] = method
    return result


def validation_frame_for_artifact(
    artifact: dict[str, Any],
    validation_features: dict[int, pd.DataFrame],
) -> pd.DataFrame:
    """Devuelve predicciones de un artefacto aplicando sus rutas guardadas."""
    scored: list[pd.DataFrame] = []
    routes = artifact.get("metadata", {}).get("station_routes", {})
    station_models = artifact.get("station_models", {})
    station_columns = artifact.get("station_feature_columns", {})
    level_adjustment_enabled = bool(
        artifact.get("metadata", {}).get("level_adjustment", {}).get("enabled", False)
    )
    for horizon, valid in validation_features.items():
        if station_models:
            values = []
            for _, row in valid.iterrows():
                station_id = str(row["station_id"])
                model = station_models.get(station_id, {}).get(str(horizon))
                columns = station_columns.get(station_id, {}).get(str(horizon))
                if model is None or columns is None:
                    raise RuntimeError(f"Artefacto sin modelo para estación {station_id}, +{horizon}")
                matrix, _ = encode_station_features(pd.DataFrame([row]), columns)
                values.append(float(model.predict(matrix)[0]))
            values = np.maximum(0.0, np.asarray(values, dtype=float))
        else:
            matrix, _ = encode_features(valid, artifact["feature_columns"])
            values = np.maximum(0.0, np.asarray(artifact["models"][horizon].predict(matrix), dtype=float))
        frame = valid[["station_id", "target_at", "actual", "lag_672", "rolling_mean_4", "level_factor"]].assign(prediction=values)
        if level_adjustment_enabled:
            frame["prediction"] = frame["prediction"] * frame["level_factor"]
        for index, row in frame.iterrows():
            method = routes.get(str(row["station_id"]), {}).get(str(horizon), "lightgbm")
            if method == "seasonal_naive":
                frame.at[index, "prediction"] = float(row["lag_672"])
            elif method == "moving_average":
                frame.at[index, "prediction"] = float(row["rolling_mean_4"])
        scored.append(frame[["station_id", "target_at", "actual", "prediction"]])
    return pd.concat(scored, ignore_index=True)


def artifact_validation_accuracy(
    artifact: dict[str, Any],
    validation_features: dict[int, pd.DataFrame],
) -> float:
    """Evalúa un artefacto en los mismos ciclos y con la métrica oficial."""
    frame = validation_frame_for_artifact(artifact, validation_features)
    actuals = frame[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"})
    predictions = frame[["station_id", "target_at", "prediction"]].rename(columns={"prediction": "value"})
    return official_accuracy(actuals, predictions)


def station_regression_guardrail(
    candidate_frame: pd.DataFrame,
    champion_artifact: dict[str, Any],
    validation_features: dict[int, pd.DataFrame],
    *,
    tolerance: float = MAX_STATION_REGRESSION,
) -> dict[str, Any]:
    """Evita mejorar el promedio a costa de romper una estación."""
    candidate_scores = _accuracy_rows(
        candidate_frame, ["station_id"], prediction_column="prediction"
    ).rename(columns={"accuracy": "candidate_accuracy"})
    champion_frame = validation_frame_for_artifact(champion_artifact, validation_features)
    champion_scores = _accuracy_rows(
        champion_frame, ["station_id"], prediction_column="prediction"
    ).rename(columns={"accuracy": "champion_accuracy"})
    comparison = candidate_scores.merge(champion_scores, on="station_id", how="inner")
    comparison["delta_accuracy"] = (
        comparison["candidate_accuracy"] - comparison["champion_accuracy"]
    )
    worst = float(comparison["delta_accuracy"].min()) if not comparison.empty else 0.0
    return {
        "passed": bool(worst >= -tolerance),
        "tolerance_points": tolerance,
        "worst_station_delta": worst,
        "regressions": comparison.loc[
            comparison["delta_accuracy"] < -tolerance,
            ["station_id", "candidate_accuracy", "champion_accuracy", "delta_accuracy"],
        ].to_dict("records"),
    }


def frame_regression_guardrail(
    candidate_frame: pd.DataFrame,
    reference_frame: pd.DataFrame,
    *,
    tolerance: float = MAX_STATION_REGRESSION,
) -> dict[str, Any]:
    """Compara el candidato contra predicciones realmente enviadas."""
    candidate_scores = _accuracy_rows(
        candidate_frame, ["station_id"], prediction_column="prediction"
    ).rename(columns={"accuracy": "candidate_accuracy"})
    reference_scores = _accuracy_rows(
        reference_frame, ["station_id"], prediction_column="prediction"
    ).rename(columns={"accuracy": "production_accuracy"})
    comparison = candidate_scores.merge(reference_scores, on="station_id", how="inner")
    comparison["delta_accuracy"] = comparison["candidate_accuracy"] - comparison["production_accuracy"]
    worst = float(comparison["delta_accuracy"].min()) if not comparison.empty else 0.0
    return {
        "passed": bool(worst >= -tolerance),
        "tolerance_points": tolerance,
        "worst_station_delta": worst,
        "regressions": comparison.loc[
            comparison["delta_accuracy"] < -tolerance,
            ["station_id", "candidate_accuracy", "production_accuracy", "delta_accuracy"],
        ].to_dict("records"),
    }


def recent_production_comparison(
    db: SupabaseDB,
    candidate_frame: pd.DataFrame,
) -> dict[str, Any]:
    """Compara con submissions aceptadas cuyos targets ya fueron revelados."""
    predictions = pd.DataFrame(
        db.client.table("predictions")
        .select("cycle_id,station_id,target_at,value,submission_id")
        .execute()
        .data
        or []
    )
    actuals = pd.DataFrame(
        db.client.table("actuals")
        .select("cycle_id,station_id,target_at,value")
        .execute()
        .data
        or []
    )
    if predictions.empty or actuals.empty:
        return {"available": False, "reason": "sin submissions y actuals suficientes"}
    predictions = predictions[predictions["submission_id"].notna()].copy()
    if predictions.empty:
        return {"available": False, "reason": "sin submissions aceptadas"}
    for frame in (predictions, actuals):
        frame["station_id"] = frame["station_id"].astype(str)
        frame["target_at"] = pd.to_datetime(frame["target_at"], utc=True)
    production = predictions.merge(
        actuals,
        on=["cycle_id", "station_id", "target_at"],
        suffixes=("_prediction", "_actual"),
    )
    if production.empty:
        return {"available": False, "reason": "sin targets revelados de submissions"}
    end = production["target_at"].max()
    production = production[production["target_at"] >= end - pd.Timedelta(hours=24)].copy()
    candidate = candidate_frame.copy()
    candidate["station_id"] = candidate["station_id"].astype(str)
    candidate["target_at"] = pd.to_datetime(candidate["target_at"], utc=True)
    keys = production[["station_id", "target_at"]].drop_duplicates()
    candidate = candidate.merge(keys, on=["station_id", "target_at"], how="inner")
    if candidate.empty:
        return {"available": False, "reason": "el backtest no se solapa con la ventana enviada"}
    production_frame = production[["station_id", "target_at", "value_actual", "value_prediction"]].rename(
        columns={"value_actual": "actual", "value_prediction": "prediction"}
    )
    candidate_frame = candidate[["station_id", "target_at", "actual", "prediction"]]
    actual_table = candidate_frame[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"})
    candidate_table = candidate_frame[["station_id", "target_at", "prediction"]].rename(columns={"prediction": "value"})
    production_actual = production_frame[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"})
    production_prediction = production_frame[["station_id", "target_at", "prediction"]].rename(columns={"prediction": "value"})
    return {
        "available": True,
        "window_end": end.isoformat(),
        "targets": len(candidate_frame),
        "candidate_accuracy": official_accuracy(actual_table, candidate_table),
        "production_accuracy": official_accuracy(production_actual, production_prediction),
        "production_frame": production_frame,
        "candidate_frame": candidate_frame,
    }


def paired_bootstrap_improvement(
    candidate_frame: pd.DataFrame,
    champion_artifact: dict[str, Any],
    validation_features: dict[int, pd.DataFrame],
    *,
    iterations: int = 2000,
) -> dict[str, Any]:
    """Estima si el candidato mejora al champion en las mismas estaciones.

    Se remuestrean estaciones completas para respetar la métrica oficial, que
    da el mismo peso a cada estación. Es una prueba conservadora: solo se
    considera significativa si el límite inferior del intervalo del delta es
    mayor que cero.
    """
    champion_frame = validation_frame_for_artifact(champion_artifact, validation_features)
    candidate = candidate_frame[["station_id", "target_at", "actual", "prediction"]].copy()
    champion = champion_frame.rename(columns={"prediction": "champion_prediction"})
    paired = candidate.merge(
        champion[["station_id", "target_at", "champion_prediction"]],
        on=["station_id", "target_at"],
        validate="one_to_one",
    )
    candidate_metrics = station_metrics(paired.rename(columns={"prediction": "prediction"}))
    champion_metrics = station_metrics(
        paired.rename(columns={"champion_prediction": "prediction"})
    )
    station_scores = candidate_metrics[["station_id", "accuracy"]].merge(
        champion_metrics[["station_id", "accuracy"]],
        on="station_id",
        suffixes=("_candidate", "_champion"),
    )
    delta = station_scores["accuracy_candidate"] - station_scores["accuracy_champion"]
    rng = np.random.default_rng(42)
    station_count = len(station_scores)
    if station_count == 0:
        raise RuntimeError("No hay estaciones para comparar candidate y champion")
    samples = rng.integers(0, station_count, size=(iterations, station_count))
    boot = delta.to_numpy()[samples].mean(axis=1)
    low, high = np.quantile(boot, [0.025, 0.975])
    return {
        "delta_accuracy": float(delta.mean()),
        "confidence_low": float(low),
        "confidence_high": float(high),
        "iterations": iterations,
        "station_count": station_count,
        "significant": bool(low > 0.0),
    }


def train_and_validate(
    observations: pd.DataFrame,
    *,
    test_origins: int = 96,
) -> tuple[
    dict[int, Any],
    pd.DataFrame,
    float,
    float,
    list[str],
    pd.Timestamp,
    dict[int, pd.DataFrame],
    dict[int, pd.DataFrame],
    pd.DataFrame,
    dict[str, dict[str, str]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, list[str]]],
    dict[str, dict[str, str]],
]:
    """Entrena por horizonte y valida en ciclos horarios completos."""
    origins = validation_origins(observations, test_origins)
    first_validation_origin = origins.min()
    prepared = prepare_observations(observations)
    train_cutoff = first_validation_origin - FREQUENCY
    models: dict[int, Any] = {}
    smoke_inputs: dict[int, pd.DataFrame] = {}
    validation_rows: list[pd.DataFrame] = []
    validation_features: dict[int, pd.DataFrame] = {}
    feature_columns: list[str] = []
    station_models: dict[str, dict[str, Any]] = {}
    station_feature_columns: dict[str, dict[str, list[str]]] = {}
    station_model_variants: dict[str, dict[str, str]] = {}

    baseline_raw = temporal_backtest(observations, test_origins=test_origins)
    baseline_for_scoring = baseline_raw.rename(
        columns={"value_actual": "actual", "value_prediction": "prediction"}
    )
    baseline_summary = _accuracy_rows(
        baseline_for_scoring,
        ["method", "horizon_minutes"],
        prediction_column="prediction",
    )
    baseline_official = _accuracy_rows(
        baseline_for_scoring,
        ["method"],
        prediction_column="prediction",
    )

    for horizon in HORIZONS:
        # El target de una fila de entrenamiento también debe estar dentro del
        # corte; así no usamos etiquetas futuras aunque sus features sean pasadas.
        # Cada modelo puede usar datos hasta el último origen cuyo target siga
        # dentro del corte. Antes se restaba el horizonte máximo para todos,
        # descartando innecesariamente hasta una hora de entrenamiento.
        train_origin_max = train_cutoff - horizon * FREQUENCY
        train = feature_rows(prepared, horizon_minutes=horizon, max_origin=train_origin_max)
        valid = feature_rows(prepared, horizon_minutes=horizon, origins=origins)
        if train.empty or valid.empty:
            raise ValueError(f"No hay features suficientes para horizonte +{horizon}")
        # Se conserva un modelo global como fallback para artefactos y datos
        # incompletos, pero la predicción principal se entrena por estación.
        train_matrix, feature_columns = encode_features(train)
        valid_matrix, _ = encode_features(valid, feature_columns)
        model = train_lightgbm(train_matrix, train["actual"], sample_weight=training_weights(train))
        prediction = np.maximum(0.0, np.asarray(model.predict(valid_matrix), dtype=float))
        for station_id in sorted(str(value) for value in train["station_id"].unique()):
            station_train = train[train["station_id"].astype(str) == station_id]
            station_valid = valid[valid["station_id"].astype(str) == station_id]
            if station_train.empty or station_valid.empty:
                continue
            station_model, station_columns, variant, station_prediction = best_station_model(
                station_train, station_valid
            )
            station_models.setdefault(station_id, {})[str(horizon)] = station_model
            station_feature_columns.setdefault(station_id, {})[str(horizon)] = station_columns
            station_model_variants.setdefault(station_id, {})[str(horizon)] = variant
            valid_indexes = station_valid.index.to_numpy()
            for local_index, global_index in enumerate(valid_indexes):
                prediction[valid.index.get_loc(global_index)] = station_prediction[local_index]
        if len(prediction) != len(valid) or not np.isfinite(prediction).all():
            raise RuntimeError(f"Inferencia inválida para horizonte +{horizon}")
        prediction = prediction * valid["level_factor"].to_numpy(dtype=float)
        validation_rows.append(
            valid[["station_id", "target_at", "actual"]].assign(
                horizon_minutes=horizon,
                method="lightgbm",
                prediction=prediction,
            )
        )
        models[horizon] = model
        smoke_inputs[horizon] = valid_matrix.iloc[[0]]
        validation_features[horizon] = valid

    model_rows = pd.concat(validation_rows, ignore_index=True)
    baseline_for_scoring = baseline_for_scoring[
        ["station_id", "target_at", "actual", "prediction", "horizon_minutes", "method"]
    ]
    station_routes = select_station_routes(model_rows, baseline_for_scoring)
    model_rows = apply_station_routes(model_rows, baseline_for_scoring, station_routes)
    model_summary = _accuracy_rows(model_rows, ["horizon_minutes"], prediction_column="prediction")
    model_summary["method"] = "lightgbm"
    model_summary = model_summary[["method", "horizon_minutes", "accuracy"]]
    comparison = pd.concat(
        [baseline_summary[["method", "horizon_minutes", "accuracy"]], model_summary],
        ignore_index=True,
    ).sort_values(["method", "horizon_minutes"])
    print(comparison.to_string(index=False, formatters={"accuracy": "{:.4f}".format}))

    best_baseline = float(baseline_official["accuracy"].max())
    validation_frame = model_rows[["station_id", "target_at", "actual", "prediction"]]
    model_accuracy = official_accuracy(
        validation_frame[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"}),
        validation_frame[["station_id", "target_at", "prediction"]].rename(columns={"prediction": "value"}),
    )
    print("Accuracy oficial agrupada en los ciclos de validación: " f"{model_accuracy:.4f}")
    print("Accuracy oficial del mejor baseline: " f"{best_baseline:.4f}")

    # Tras medir fuera de muestra, reentrenamos los artefactos de producción
    # con todo el histórico disponible. La validación anterior sigue intacta.
    data_cutoff = pd.Timestamp(prepared["target_at"].max())
    production_models: dict[int, Any] = {}
    production_smoke_inputs: dict[int, pd.DataFrame] = {}
    production_station_models: dict[str, dict[str, Any]] = {}
    production_station_columns: dict[str, dict[str, list[str]]] = {}
    for horizon in HORIZONS:
        production_train = feature_rows(
            prepared,
            horizon_minutes=horizon,
            max_origin=data_cutoff - horizon * FREQUENCY,
        )
        if production_train.empty:
            raise ValueError(f"No hay datos de producción para horizonte +{horizon}")
        production_matrix, production_columns = encode_features(production_train)
        production_models[horizon] = train_lightgbm(
            production_matrix,
            production_train["actual"],
            sample_weight=training_weights(production_train),
        )
        production_smoke_inputs[horizon] = production_matrix.iloc[[-1]]
        if production_columns != feature_columns:
            raise RuntimeError("Las columnas de features cambian entre horizontes")
        for station_id in sorted(str(value) for value in production_train["station_id"].unique()):
            station_train = production_train[production_train["station_id"].astype(str) == station_id]
            station_matrix, station_columns = encode_station_features(station_train)
            production_station_models.setdefault(station_id, {})[str(horizon)] = train_lightgbm(
                station_matrix,
                station_train["actual"],
                sample_weight=recency_weights(station_train),
                variant=station_model_variants.get(station_id, {}).get(str(horizon), "standard"),
            )
            production_station_columns.setdefault(station_id, {})[str(horizon)] = station_columns

    return (
        production_models,
        comparison,
        model_accuracy,
        best_baseline,
        feature_columns,
        data_cutoff,
        production_smoke_inputs,
        validation_features,
        model_rows,
        station_routes,
        production_station_models,
        production_station_columns,
        station_model_variants,
    )


def artifact_bytes(
    models: dict[int, Any],
    feature_columns: list[str],
    metadata: dict[str, Any],
    *,
    station_models: dict[str, dict[str, Any]] | None = None,
    station_feature_columns: dict[str, dict[str, list[str]]] | None = None,
) -> bytes:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError("Instala joblib para serializar el artefacto") from exc
    payload = {
        "models": models,
        "feature_columns": feature_columns,
        "metadata": metadata,
        "station_models": station_models or {},
        "station_feature_columns": station_feature_columns or {},
    }
    buffer = io.BytesIO()
    joblib.dump(payload, buffer, compress=3)
    return buffer.getvalue()


def observations_from_supabase(db: SupabaseDB, page_size: int = 1000) -> pd.DataFrame:
    """Descarga el histórico persistido por el collector, paginado y ordenado."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            db.client.table("observations")
            .select("station_id,ts,value")
            .order("ts")
            .order("station_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = list(response.data or [])
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    if not rows:
        raise RuntimeError("Supabase no tiene observations para entrenar")
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame


def train_and_register(
    observations: pd.DataFrame,
    *,
    db: SupabaseDB | None = None,
    bucket: str = "model-artifacts",
    test_origins: int = 96,
    dataset_snapshot: dict[str, Any] | None = None,
    promotion_cooldown_hours: float = 6.0,
) -> dict[str, Any]:
    db = db or SupabaseDB()
    (
        models,
        comparison,
        model_accuracy,
        best_baseline,
        feature_columns,
        data_cutoff,
        smoke_inputs,
        validation_features,
        validation_frame,
        station_routes,
        station_models,
        station_feature_columns,
        station_model_variants,
    ) = train_and_validate(observations, test_origins=test_origins)
    smoke_predictions = [models[horizon].predict(smoke_inputs[horizon]) for horizon in HORIZONS]
    smoke_ok = all(
        len(prediction) == 1 and bool(np.isfinite(prediction[0])) and prediction[0] >= 0
        for prediction in smoke_predictions
    )
    if not smoke_ok:
        raise RuntimeError("La inferencia de prueba no produjo exactamente una predicción")

    current_champion_response = (
        db.client.table("model_versions")
        .select("version,metric,artifact_path")
        .eq("status", "champion")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    current_champion = current_champion_response.data[0] if current_champion_response.data else None
    champion_same_window_accuracy: float | None = None
    champion_evaluation_error: str | None = None
    live_comparison = recent_production_comparison(db, validation_frame)
    live_guardrail: dict[str, Any] = {
        "passed": True,
        "tolerance_points": MAX_STATION_REGRESSION,
        "worst_station_delta": None,
        "regressions": [],
    }
    if live_comparison.get("available"):
        live_guardrail = frame_regression_guardrail(
            live_comparison["candidate_frame"], live_comparison["production_frame"]
        )
    station_guardrail: dict[str, Any] = {
        "passed": True,
        "tolerance_points": MAX_STATION_REGRESSION,
        "worst_station_delta": None,
        "regressions": [],
    }
    significance: dict[str, Any] = {
        "significant": current_champion is None,
        "delta_accuracy": None,
        "confidence_low": None,
        "confidence_high": None,
    }
    if current_champion:
        try:
            import joblib

            champion_bucket, champion_path = current_champion["artifact_path"].split("/", 1)
            champion_artifact = joblib.load(
                io.BytesIO(db.download_artifact(champion_bucket, champion_path))
            )
            champion_same_window_accuracy = artifact_validation_accuracy(
                champion_artifact, validation_features
            )
            significance = paired_bootstrap_improvement(
                validation_frame, champion_artifact, validation_features
            )
            station_guardrail = station_regression_guardrail(
                validation_frame, champion_artifact, validation_features
            )
        except Exception as exc:
            champion_evaluation_error = str(exc)[:1000]
            print(
                "No se pudo evaluar el champion en el mismo corte; "
                "el candidato se guardará sin promover: "
                f"{champion_evaluation_error}"
            )

    commit = git_commit()
    commit_label = (commit or "nogit")[:12]
    version = f"lgbm-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{commit_label}-{uuid.uuid4().hex[:8]}"
    artifact_path = f"lightgbm/{version}.joblib"
    metadata = {
        "feature_schema_version": "2026-09-drift-v2",
        "feature_names": list(dict.fromkeys(FEATURE_NAMES + feature_columns)),
        "model_strategy": "independent_station_horizon",
        "station_model_count": sum(len(models_by_horizon) for models_by_horizon in station_models.values()),
        "level_adjustment": {
            "enabled": True,
            "window_points": LEVEL_WINDOW_POINTS,
            "bounds": [LEVEL_MIN_FACTOR, LEVEL_MAX_FACTOR],
        "reference": "same_15_minute_slots_1_day_prior",
        },
        "horizons_minutes": list(HORIZONS),
        "validation_accuracy": model_accuracy,
        "best_baseline_accuracy": best_baseline,
        "champion_same_window_accuracy": champion_same_window_accuracy,
        "live_production_comparison": {
            key: value
            for key, value in live_comparison.items()
            if key not in {"production_frame", "candidate_frame"}
        },
        "validation_cycles": test_origins,
        "validation_metric": "official_unweighted_station_wape_hourly_cycles",
        "smoke_inference": smoke_ok,
        "significance": significance,
        "station_guardrail": station_guardrail,
        "station_routes": station_routes,
        "station_model_variants": station_model_variants,
        "station_accuracy_target": MIN_STATION_ACCURACY,
    }
    db.upload_artifact(
        bucket,
        artifact_path,
        artifact_bytes(
            models,
            feature_columns,
            metadata,
            station_models=station_models,
            station_feature_columns=station_feature_columns,
        ),
    )
    db.insert(
        "model_versions",
        {
            "version": version,
            "data_cutoff": data_cutoff.isoformat(),
            "git_commit": commit,
            "features": list(dict.fromkeys(FEATURE_NAMES + feature_columns)),
            "metric": model_accuracy,
            "artifact_path": f"{bucket}/{artifact_path}",
            "status": "candidate",
            "training_metadata": {
                "validation_accuracy": model_accuracy,
                "best_baseline_accuracy": best_baseline,
                "champion_same_window_accuracy": champion_same_window_accuracy,
                "promotion_reference": max(MIN_PROMOTION_ACCURACY, best_baseline),
                "significance": significance,
                "station_guardrail": station_guardrail,
                "live_production_comparison": {
                    key: value
                    for key, value in live_comparison.items()
                    if key not in {"production_frame", "candidate_frame"}
                },
                "live_guardrail": live_guardrail,
                "station_routes": station_routes,
                "station_model_variants": station_model_variants,
                "station_accuracy_target": MIN_STATION_ACCURACY,
                "validation_cycles": test_origins,
            },
            **(
                {"dataset_snapshot_id": dataset_snapshot["snapshot_id"]}
                if dataset_snapshot
                else {}
            ),
        },
    )

    promotion_reference = best_baseline
    station_scores = _accuracy_rows(
        validation_frame,
        ["station_id"],
        prediction_column="prediction",
    )
    minimum_station_accuracy = float(station_scores["accuracy"].min()) if not station_scores.empty else 0.0
    beats_champion = current_champion is None or (
        champion_same_window_accuracy is not None
        and model_accuracy >= champion_same_window_accuracy + MIN_PROMOTION_MARGIN
        and significance["significant"]
    )
    if live_comparison.get("available"):
        reference_gate = (
            float(live_comparison["candidate_accuracy"])
            >= float(live_comparison["production_accuracy"]) + MIN_PROMOTION_MARGIN
            and live_guardrail["passed"]
        )
    else:
        reference_gate = beats_champion and station_guardrail["passed"]
    promotion_cooldown_active = False
    try:
        recent_event = (
            db.client.table("model_promotion_events")
            .select("completed_at")
            .eq("action", "promotion")
            .eq("status", "succeeded")
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        if recent_event.data and recent_event.data[0].get("completed_at"):
            completed_at = pd.Timestamp(recent_event.data[0]["completed_at"])
            if completed_at.tzinfo is None:
                completed_at = completed_at.tz_localize("UTC")
            promotion_cooldown_active = completed_at >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=promotion_cooldown_hours)
    except Exception as exc:
        print(f"Aviso: no se pudo consultar histéresis de promoción: {exc}")

    # Durante drift no bloqueamos la promoción por un piso absoluto. El
    # candidato debe superar el mejor baseline y mejorar lo que realmente se
    # envió recientemente, sin romper una estación en más de 3 puntos.
    promoted = (
        model_accuracy > promotion_reference
        and reference_gate
        and smoke_ok
        and not promotion_cooldown_active
    )
    if promoted:
        previous_version = current_champion["version"] if current_champion else None
        event_rows = db.insert(
            "model_promotion_events",
            {
                "requested_version": version,
                "previous_version": previous_version,
                "action": "promotion",
                "reason": "superó la referencia y al champion en ciclos horarios idénticos; pasó inferencia y comparación significativa",
                "status": "pending",
                "verification": {
                    "validation_accuracy": model_accuracy,
                    "best_baseline_accuracy": best_baseline,
                    "champion_same_window_accuracy": champion_same_window_accuracy,
                    "promotion_margin": MIN_PROMOTION_MARGIN,
                    "smoke_inference": smoke_ok,
                    "significance": significance,
                    "minimum_station_accuracy": minimum_station_accuracy,
                    "station_accuracy_target": MIN_STATION_ACCURACY,
                    "live_production_comparison": {
                        key: value
                        for key, value in live_comparison.items()
                        if key not in {"production_frame", "candidate_frame"}
                    },
                    "live_guardrail": live_guardrail,
                },
            },
        )
        if not event_rows:
            raise RuntimeError("No se pudo abrir el evento de promoción")
        event_id = event_rows[0]["id"]
        changed = False
        try:
            db.client.table("model_versions").update({"status": "retired"}).eq("status", "champion").execute()
            db.client.table("model_versions").update({"status": "champion"}).eq("version", version).execute()
            changed = True
            db.client.table("model_promotion_events").update(
                {
                    "status": "succeeded",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", event_id).execute()
        except Exception as exc:
            if changed and previous_version:
                try:
                    db.client.table("model_versions").update({"status": "retired"}).eq("version", version).execute()
                    db.client.table("model_versions").update({"status": "champion"}).eq("version", previous_version).execute()
                except Exception:
                    pass
            try:
                db.client.table("model_promotion_events").update(
                    {
                        "status": "failed",
                        "error": str(exc)[:4000],
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", event_id).execute()
            except Exception:
                pass
            raise RuntimeError(f"No se pudo registrar la promoción: {exc}") from exc
    print(
        f"Modelo: {version} | accuracy={model_accuracy:.4f} | "
        f"umbral_promoción={promotion_reference:.4f} | "
        f"champion_mismo_corte={champion_same_window_accuracy} | "
        f"cooldown={promotion_cooldown_active} | "
        f"status={'champion' if promoted else 'candidate'}"
    )
    return {"version": version, "comparison": comparison, "promoted": promoted, "artifact_path": artifact_path}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/starter/observations.csv"))
    parser.add_argument(
        "--from-supabase",
        action="store_true",
        help="entrena con observations persistidas por el collector",
    )
    parser.add_argument("--bucket", default="model-artifacts")
    parser.add_argument("--origins", type=int, default=96)
    args = parser.parse_args()
    db = SupabaseDB() if args.from_supabase else None
    observations = observations_from_supabase(db) if db is not None else pd.read_csv(
        args.data, dtype={"station_id": "string"}
    )
    train_and_register(
        observations,
        db=db,
        bucket=args.bucket,
        test_origins=args.origins,
    )


if __name__ == "__main__":
    main()
