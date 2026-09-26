"""Snapshot + entrenamiento candidato + promoción controlada."""

from __future__ import annotations

import argparse

from .snapshot import create_snapshot
from .db import SupabaseDB
from .train import train_and_register


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-bucket", default="dataset-snapshots")
    parser.add_argument("--model-bucket", default="model-artifacts")
    parser.add_argument("--origins", type=int, default=96)
    args = parser.parse_args()
    db = SupabaseDB()
    snapshot = create_snapshot(db, bucket=args.snapshot_bucket)
    result = train_and_register(
        snapshot["frame"],
        db=db,
        bucket=args.model_bucket,
        test_origins=args.origins,
        dataset_snapshot=snapshot,
    )
    print({"snapshot_id": snapshot["snapshot_id"], **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
