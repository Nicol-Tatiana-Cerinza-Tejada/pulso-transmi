"""Entrena un baseline real y envía la primera predicción de práctica.

Requiere la variable de entorno PULSO_API_KEY. El script descubre el ciclo y
sus targets; no tiene nombres de estaciones ni timestamps quemados.
"""

from __future__ import annotations

import math
import os
import subprocess
from datetime import datetime, timezone

import pandas as pd
import requests
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


BASE_URL = os.getenv(
    "PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io"
).rstrip("/")
API_KEY = os.environ.get("PULSO_API_KEY")


def api_get(path: str) -> dict:
    response = requests.get(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.sort_values(["station_id", "observed_at"]).copy()
    timestamp = data["observed_at"]
    data["hour"] = timestamp.dt.hour
    data["day_of_week"] = timestamp.dt.dayofweek
    data["hour_sin"] = timestamp.dt.hour.map(lambda value: math.sin(2 * math.pi * value / 24))
    data["hour_cos"] = timestamp.dt.hour.map(lambda value: math.cos(2 * math.pi * value / 24))
    grouped = data.groupby("station_id", observed=True)["demand"]
    data["lag_1"] = grouped.shift(1)
    data["lag_4"] = grouped.shift(4)
    data["lag_96"] = grouped.shift(96)
    data["rolling_12"] = grouped.transform(lambda values: values.shift(1).rolling(12).mean())
    data["rolling_96"] = grouped.transform(lambda values: values.shift(1).rolling(96).mean())
    return data.dropna()


def future_features(history: pd.DataFrame, targets: list[dict]) -> pd.DataFrame:
    rows = []
    for target in targets:
        station = target["station_id"]
        timestamp = pd.Timestamp(target["target_at"])
        values = (
            history.loc[history["station_id"] == station]
            .sort_values("observed_at")["demand"]
            .to_numpy()
        )
        if len(values) < 96:
            raise RuntimeError(f"No hay suficiente historia para {station}")
        rows.append(
            {
                "station_id": station,
                "target_at": target["target_at"],
                "hour": timestamp.hour,
                "day_of_week": timestamp.dayofweek,
                "hour_sin": math.sin(2 * math.pi * timestamp.hour / 24),
                "hour_cos": math.cos(2 * math.pi * timestamp.hour / 24),
                "lag_1": values[-1],
                "lag_4": values[-4],
                "lag_96": values[-96],
                "rolling_12": values[-12:].mean(),
                "rolling_96": values[-96:].mean(),
            }
        )
    return pd.DataFrame(rows)


def git_commit() -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        return value if len(value) == 40 else None
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    if not API_KEY:
        raise SystemExit("Define PULSO_API_KEY antes de ejecutar el script.")

    identity = api_get("/v1/me")
    cycle = api_get("/v1/forecast-cycles/current")
    observations_url = f"{BASE_URL}/v1/downloads/observations.csv"
    history = pd.read_csv(observations_url, dtype={"station_id": "string"})
    history["observed_at"] = pd.to_datetime(history["observed_at"], utc=True)

    training = add_features(history)
    feature_columns = [
        "station_id",
        "hour",
        "day_of_week",
        "hour_sin",
        "hour_cos",
        "lag_1",
        "lag_4",
        "lag_96",
        "rolling_12",
        "rolling_96",
    ]
    categorical = ["station_id"]
    numeric = [column for column in feature_columns if column not in categorical]
    model = Pipeline(
        [
            (
                "features",
                ColumnTransformer(
                    [
                        ("station", OneHotEncoder(handle_unknown="ignore"), categorical),
                        ("numeric", "passthrough", numeric),
                    ]
                ),
            ),
            (
                "model",
                RandomForestRegressor(
                    n_estimators=80,
                    min_samples_leaf=3,
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    model.fit(training[feature_columns], training["demand"])

    future = future_features(history, cycle["targets"])
    values = model.predict(future[feature_columns])
    predictions = [
        {
            "station_id": row.station_id,
            "target_at": row.target_at,
            "value": max(0.0, round(float(value), 3)),
        }
        for row, value in zip(future.itertuples(index=False), values, strict=True)
    ]

    run_id = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    payload = {
        "schema_version": "1.0",
        "cycle_id": cycle["cycle_id"],
        "client_run_id": run_id,
        "data_cutoff": cycle["data_cutoff"],
        "model": {
            "version": "random-forest-baseline:0.1",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "training_data_end": cycle["data_cutoff"],
            "git_commit": git_commit(),
        },
        "predictions": predictions,
    }
    if payload["model"]["git_commit"] is None:
        del payload["model"]["git_commit"]

    response = requests.post(
        f"{BASE_URL}/v1/submissions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Idempotency-Key": run_id,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    receipt = response.json()
    print(f"Estudiante: {identity['display_name']}")
    print(f"Entrega: {receipt['submission_id']}")
    print(f"Estado: {receipt['status']}")
    print(
        f"Predicciones: {receipt['predictions_received']}/"
        f"{receipt['expected_predictions']}"
    )


if __name__ == "__main__":
    main()
