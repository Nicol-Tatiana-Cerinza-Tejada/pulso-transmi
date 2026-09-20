"""Promoción controlada de una versión anterior de modelo a ``champion``."""

from __future__ import annotations

import argparse
import io
import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .db import SupabaseDB
from .infer import build_target_features, load_observations_until
from .train import encode_features


BUCKET = "model-artifacts"
HORIZONS = (15, 30, 45, 60)


def _model(db: SupabaseDB, version: str) -> dict[str, Any]:
    response = (
        db.client.table("model_versions")
        .select("*")
        .eq("version", version)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise RuntimeError(f"No existe model_versions.version={version!r}")
    return dict(response.data[0])


def _champion(db: SupabaseDB) -> dict[str, Any] | None:
    response = (
        db.client.table("model_versions")
        .select("*")
        .eq("status", "champion")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return dict(response.data[0]) if response.data else None


def _artifact_path(model: dict[str, Any], bucket: str) -> str:
    path = str(model.get("artifact_path") or "")
    if not path:
        raise RuntimeError(f"La versión {model['version']} no tiene artifact_path")
    prefix = f"{bucket}/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def verify_artifact_and_inference(
    db: SupabaseDB,
    model: dict[str, Any],
    *,
    bucket: str = BUCKET,
) -> dict[str, Any]:
    """Descarga el artefacto y ejecuta 48 predicciones sobre datos hasta el corte."""
    path = _artifact_path(model, bucket)
    try:
        import joblib

        artifact_bytes = db.download_artifact(bucket, path)
        artifact = joblib.load(io.BytesIO(artifact_bytes))
    except Exception as exc:
        raise RuntimeError(f"No se pudo cargar {bucket}/{path} desde Storage: {exc}") from exc

    if not isinstance(artifact, dict) or not artifact.get("models") or not artifact.get("feature_columns"):
        raise RuntimeError("El artefacto no contiene models y feature_columns")

    cutoff = pd.Timestamp(model["data_cutoff"])
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    history = load_observations_until(db, cutoff)
    stations = sorted(str(value) for value in history["station_id"].unique())
    if len(stations) != 12:
        raise RuntimeError(f"La inferencia de prueba requiere 12 estaciones; hay {len(stations)}")

    targets = [
        {
            "station_id": station,
            "target_at": (cutoff + pd.Timedelta(minutes=horizon)).isoformat(),
        }
        for station in stations
        for horizon in HORIZONS
    ]
    features, positions = build_target_features(history, targets, cutoff)
    values: list[float] = []
    for horizon in HORIZONS:
        indexes = positions.get(horizon, [])
        if len(indexes) != len(stations):
            raise RuntimeError(f"Faltan targets para el horizonte +{horizon}")
        try:
            model_object = artifact["models"][horizon]
        except KeyError as exc:
            raise RuntimeError(f"El artefacto no contiene el horizonte +{horizon}") from exc
        matrix, _ = encode_features(features.iloc[indexes], artifact["feature_columns"])
        predictions = model_object.predict(matrix)
        if len(predictions) != len(indexes):
            raise RuntimeError(f"Inferencia incompleta para el horizonte +{horizon}")
        for value in predictions:
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0:
                raise RuntimeError(f"Inferencia inválida: {numeric!r}")
            values.append(numeric)
    if len(values) != 48:
        raise RuntimeError(f"La inferencia produjo {len(values)} valores en vez de 48")
    return {"artifact_path": f"{bucket}/{path}", "cutoff": cutoff.isoformat(), "values": 48}


def _audit_insert(db: SupabaseDB, version: str, previous: str | None, reason: str, verification: dict[str, Any]) -> dict[str, Any]:
    rows = db.insert(
        "model_promotion_events",
        {
            "requested_version": version,
            "previous_version": previous,
            "action": "rollback",
            "reason": reason,
            "status": "pending",
            "verification": verification,
        },
    )
    if not rows:
        raise RuntimeError("Supabase no devolvió el evento de rollback")
    return rows[0]


def rollback_version(
    version: str,
    *,
    reason: str = "rollback manual solicitado",
    bucket: str = BUCKET,
    db: SupabaseDB | None = None,
) -> dict[str, Any]:
    """Verifica y promueve una versión, dejando auditoría del cambio."""
    if not version.strip():
        raise ValueError("version no puede estar vacía")
    if not reason.strip():
        raise ValueError("reason no puede estar vacío")

    db = db or SupabaseDB()
    target = _model(db, version)
    previous_model = _champion(db)
    previous = previous_model["version"] if previous_model else None
    verification = verify_artifact_and_inference(db, target, bucket=bucket)
    event = _audit_insert(db, version, previous, reason.strip(), verification)
    event_id = event.get("id")
    changed = False
    try:
        if previous != version:
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
        # Se intenta restaurar el estado anterior; el evento conserva el fallo.
        if changed and previous:
            try:
                db.client.table("model_versions").update({"status": "retired"}).eq("version", version).execute()
                db.client.table("model_versions").update({"status": "champion"}).eq("version", previous).execute()
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
        raise RuntimeError(f"No se pudo completar el rollback: {exc}") from exc

    return {
        "status": "succeeded",
        "version": version,
        "previous_version": previous,
        "changed": changed,
        "event_id": event_id,
        "reason": reason.strip(),
        **verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Promueve una versión validada a champion")
    parser.add_argument("version", help="Valor exacto de model_versions.version")
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--reason", default="rollback manual solicitado")
    args = parser.parse_args()
    try:
        result = rollback_version(args.version, reason=args.reason, bucket=args.bucket)
        print(result)
        return 0
    except Exception as exc:
        print(f"rollback error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
