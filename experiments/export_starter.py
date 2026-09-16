"""Export a leakage-safe static dataset for the first class."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from calibrate import HISTORY_DAYS, SEED, generate_scenario


OUTPUT_DIR = Path("data/starter")
SOURCE_URL = (
    "https://gis.transmilenio.gov.co/arcgis/rest/services/Troncal/"
    "consulta_estaciones_troncales/FeatureServer/0"
)

# Metadata verified against the official TransMilenio ArcGIS FeatureServer on
# 2026-09-16. The synthetic generator keeps its private STxx identity; this map
# only controls the public catalog and prevents calibration details from leaking.
STATIONS = {
    "ST01": ("03000", "Portal Suba", "Suba", 4.74681506, -74.09427889),
    "ST02": ("05000", "Portal Américas", "Américas", 4.62938130, -74.17305845),
    "ST03": ("09000", "Portal Usme", "Caracas", 4.53171458, -74.11939098),
    "ST04": ("02300", "Calle 100 - Marketmedios", "Autonorte", 4.68394667, -74.05769591),
    "ST05": ("09122", "Calle 72", "Caracas", 4.65823884, -74.06206854),
    "ST06": ("05100", "Banderas", "Américas", 4.63130064, -74.14576938),
    "ST07": ("07111", "Ricaurte - NQS", "NQS", 4.61168620, -74.09386888),
    "ST08": ("06000", "Portal El Dorado – C.C. NUESTRO BOGOTÁ", "Calle 26", 4.68160430, -74.12139545),
    "ST09": ("07107", "Universidad Nacional", "NQS", 4.63711879, -74.07932113),
    "ST10": ("06111", "Universidades – CityU", "Calle 26", 4.60464286, -74.06730954),
    "ST11": ("07105", "Movistar Arena", "NQS", 4.65003852, -74.07834591),
    "ST12": ("10009", "Museo Nacional", "Carrera 7-10", 4.61524712, -74.06922646),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scenario = generate_scenario(SEED)
    competition_start = scenario["timestamp"].min() + pd.Timedelta(days=HISTORY_DAYS)
    history = scenario.loc[scenario["timestamp"] < competition_start].copy()

    station_rows = [
        {
            "station_id": values[0],
            "station_name": values[1],
            "corridor": values[2],
            "latitude": values[3],
            "longitude": values[4],
        }
        for values in STATIONS.values()
    ]
    stations = pd.DataFrame(station_rows)
    id_map = {private_id: values[0] for private_id, values in STATIONS.items()}
    history["station_id"] = history["station_id"].map(id_map)

    observations = history[["timestamp", "station_id", "value"]].rename(
        columns={"timestamp": "observed_at", "value": "demand"}
    ).sort_values(["observed_at", "station_id"])
    context = history.loc[history["station_id"] == stations.iloc[0]["station_id"], [
        "timestamp", "rain_actual", "rain_forecast", "temperature_actual",
        "temperature_forecast", "event_intensity",
    ]].rename(columns={
        "timestamp": "observed_at",
        "rain_actual": "rain_mm",
        "temperature_actual": "temperature_c",
    }).sort_values("observed_at")

    stations_path = OUTPUT_DIR / "stations.csv"
    observations_path = OUTPUT_DIR / "observations.csv"
    context_path = OUTPUT_DIR / "context.csv"
    stations.to_csv(stations_path, index=False)
    observations.to_csv(observations_path, index=False)
    context.to_csv(context_path, index=False)

    metadata = {
        "dataset": "pulso-transmi-starter-v1",
        "generated_at": "2026-09-16",
        "timezone": "America/Bogota",
        "frequency_minutes": 15,
        "history_days": HISTORY_DAYS,
        "periods_per_station": HISTORY_DAYS * 96,
        "station_count": len(stations),
        "observation_rows": len(observations),
        "context_rows": len(context),
        "history_start": observations["observed_at"].min().isoformat(),
        "history_end": observations["observed_at"].max().isoformat(),
        "generator_seed": SEED,
        "future_included": False,
        "demand_is_synthetic": True,
        "station_metadata_source": SOURCE_URL,
        "station_metadata_verified_at": "2026-09-16",
        "files": {
            stations_path.name: {"rows": len(stations), "sha256": sha256(stations_path)},
            observations_path.name: {"rows": len(observations), "sha256": sha256(observations_path)},
            context_path.name: {"rows": len(context), "sha256": sha256(context_path)},
        },
    }
    (OUTPUT_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
