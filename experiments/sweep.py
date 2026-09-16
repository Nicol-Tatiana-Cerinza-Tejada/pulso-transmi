"""Run the calibration over several seeds to test robustness."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from calibrate import (
    CATEGORICAL,
    HISTORY_DAYS,
    NUMERIC,
    build_examples,
    evaluate_predictions,
    fit_adaptive,
    generate_scenario,
    make_pipeline,
)


SEEDS = (20260916, 20260917, 20260918)
MODELS = ["naive_day", "naive_week", "ridge_static", "boosting_static", "boosting_adaptive_daily"]
SEGMENTS = ["full", "pre_drift_d0_d2", "transition_d3_d4", "post_drift_d5_d6"]


def run(seed: int) -> pd.DataFrame:
    data = generate_scenario(seed)
    examples = build_examples(data)
    competition_start = data["timestamp"].min() + pd.Timedelta(days=HISTORY_DAYS)
    train = examples.loc[examples["target_at"] < competition_start].copy()
    competition = examples.loc[examples["timestamp"] >= competition_start].copy()
    features = CATEGORICAL + NUMERIC

    ridge = make_pipeline("ridge")
    boosting = make_pipeline("boosting")
    ridge.fit(train[features], train["target"])
    boosting.fit(train[features], train["target"])
    competition["ridge_static"] = np.clip(ridge.predict(competition[features]), 0, None)
    competition["boosting_static"] = np.clip(boosting.predict(competition[features]), 0, None)
    competition["boosting_adaptive_daily"] = fit_adaptive(train, competition)

    result = evaluate_predictions(competition, MODELS)
    result = result.loc[result["segment"].isin(SEGMENTS)].copy()
    result.insert(0, "seed", seed)
    return result


def main() -> None:
    output = Path("experiments/results")
    output.mkdir(parents=True, exist_ok=True)
    results = pd.concat([run(seed) for seed in SEEDS], ignore_index=True)
    results.to_csv(output / "seed_sweep.csv", index=False)

    summary = results.groupby(["model", "segment"])["accuracy"].agg(["mean", "std", "min", "max"]).reset_index()
    summary.to_csv(output / "seed_sweep_summary.csv", index=False)
    table = summary.loc[summary["segment"].isin(SEGMENTS)].pivot(index="model", columns="segment", values="mean")
    lines = [
        "# Robustez entre semillas",
        "",
        f"Semillas evaluadas: {', '.join(str(seed) for seed in SEEDS)}.",
        "",
        "## Accuracy media",
        "",
        table[SEGMENTS].rename(columns={
            "full": "Total",
            "pre_drift_d0_d2": "Pre-drift",
            "transition_d3_d4": "Transición",
            "post_drift_d5_d6": "Post-drift",
        }).to_markdown(floatfmt=".2f"),
        "",
        "Consulta `seed_sweep_summary.csv` para desviación estándar, mínimo y máximo.",
    ]
    (output / "seed_sweep.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
