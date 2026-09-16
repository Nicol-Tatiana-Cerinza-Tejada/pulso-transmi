"""Calibrate the synthetic forecasting challenge before implementing production code.

This experiment is deliberately isolated from ``app``. It generates a complete
52-day scenario, builds leakage-safe hourly forecast examples and compares
simple, static and adaptive baselines.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


SEED = 20260916
INTERVALS_PER_DAY = 96
HISTORY_DAYS = 45
COMPETITION_DAYS = 7
HORIZONS = (1, 2, 3, 4)


@dataclass(frozen=True)
class Station:
    station_id: str
    archetype: str
    level: float
    latitude: float
    longitude: float
    phase_minutes: int
    weather_sensitivity: float
    trend_per_day: float


STATIONS = (
    Station("ST01", "residential", 520, 4.612, -74.185, -15, -0.10, 0.0010),
    Station("ST02", "residential", 680, 4.625, -74.174, 20, -0.06, 0.0003),
    Station("ST03", "residential", 440, 4.640, -74.162, 35, -0.13, 0.0014),
    Station("ST04", "business", 720, 4.655, -74.072, -10, -0.18, 0.0010),
    Station("ST05", "business", 610, 4.668, -74.061, 15, -0.14, 0.0001),
    Station("ST06", "interchange", 980, 4.630, -74.110, 0, -0.05, 0.0006),
    Station("ST07", "interchange", 1120, 4.676, -74.103, 25, -0.04, 0.0008),
    Station("ST08", "interchange", 860, 4.594, -74.154, -20, -0.08, 0.0004),
    Station("ST09", "university", 510, 4.601, -74.068, 10, -0.16, 0.0016),
    Station("ST10", "university", 430, 4.648, -74.079, -25, -0.12, 0.0011),
    Station("ST11", "leisure", 390, 4.667, -74.054, 30, 0.08, 0.0005),
    Station("ST12", "leisure", 470, 4.626, -74.073, -5, 0.12, 0.0007),
)


def gaussian(hour: np.ndarray, center: float, width: float) -> np.ndarray:
    distance = np.minimum(np.abs(hour - center), 24 - np.abs(hour - center))
    return np.exp(-0.5 * (distance / width) ** 2)


def daily_profile(archetype: str, hour: np.ndarray) -> np.ndarray:
    profiles = {
        "residential": 0.20 + 1.65 * gaussian(hour, 6.8, 1.15) + 0.80 * gaussian(hour, 18.2, 1.7),
        "business": 0.18 + 1.05 * gaussian(hour, 8.2, 1.35) + 1.45 * gaussian(hour, 17.4, 1.25),
        "interchange": 0.30 + 1.20 * gaussian(hour, 7.3, 1.35) + 1.15 * gaussian(hour, 17.7, 1.45),
        "university": 0.16 + 0.75 * gaussian(hour, 7.5, 1.5) + 1.30 * gaussian(hour, 12.4, 2.2) + 0.62 * gaussian(hour, 19.0, 1.6),
        "leisure": 0.17 + 0.48 * gaussian(hour, 12.8, 2.1) + 1.48 * gaussian(hour, 20.2, 2.1),
    }
    return profiles[archetype]


def generate_scenario(seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range(
        "2026-07-26 00:00:00", periods=(HISTORY_DAYS + COMPETITION_DAYS) * INTERVALS_PER_DAY,
        freq="15min", tz="America/Bogota"
    )
    n_times = len(timestamps)
    competition_start_idx = HISTORY_DAYS * INTERVALS_PER_DAY

    city = np.zeros(n_times)
    rain = np.zeros(n_times)
    temperature = np.zeros(n_times)
    city_shocks = rng.normal(0, 0.055, n_times)
    rain_shocks = rng.gamma(shape=0.7, scale=1.2, size=n_times)
    rain_gate = rng.random(n_times) < 0.055
    for idx in range(1, n_times):
        city[idx] = 0.93 * city[idx - 1] + city_shocks[idx]
        rain[idx] = max(0.0, 0.82 * rain[idx - 1] + rain_shocks[idx] * rain_gate[idx])
    local_hour = timestamps.hour.to_numpy() + timestamps.minute.to_numpy() / 60
    temperature = 14.5 + 5.8 * np.sin(2 * np.pi * (local_hour - 8) / 24) + rng.normal(0, 0.7, n_times)
    rain_forecast = np.clip(rain + rng.normal(0, 0.45, n_times), 0, None)
    temperature_forecast = temperature + rng.normal(0, 0.8, n_times)

    event = np.zeros(n_times)
    for day in (8, 15, 23, 31, 40, 47, 50):
        center = day * INTERVALS_PER_DAY + 79
        width = 9 if day % 2 else 14
        indices = np.arange(n_times)
        event += np.exp(-0.5 * ((indices - center) / width) ** 2)

    rows: list[pd.DataFrame] = []
    station_latent = np.zeros((len(STATIONS), n_times))
    for s_idx in range(len(STATIONS)):
        shocks = rng.normal(0, 0.045 + 0.005 * (s_idx % 3), n_times)
        for idx in range(1, n_times):
            station_latent[s_idx, idx] = 0.72 * station_latent[s_idx, idx - 1] + shocks[idx]

    values = np.zeros((len(STATIONS), n_times), dtype=np.int64)
    means = np.zeros_like(values, dtype=float)
    for s_idx, station in enumerate(STATIONS):
        shifted_hour = (local_hour + station.phase_minutes / 60) % 24
        profile = daily_profile(station.archetype, shifted_hour)
        dow = timestamps.dayofweek.to_numpy()
        is_weekend = dow >= 5
        weekly = np.ones(n_times)
        if station.archetype in {"business", "university"}:
            weekly[is_weekend] = 0.48
        elif station.archetype == "leisure":
            weekly[is_weekend] = 1.32
        else:
            weekly[is_weekend] = 0.83

        day_number = np.arange(n_times) / INTERVALS_PER_DAY
        trend = np.exp(station.trend_per_day * day_number)
        event_effect = 1 + event * (0.34 if station.archetype == "leisure" else 0.08)
        weather_effect = np.exp(station.weather_sensitivity * np.sqrt(rain + 0.1))
        latent_effect = np.exp(0.23 * city + 0.18 * station_latent[s_idx])

        drift = np.ones(n_times)
        comp_day = (np.arange(n_times) - competition_start_idx) / INTERVALS_PER_DAY

        # Four heterogeneous stations shift their peaks and level gradually.
        if station.station_id in {"ST02", "ST05", "ST07", "ST09"}:
            transition = 1 / (1 + np.exp(-(comp_day - 2.6) * 5.0))
            shifted = daily_profile(station.archetype, (shifted_hour - 0.75 * transition) % 24)
            profile = profile * (1 - transition) + shifted * transition
            drift *= 1 + 0.18 * transition

        # An operational closure depresses one hub and moves demand to two others.
        closure = 1 / (1 + np.exp(-(comp_day - 4.55) * 12.0))
        if station.station_id == "ST06":
            drift *= 1 - 0.58 * closure
        elif station.station_id in {"ST07", "ST08"}:
            drift *= 1 + 0.24 * closure

        # A hidden behavioural change reverses the effect of rain for a subset.
        if station.station_id in {"ST03", "ST10", "ST12"}:
            weather_transition = 1 / (1 + np.exp(-(comp_day - 5.15) * 7.0))
            alternative = np.exp((-station.weather_sensitivity * 0.8) * np.sqrt(rain + 0.1))
            weather_effect = weather_effect * (1 - weather_transition) + alternative * weather_transition

        mu = np.clip(station.level * profile * weekly * trend * event_effect * weather_effect * latent_effect * drift, 8, None)
        # Gamma-Poisson sampling introduces over-dispersion without destroying structure.
        dispersion = 72 - 6 * (s_idx % 4)
        sampled_rate = rng.gamma(shape=dispersion, scale=mu / dispersion)
        y = rng.poisson(sampled_rate)
        means[s_idx] = mu
        values[s_idx] = y

        rows.append(pd.DataFrame({
            "station_id": station.station_id,
            "archetype": station.archetype,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "timestamp": timestamps,
            "value": y,
            "expected_mean": mu,
            "rain_actual": rain,
            "rain_forecast": rain_forecast,
            "temperature_actual": temperature,
            "temperature_forecast": temperature_forecast,
            "event_intensity": event,
            "competition_day": comp_day,
        }))

    return pd.concat(rows, ignore_index=True).sort_values(["station_id", "timestamp"]).reset_index(drop=True)


def build_examples(data: pd.DataFrame) -> pd.DataFrame:
    groups = data.groupby("station_id", sort=False)
    frame = data.copy()
    for lag in (1, 4, 8, 16, 96, 192, 672):
        frame[f"lag_{lag}"] = groups["value"].shift(lag)
    shifted = groups["value"].shift(1)
    frame["rolling_4"] = shifted.groupby(frame["station_id"]).transform(lambda x: x.rolling(4).mean())
    frame["rolling_16"] = shifted.groupby(frame["station_id"]).transform(lambda x: x.rolling(16).mean())
    frame["rolling_96"] = shifted.groupby(frame["station_id"]).transform(lambda x: x.rolling(96).mean())
    frame["origin_value"] = frame["value"]
    frame["origin_index"] = groups.cumcount()

    origins = frame.loc[(frame["timestamp"].dt.minute == 0) & (frame["origin_index"] >= 672)].copy()
    examples: list[pd.DataFrame] = []
    source = frame.set_index(["station_id", "origin_index"])
    for horizon in HORIZONS:
        part = origins.copy()
        target_index = part["origin_index"] + horizon
        lookup_index = pd.MultiIndex.from_arrays([part["station_id"], target_index])
        target = source.reindex(lookup_index).reset_index(drop=True)
        part["horizon"] = horizon
        part["target_at"] = target["timestamp"].to_numpy()
        part["target"] = target["value"].to_numpy()
        part["target_rain_forecast"] = target["rain_forecast"].to_numpy()
        part["target_temperature_forecast"] = target["temperature_forecast"].to_numpy()
        part["target_event_intensity"] = target["event_intensity"].to_numpy()
        target_hour = target["timestamp"].dt.hour.to_numpy() + target["timestamp"].dt.minute.to_numpy() / 60
        part["hour_sin"] = np.sin(2 * np.pi * target_hour / 24)
        part["hour_cos"] = np.cos(2 * np.pi * target_hour / 24)
        target_dow = target["timestamp"].dt.dayofweek.to_numpy()
        part["dow_sin"] = np.sin(2 * np.pi * target_dow / 7)
        part["dow_cos"] = np.cos(2 * np.pi * target_dow / 7)
        part["is_weekend"] = (target_dow >= 5).astype(int)
        part["naive_last"] = part["origin_value"]
        part["naive_day"] = target["lag_96"].to_numpy()
        part["naive_week"] = target["lag_672"].to_numpy()
        examples.append(part)
    result = pd.concat(examples, ignore_index=True)
    return result.dropna(subset=["target", "lag_672", "rolling_96"]).reset_index(drop=True)


CATEGORICAL = ["station_id", "archetype"]
NUMERIC = [
    "horizon", "latitude", "longitude", "lag_1", "lag_4", "lag_8", "lag_16",
    "lag_96", "lag_192", "lag_672", "rolling_4", "rolling_16", "rolling_96",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "is_weekend",
    "target_rain_forecast", "target_temperature_forecast", "target_event_intensity",
]


def make_pipeline(kind: str) -> Pipeline:
    if kind == "ridge":
        numeric_transformer = StandardScaler()
        estimator = Ridge(alpha=18.0)
    else:
        numeric_transformer = "passthrough"
        estimator = HistGradientBoostingRegressor(
            loss="absolute_error", learning_rate=0.075, max_iter=150,
            max_leaf_nodes=24, min_samples_leaf=35, l2_regularization=2.0,
            random_state=SEED,
        )
    preprocessor = ColumnTransformer([
        ("numeric", numeric_transformer, NUMERIC),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
    ])
    return Pipeline([("features", preprocessor), ("model", estimator)])


def station_average_accuracy(frame: pd.DataFrame, prediction: str) -> float:
    grouped = frame.assign(error=(frame["target"] - frame[prediction]).abs()).groupby("station_id")
    wape = grouped["error"].sum() / grouped["target"].sum().clip(lower=1)
    return float((100 * (1 - wape).clip(lower=0)).mean())


def evaluate_predictions(frame: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    segments = {
        "full": np.ones(len(frame), dtype=bool),
        "pre_drift_d0_d2": frame["competition_day"].between(0, 2.59),
        "transition_d3_d4": frame["competition_day"].between(2.6, 4.54),
        "post_drift_d5_d6": frame["competition_day"] >= 4.55,
    }
    records = []
    for model in models:
        for segment, mask in segments.items():
            subset = frame.loc[mask]
            records.append({
                "model": model,
                "segment": segment,
                "accuracy": station_average_accuracy(subset, model),
                "mae": float((subset["target"] - subset[model]).abs().mean()),
                "rows": len(subset),
            })
        for horizon in HORIZONS:
            subset = frame.loc[frame["horizon"] == horizon]
            records.append({
                "model": model,
                "segment": f"horizon_{horizon}",
                "accuracy": station_average_accuracy(subset, model),
                "mae": float((subset["target"] - subset[model]).abs().mean()),
                "rows": len(subset),
            })
    return pd.DataFrame(records)


def evaluate_by_station(frame: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    records = []
    for model in models:
        for station_id, subset in frame.groupby("station_id"):
            error = (subset["target"] - subset[model]).abs().sum()
            denominator = max(float(subset["target"].sum()), 1.0)
            records.append({
                "model": model,
                "station_id": station_id,
                "accuracy": 100 * max(0.0, 1 - error / denominator),
                "mae": float((subset["target"] - subset[model]).abs().mean()),
                "rows": len(subset),
            })
    return pd.DataFrame(records)


def evaluate_by_day(frame: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    work = frame.copy()
    work["day"] = np.floor(work["competition_day"]).astype(int)
    records = []
    for model in models:
        for day, subset in work.groupby("day"):
            records.append({
                "model": model,
                "day": int(day),
                "accuracy": station_average_accuracy(subset, model),
                "mae": float((subset["target"] - subset[model]).abs().mean()),
                "rows": len(subset),
            })
    return pd.DataFrame(records)


def fit_adaptive(train: pd.DataFrame, competition: pd.DataFrame) -> np.ndarray:
    predictions = pd.Series(index=competition.index, dtype=float)
    competition_dates = competition["timestamp"].dt.date.unique()
    all_known = pd.concat([train, competition], axis=0).sort_values("timestamp")
    for day in competition_dates:
        day_start = pd.Timestamp(day, tz="America/Bogota")
        day_end = day_start + pd.Timedelta(days=1)
        # Targets must already be observable at the retraining cutoff.
        available = all_known.loc[
            (all_known["target_at"] < day_start)
            & (all_known["target_at"] >= day_start - pd.Timedelta(days=28))
        ]
        today = competition.loc[(competition["timestamp"] >= day_start) & (competition["timestamp"] < day_end)]
        model = make_pipeline("boosting")
        model.fit(available[CATEGORICAL + NUMERIC], available["target"])
        predictions.loc[today.index] = np.clip(model.predict(today[CATEGORICAL + NUMERIC]), 0, None)
    return predictions.to_numpy()


def write_report(metrics: pd.DataFrame, output_dir: Path) -> None:
    full = metrics.loc[metrics["segment"].isin(["full", "pre_drift_d0_d2", "transition_d3_d4", "post_drift_d5_d6"])]
    pivot = full.pivot(index="model", columns="segment", values="accuracy")
    ordered = ["full", "pre_drift_d0_d2", "transition_d3_d4", "post_drift_d5_d6"]
    pivot = pivot[ordered].sort_values("full", ascending=False)

    lines = [
        "# Resultado de calibración experimental",
        "",
        "Escenario determinista con 45 días de historia, 7 de competencia, 12 estaciones y cuatro horizontes.",
        "La accuracy es `100 × max(0, 1 - WAPE)` promediada por estación.",
        "",
        "## Accuracy por régimen",
        "",
        pivot.rename(columns={
            "full": "Total",
            "pre_drift_d0_d2": "Pre-drift",
            "transition_d3_d4": "Transición",
            "post_drift_d5_d6": "Post-drift",
        }).to_markdown(floatfmt=".2f"),
        "",
        "## Lectura automática",
        "",
    ]
    ridge = pivot.loc["ridge_static"]
    static = pivot.loc["boosting_static"]
    adaptive = pivot.loc["boosting_adaptive_daily"]
    simple_too_easy = ridge["full"] >= 88
    drift_drop = static["pre_drift_d0_d2"] - static["post_drift_d5_d6"]
    recovery = adaptive["post_drift_d5_d6"] - static["post_drift_d5_d6"]
    lines.extend([
        f"- Regresión lineal estática: **{ridge['full']:.2f}**. " + ("Demasiado alta." if simple_too_easy else "No resuelve por sí sola el reto."),
        f"- Caída del boosting estático tras drift: **{drift_drop:.2f} puntos**.",
        f"- Recuperación por reentrenamiento diario post-drift: **{recovery:.2f} puntos**.",
        f"- Veredicto automático: **{'REAJUSTAR' if simple_too_easy or drift_drop < 7 or recovery < 2 else 'RETO VIABLE'}**.",
        "",
        "La decisión final también debe revisar estabilidad por estación y horizonte; no se debe optimizar solo el promedio.",
    ])
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_metrics(metrics: pd.DataFrame, output_dir: Path) -> None:
    segments = ["pre_drift_d0_d2", "transition_d3_d4", "post_drift_d5_d6"]
    models = ["naive_day", "naive_week", "ridge_static", "boosting_static", "boosting_adaptive_daily"]
    pivot = metrics.loc[metrics["segment"].isin(segments) & metrics["model"].isin(models)].pivot(
        index="segment", columns="model", values="accuracy"
    ).reindex(segments)
    ax = pivot.plot(kind="bar", figsize=(12, 6), ylim=(45, 100), width=0.82)
    ax.set_title("Pulso TransMi: dificultad por régimen")
    ax.set_ylabel("Accuracy (%)")
    ax.set_xlabel("")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="lower left", fontsize=8)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(output_dir / "accuracy_by_regime.png", dpi=160)
    plt.close()


def plot_patterns(data: pd.DataFrame, output_dir: Path) -> None:
    competition = data.loc[data["competition_day"] >= 0].copy()
    selected = ["ST02", "ST06", "ST10"]
    fig, axes = plt.subplots(len(selected), 1, figsize=(14, 9), sharex=True)
    descriptions = {
        "ST02": "cambio gradual de pico y nivel",
        "ST06": "cierre operacional",
        "ST10": "cambio oculto de sensibilidad a lluvia",
    }
    for ax, station_id in zip(axes, selected, strict=True):
        station = competition.loc[competition["station_id"] == station_id].set_index("timestamp")
        hourly = station[["value", "expected_mean"]].resample("1h").mean()
        ax.plot(hourly.index, hourly["value"], color="#2864dc", linewidth=0.8, alpha=0.65, label="observado")
        ax.plot(hourly.index, hourly["expected_mean"], color="#ec6b2d", linewidth=1.4, label="media latente")
        ax.set_title(f"{station_id} — {descriptions[station_id]}", loc="left", fontsize=10)
        ax.set_ylabel("demanda")
        ax.grid(alpha=0.2)
        for day, color in ((2.6, "#7a4bc2"), (4.55, "#c43c39"), (5.15, "#17856b")):
            marker = competition["timestamp"].min() + pd.Timedelta(days=day)
            ax.axvline(marker, color=color, linestyle="--", linewidth=1, alpha=0.7)
    axes[0].legend(loc="upper left", ncol=2)
    axes[-1].set_xlabel("tiempo de competencia")
    fig.suptitle("Patrones visibles y puntos de cambio del escenario experimental", fontsize=13)
    plt.tight_layout()
    plt.savefig(output_dir / "patterns_and_drift.png", dpi=160)
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("experiments/results"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    data = generate_scenario(args.seed)
    examples = build_examples(data)
    competition_start = data["timestamp"].min() + pd.Timedelta(days=HISTORY_DAYS)
    train = examples.loc[examples["target_at"] < competition_start].copy()
    competition = examples.loc[examples["timestamp"] >= competition_start].copy()

    ridge = make_pipeline("ridge")
    boosting = make_pipeline("boosting")
    feature_columns = CATEGORICAL + NUMERIC
    ridge.fit(train[feature_columns], train["target"])
    boosting.fit(train[feature_columns], train["target"])
    competition["ridge_static"] = np.clip(ridge.predict(competition[feature_columns]), 0, None)
    competition["boosting_static"] = np.clip(boosting.predict(competition[feature_columns]), 0, None)
    competition["boosting_adaptive_daily"] = fit_adaptive(train, competition)

    models = ["naive_last", "naive_day", "naive_week", "ridge_static", "boosting_static", "boosting_adaptive_daily"]
    metrics = evaluate_predictions(competition, models)
    metrics.to_csv(args.output / "metrics.csv", index=False)
    evaluate_by_station(competition, models).to_csv(args.output / "metrics_by_station.csv", index=False)
    evaluate_by_day(competition, models).to_csv(args.output / "metrics_by_day.csv", index=False)
    competition[[
        "station_id", "timestamp", "target_at", "horizon", "competition_day", "target", *models
    ]].to_csv(args.output / "competition_predictions.csv", index=False)
    data.groupby(["station_id", pd.Grouper(key="timestamp", freq="1h")])["value"].sum().reset_index().to_csv(
        args.output / "hourly_sample.csv", index=False
    )
    write_report(metrics, args.output)
    plot_metrics(metrics, args.output)
    plot_patterns(data, args.output)
    summary = {
        "seed": args.seed,
        "raw_rows": len(data),
        "train_examples": len(train),
        "competition_examples": len(competition),
        "competition_start": competition_start.isoformat(),
    }
    (args.output / "run.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(metrics.loc[metrics["segment"].isin(["full", "pre_drift_d0_d2", "transition_d3_d4", "post_drift_d5_d6"])].to_string(index=False))


if __name__ == "__main__":
    main()
