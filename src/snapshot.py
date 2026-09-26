"""Crea snapshots inmutables del histórico usado por entrenamiento."""

from __future__ import annotations

import argparse
import hashlib
from typing import Any

import pandas as pd

from .db import SupabaseDB


def load_observations(db: SupabaseDB, page_size: int = 1000) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            db.client.table("observations")
            .select("station_id,ts,value,released_at,source")
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
        raise RuntimeError("No hay observations para crear un snapshot")
    frame = pd.DataFrame(rows)
    frame["station_id"] = frame["station_id"].astype(str)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["value"] = pd.to_numeric(frame["value"], errors="raise").astype(int)
    return frame.sort_values(["ts", "station_id"]).reset_index(drop=True)


def canonical_csv(frame: pd.DataFrame) -> bytes:
    columns = ["station_id", "ts", "value", "released_at", "source"]
    output = frame.copy()
    for column in ("ts", "released_at"):
        if column in output:
            output[column] = pd.to_datetime(output[column], utc=True).map(
                lambda value: "" if pd.isna(value) else value.isoformat()
            )
    output = output.reindex(columns=columns, fill_value="")
    return output.to_csv(index=False, lineterminator="\n").encode("utf-8")


def create_snapshot(
    db: SupabaseDB,
    *,
    bucket: str = "dataset-snapshots",
) -> dict[str, Any]:
    frame = load_observations(db)
    content = canonical_csv(frame)
    digest = hashlib.sha256(content).hexdigest()
    # El hash forma parte del ID: repetir el job con los mismos datos es
    # idempotente y no crea versiones ficticias del dataset.
    snapshot_id = f"ds-{digest[:16]}"
    path = f"observations/{snapshot_id}.csv"
    try:
        db.upload_artifact(bucket, path, content)
    except Exception as exc:
        if not any(word in str(exc).lower() for word in ("already exists", "duplicate", "exists")):
            raise
    row = {
        "snapshot_id": snapshot_id,
        "sha256": digest,
        "artifact_path": f"{bucket}/{path}",
        "row_count": len(frame),
        "station_count": int(frame["station_id"].nunique()),
        "data_start": frame["ts"].min().isoformat(),
        "data_end": frame["ts"].max().isoformat(),
        "metadata": {
            "columns": ["station_id", "ts", "value", "released_at", "source"],
            "created_by": "src.snapshot",
        },
    }
    db.upsert("dataset_snapshots", row, on_conflict="sha256")
    print(
        f"Snapshot: {snapshot_id} | rows={len(frame)} | "
        f"range={row['data_start']}..{row['data_end']} | sha256={digest}"
    )
    return {**row, "frame": frame}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default="dataset-snapshots")
    args = parser.parse_args()
    create_snapshot(SupabaseDB(), bucket=args.bucket)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
