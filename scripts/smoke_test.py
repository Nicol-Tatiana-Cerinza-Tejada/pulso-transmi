"""Smoke test end-to-end sin enviar submissions ni depender de la competencia."""

from __future__ import annotations

import io
import math
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import joblib
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api_client import PulsoTransmiClient  # noqa: E402
from src.collector import collect_once  # noqa: E402
from src.db import SupabaseDB  # noqa: E402
from src.infer import build_target_features, load_champion, load_observations_until  # noqa: E402
from src.train import encode_features  # noqa: E402


def check(name: str, function: Callable[[], str | None]) -> bool:
    try:
        detail = function()
        print(f"[PASS] {name}{f': {detail}' if detail else ''}")
        return True
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
        return False


def read_clock() -> dict[str, Any]:
    with PulsoTransmiClient() as api:
        clock = api.clock()
    state = clock.get("state")
    if state not in {"waiting", "paused", "running", "completed"}:
        raise RuntimeError(f"Estado de reloj inesperado: {state!r}")
    return clock


def collector_is_idempotent() -> str:
    db = SupabaseDB()
    before = db.observation_count()
    with PulsoTransmiClient() as api:
        first = collect_once(api, db)
    middle = db.observation_count()
    with PulsoTransmiClient() as api:
        second = collect_once(api, db)
    after = db.observation_count()
    if first["status"] != "succeeded" or second["status"] != "succeeded":
        raise RuntimeError(f"collector statuses: {first['status']}, {second['status']}")
    # La primera ejecución puede incorporar novedades. La garantía que se
    # prueba aquí es que repetir exactamente el colector no agrega filas.
    if middle != after:
        raise RuntimeError(f"observations cambiaron en la repetición: {before} -> {middle} -> {after}")
    return f"observations={after}; runs={first['status']}/{second['status']}"


def champion_produces_48_values() -> str:
    db = SupabaseDB()
    champion = load_champion(db)
    artifact_path = champion["artifact_path"].split("/", 1)[-1]
    artifact = joblib.load(
        io.BytesIO(db.download_artifact("model-artifacts", artifact_path))
    )
    history = load_observations_until(db, pd.Timestamp.now(tz="UTC"))
    cutoff = history["ts"].max()
    stations = sorted(str(value) for value in history["station_id"].unique())
    if len(stations) != 12:
        raise RuntimeError(f"se esperaban 12 estaciones, llegaron {len(stations)}")
    targets = [
        {
            "station_id": station,
            "target_at": (cutoff + timedelta(minutes=horizon)).isoformat(),
        }
        for station in stations
        for horizon in (15, 30, 45, 60)
    ]
    features, positions = build_target_features(history, targets, cutoff)
    values: list[float] = []
    for horizon, indexes in positions.items():
        matrix, _ = encode_features(features.iloc[indexes], artifact["feature_columns"])
        values.extend(float(value) for value in artifact["models"][horizon].predict(matrix))
    if len(values) != 48:
        raise RuntimeError(f"se generaron {len(values)} valores en vez de 48")
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise RuntimeError("hay predicciones no finitas o negativas")
    return f"champion={champion['version']}; valores=48"


def payload_is_valid() -> str:
    from app.contracts import SubmissionInput

    db = SupabaseDB()
    champion = load_champion(db)
    history = load_observations_until(db, pd.Timestamp.now(tz="UTC"))
    cutoff = history["ts"].max()
    stations = sorted(str(value) for value in history["station_id"].unique())
    targets = [
        {
            "station_id": station,
            "target_at": (cutoff + timedelta(minutes=horizon)).isoformat(),
        }
        for station in stations
        for horizon in (15, 30, 45, 60)
    ]
    features, positions = build_target_features(history, targets, cutoff)
    generated: dict[int, list[float]] = {}
    artifact_path = champion["artifact_path"].split("/", 1)[-1]
    artifact = joblib.load(io.BytesIO(db.download_artifact("model-artifacts", artifact_path)))
    for horizon, indexes in positions.items():
        matrix, _ = encode_features(features.iloc[indexes], artifact["feature_columns"])
        generated[horizon] = [max(0.0, float(value)) for value in artifact["models"][horizon].predict(matrix)]
    predictions = []
    cursor = 0
    for target in targets:
        horizon = int((pd.Timestamp(target["target_at"]) - cutoff).total_seconds() // 60)
        predictions.append({**target, "value": generated[horizon][cursor % 12]})
        if horizon == 60:
            cursor += 1
    payload = {
        "schema_version": "1.0",
        "cycle_id": "cyc_smoke_test_20260920",
        "client_run_id": "smoke-test-dry-run",
        "data_cutoff": cutoff.isoformat(),
        "model": {
            "version": champion["version"],
            "trained_at": champion.get("created_at"),
            "training_data_end": champion["data_cutoff"],
            "git_commit": champion.get("git_commit"),
        },
        "predictions": predictions,
    }
    validated = SubmissionInput.model_validate(payload)
    if len(validated.predictions) != 48:
        raise RuntimeError("el payload no contiene exactamente 48 predicciones")
    return "payload válido; dry-run sin POST"


def infer_waiting_exits_zero() -> str:
    fake_clock = (
        "import src.infer as module\n"
        "class FakeAPI:\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *args): pass\n"
        "    def clock(self): return {'state': 'waiting'}\n"
        "module.PulsoTransmiClient = FakeAPI\n"
        "module.SupabaseDB = lambda: object()\n"
        "raise SystemExit(module.main())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", fake_clock],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"exit={completed.returncode}; stderr={completed.stderr.strip()}")
    if "waiting" not in completed.stdout:
        raise RuntimeError(f"salida inesperada: {completed.stdout.strip()}")
    return "exit code 0; salida waiting"


def main() -> int:
    print("Pulso TransMi smoke test")
    results = [
        check("1. reloj responde y estado válido", lambda: f"state={read_clock()['state']}"),
        check("2. collector idempotente en dos ejecuciones", collector_is_idempotent),
        check("3. champion produce 48 valores válidos", champion_produces_48_values),
        check("4. payload válido sin enviar submission", payload_is_valid),
        check("5. infer waiting termina con exit 0", infer_waiting_exits_zero),
    ]
    passed = sum(results)
    print(f"Resultado: {passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
