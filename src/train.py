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

from .baselines import FREQUENCY, HORIZONS, prepare_observations, temporal_backtest
from .db import SupabaseDB
from .metrics import official_accuracy


LAG_STEPS = (1, 2, 4, 8, 96, 672)
ROLLING_WINDOWS = (4, 96, 672)
FEATURE_NAMES = [
    *(f"lag_{steps}" for steps in LAG_STEPS),
    *(f"rolling_mean_{window}" for window in ROLLING_WINDOWS),
    "rolling_std_96",
    "trend_4_96",
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
    history = prepare_observations(observations)
    timestamps = history["target_at"].drop_duplicates().sort_values()
    if len(timestamps) <= test_origins + 4:
        raise ValueError("No hay suficientes timestamps para validar temporalmente")
    return pd.DatetimeIndex(timestamps[timestamps <= timestamps.iloc[-5]].tail(test_origins))


def _station_series(history: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        str(station): group.set_index("target_at")["value"].sort_index()
        for station, group in history.groupby("station_id")
    }


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
            recent_96 = history_to_origin.tail(96)
            recent_4 = history_to_origin.tail(4)
            values["rolling_std_96"] = float(recent_96.std(ddof=0))
            values["trend_4_96"] = float(recent_4.mean() - recent_96.mean())
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
                    **values,
                }
            )
    return pd.DataFrame(rows)


def encode_features(frame: pd.DataFrame, columns: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    base_columns = [
        *(f"lag_{steps}" for steps in LAG_STEPS),
        *(f"rolling_mean_{window}" for window in ROLLING_WINDOWS),
        "rolling_std_96",
        "trend_4_96",
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


def train_lightgbm(train_features: pd.DataFrame, target: pd.Series):
    try:
        from lightgbm import LGBMRegressor
    except ImportError as exc:
        raise RuntimeError("Instala LightGBM con: pip install 'lightgbm>=4,<5'") from exc
    model = LGBMRegressor(
        objective="regression",
        n_estimators=350,
        learning_rate=0.04,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )
    model.fit(train_features, target)
    return model


def _accuracy_rows(frame: pd.DataFrame, group_columns: list[str], *, prediction_column: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(group_columns):
        if not isinstance(keys, tuple):
            keys = (keys,)
        actuals = group[["station_id", "target_at", "actual"]].rename(columns={"actual": "value"})
        predictions = group[["station_id", "target_at", prediction_column]].rename(columns={prediction_column: "value"})
        rows.append({**dict(zip(group_columns, keys)), "accuracy": official_accuracy(actuals, predictions)})
    return pd.DataFrame(rows)


def train_and_validate(
    observations: pd.DataFrame,
    *,
    test_origins: int = 96,
) -> tuple[dict[int, Any], pd.DataFrame, float, float, list[str], pd.Timestamp, dict[int, pd.DataFrame]]:
    """Entrena cuatro modelos y devuelve modelos, tabla, métricas y features."""
    origins = validation_origins(observations, test_origins)
    first_validation_origin = origins.min()
    prepared = prepare_observations(observations)
    train_cutoff = first_validation_origin - FREQUENCY
    models: dict[int, Any] = {}
    smoke_inputs: dict[int, pd.DataFrame] = {}
    validation_rows: list[pd.DataFrame] = []
    feature_columns: list[str] = []

    baseline_raw = temporal_backtest(observations, test_origins=test_origins)
    baseline_for_scoring = baseline_raw.rename(
        columns={"value_actual": "actual", "value_prediction": "prediction"}
    )
    baseline_summary = _accuracy_rows(
        baseline_for_scoring,
        ["method", "horizon_minutes"],
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
        train_matrix, feature_columns = encode_features(train)
        valid_matrix, _ = encode_features(valid, feature_columns)
        model = train_lightgbm(train_matrix, train["actual"])
        prediction = model.predict(valid_matrix)
        if len(prediction) != len(valid) or not np.isfinite(prediction).all():
            raise RuntimeError(f"Inferencia inválida para horizonte +{horizon}")
        validation_rows.append(
            valid[["station_id", "target_at", "actual"]].assign(
                horizon_minutes=horizon,
                method="lightgbm",
                prediction=prediction,
            )
        )
        models[horizon] = model
        smoke_inputs[horizon] = valid_matrix.iloc[[0]]

    model_rows = pd.concat(validation_rows, ignore_index=True)
    model_summary = _accuracy_rows(model_rows, ["horizon_minutes"], prediction_column="prediction")
    model_summary["method"] = "lightgbm"
    model_summary = model_summary[["method", "horizon_minutes", "accuracy"]]
    comparison = pd.concat(
        [baseline_summary[["method", "horizon_minutes", "accuracy"]], model_summary],
        ignore_index=True,
    ).sort_values(["method", "horizon_minutes"])
    print(comparison.to_string(index=False, formatters={"accuracy": "{:.4f}".format}))

    baseline_means = baseline_summary.groupby("method")["accuracy"].mean()
    best_baseline = float(baseline_means.max())
    model_accuracy = float(model_summary["accuracy"].mean())
    return models, comparison, model_accuracy, best_baseline, feature_columns, train_cutoff, smoke_inputs


def artifact_bytes(models: dict[int, Any], feature_columns: list[str], metadata: dict[str, Any]) -> bytes:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError("Instala joblib para serializar el artefacto") from exc
    payload = {"models": models, "feature_columns": feature_columns, "metadata": metadata}
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
) -> dict[str, Any]:
    db = db or SupabaseDB()
    models, comparison, model_accuracy, best_baseline, feature_columns, data_cutoff, smoke_inputs = train_and_validate(
        observations, test_origins=test_origins
    )
    smoke_predictions = [models[horizon].predict(smoke_inputs[horizon]) for horizon in HORIZONS]
    smoke_ok = all(
        len(prediction) == 1 and bool(pd.notna(prediction[0]))
        for prediction in smoke_predictions
    )
    if not smoke_ok:
        raise RuntimeError("La inferencia de prueba no produjo exactamente una predicción")

    commit = git_commit()
    commit_label = (commit or "nogit")[:12]
    version = f"lgbm-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{commit_label}-{uuid.uuid4().hex[:8]}"
    artifact_path = f"lightgbm/{version}.joblib"
    metadata = {
        "horizons_minutes": list(HORIZONS),
        "validation_accuracy": model_accuracy,
        "best_baseline_accuracy": best_baseline,
        "smoke_inference": smoke_ok,
    }
    db.upload_artifact(bucket, artifact_path, artifact_bytes(models, feature_columns, metadata))
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
        },
    )

    current_champion_response = (
        db.client.table("model_versions")
        .select("version,metric")
        .eq("status", "champion")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    current_champion = current_champion_response.data[0] if current_champion_response.data else None
    current_champion_metric = (
        float(current_champion["metric"])
        if current_champion and current_champion.get("metric") is not None
        else None
    )
    promotion_reference = max(best_baseline, current_champion_metric or float("-inf"))
    promoted = model_accuracy > promotion_reference and smoke_ok
    if promoted:
        previous_version = current_champion["version"] if current_champion else None
        event_rows = db.insert(
            "model_promotion_events",
            {
                "requested_version": version,
                "previous_version": previous_version,
                "action": "promotion",
                "reason": "superó al mejor baseline, al champion vigente y pasó la inferencia de prueba",
                "status": "pending",
                "verification": {
                    "validation_accuracy": model_accuracy,
                    "best_baseline_accuracy": best_baseline,
                    "smoke_inference": smoke_ok,
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
