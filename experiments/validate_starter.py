"""Validate the static starter dataset before distribution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


DATA_DIR = Path("data/starter")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    metadata = json.loads((DATA_DIR / "metadata.json").read_text(encoding="utf-8"))
    stations = pd.read_csv(DATA_DIR / "stations.csv", dtype={"station_id": "string"})
    observations = pd.read_csv(
        DATA_DIR / "observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"]
    )
    context = pd.read_csv(DATA_DIR / "context.csv", parse_dates=["observed_at"])

    assert len(stations) == 12
    assert stations["station_id"].nunique() == 12
    assert stations["station_id"].str.len().eq(5).all()
    assert stations["latitude"].between(4.0, 5.0).all()
    assert stations["longitude"].between(-75.0, -73.0).all()

    assert len(observations) == 51_840
    assert observations.duplicated(["station_id", "observed_at"]).sum() == 0
    assert set(observations["station_id"]) == set(stations["station_id"])
    assert observations.groupby("station_id").size().eq(4_320).all()
    assert observations["demand"].ge(0).all()
    assert observations["demand"].mod(1).eq(0).all()

    assert len(context) == 4_320
    assert context["observed_at"].is_unique
    deltas = context["observed_at"].sort_values().diff().dropna()
    assert deltas.eq(pd.Timedelta(minutes=15)).all()
    assert context["observed_at"].min() == observations["observed_at"].min()
    assert context["observed_at"].max() == observations["observed_at"].max()
    assert context["rain_mm"].ge(0).all()
    assert context["rain_forecast"].ge(0).all()

    for filename, details in metadata["files"].items():
        assert sha256(DATA_DIR / filename) == details["sha256"]
    assert metadata["future_included"] is False
    assert metadata["periods_per_station"] == 4_320
    print("starter dataset valid: 12 stations, 4,320 periods/station, 51,840 observations")


if __name__ == "__main__":
    main()
