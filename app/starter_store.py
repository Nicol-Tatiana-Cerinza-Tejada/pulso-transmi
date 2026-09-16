from __future__ import annotations

import base64
import csv
import hashlib
import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class InvalidCursor(ValueError):
    pass


def _encode_cursor(parts: list[str]) -> str:
    payload = json.dumps(parts, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, expected_parts: int) -> list[str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidCursor("invalid cursor") from exc
    if not isinstance(value, list) or len(value) != expected_parts or not all(isinstance(item, str) for item in value):
        raise InvalidCursor("invalid cursor")
    return value


@dataclass(frozen=True)
class Page:
    data: list[dict[str, Any]]
    next_cursor: str | None


class StarterStore:
    DOWNLOADS = {"stations.csv", "observations.csv", "context.csv", "metadata.json"}

    def __init__(
        self,
        data_dir: Path,
        metadata: dict[str, Any],
        stations: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        contexts: list[dict[str, Any]],
    ) -> None:
        self.data_dir = data_dir
        self.metadata = metadata
        self.stations = stations
        self.station_ids = {station["station_id"] for station in stations}
        self.observations = observations
        self.contexts = contexts
        self._observation_keys = [(row["observed_at"], row["station_id"]) for row in observations]
        self._context_keys = [row["observed_at"] for row in contexts]

    @classmethod
    def load(cls, data_dir: Path) -> "StarterStore":
        data_dir = data_dir.resolve()
        metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
        stations = []
        with (data_dir / "stations.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                stations.append({
                    "station_id": row["station_id"],
                    "station_name": row["station_name"],
                    "corridor": row["corridor"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                })

        observations = []
        with (data_dir / "observations.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                observations.append({
                    "observed_at": datetime.fromisoformat(row["observed_at"]),
                    "station_id": row["station_id"],
                    "demand": int(row["demand"]),
                })

        contexts = []
        with (data_dir / "context.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                contexts.append({
                    "observed_at": datetime.fromisoformat(row["observed_at"]),
                    "rain_mm": float(row["rain_mm"]),
                    "rain_forecast": float(row["rain_forecast"]),
                    "temperature_c": float(row["temperature_c"]),
                    "temperature_forecast": float(row["temperature_forecast"]),
                    "event_intensity": float(row["event_intensity"]),
                })
        return cls(data_dir, metadata, stations, observations, contexts)

    def observation_page(
        self,
        *,
        station_id: str | None,
        start: datetime | None,
        end: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> Page:
        if station_id is not None and station_id not in self.station_ids:
            return Page([], None)
        position = bisect_left(self._observation_keys, (start, "")) if start is not None else 0
        if cursor is not None:
            raw_timestamp, raw_station = _decode_cursor(cursor, 2)
            cursor_key = (datetime.fromisoformat(raw_timestamp), raw_station)
            position = max(position, bisect_right(self._observation_keys, cursor_key))

        rows: list[dict[str, Any]] = []
        for row in self.observations[position:]:
            if end is not None and row["observed_at"] > end:
                break
            if station_id is not None and row["station_id"] != station_id:
                continue
            rows.append(row)
            if len(rows) == limit:
                break
        next_cursor = None
        if len(rows) == limit:
            last = rows[-1]
            next_cursor = _encode_cursor([last["observed_at"].isoformat(), last["station_id"]])
        return Page(rows, next_cursor)

    def context_page(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> Page:
        position = bisect_left(self._context_keys, start) if start is not None else 0
        if cursor is not None:
            raw_timestamp = _decode_cursor(cursor, 1)[0]
            position = max(position, bisect_right(self._context_keys, datetime.fromisoformat(raw_timestamp)))
        rows = []
        for row in self.contexts[position:]:
            if end is not None and row["observed_at"] > end:
                break
            rows.append(row)
            if len(rows) == limit:
                break
        next_cursor = _encode_cursor([rows[-1]["observed_at"].isoformat()]) if len(rows) == limit else None
        return Page(rows, next_cursor)

    def download(self, filename: str) -> tuple[Path, str]:
        if filename not in self.DOWNLOADS:
            raise FileNotFoundError(filename)
        path = self.data_dir / filename
        if filename == "metadata.json":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            digest = self.metadata["files"][filename]["sha256"]
        return path, digest
