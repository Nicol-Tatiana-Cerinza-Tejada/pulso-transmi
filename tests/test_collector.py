from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.collector import collect_once


@dataclass
class FakeDB:
    rows: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    cursor: str | None = None

    def start_collector_run(self) -> int:
        self.runs.append({"status": "running", "cursor": None})
        return len(self.runs) - 1

    def latest_confirmed_cursor(self) -> str | None:
        return self.cursor

    def observation_count(self) -> int:
        return len(self.rows)

    def upsert_observations(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.rows[(row["station_id"], row["ts"])] = row

    def finish_collector_run(self, run_id: int, **values: Any) -> None:
        self.runs[run_id].update(values)
        if values["status"] == "succeeded":
            self.cursor = values["cursor"]


class FakeAPI:
    def __init__(self, pages: list[dict[str, Any]], state: str = "running") -> None:
        self.pages = list(pages)
        self.state = state
        self.cursors: list[str | None] = []

    def clock(self) -> dict[str, str]:
        return {"state": self.state}

    def stream_observations(self, *, cursor: str | None, limit: int) -> dict[str, Any]:
        self.cursors.append(cursor)
        return self.pages.pop(0)


def observation() -> dict[str, Any]:
    return {
        "station_id": "02300",
        "observed_at": "2026-09-16T10:15:00-05:00",
        "demand": 341,
        "released_at": "2026-09-16T10:30:03-05:00",
    }


def test_empty_stream_finishes_successfully_without_upsert() -> None:
    db = FakeDB(cursor="cursor-confirmado")
    api = FakeAPI([{"data": [], "count": 0, "next_cursor": None}])

    result = collect_once(api, db)

    assert result["status"] == "succeeded"
    assert result["rows"] == 0
    assert result["rows_new"] == 0
    assert db.rows == {}
    assert db.runs[-1]["cursor"] == "cursor-confirmado"


def test_repeated_data_is_idempotent_and_checkpoint_follows_upsert() -> None:
    db = FakeDB()
    pages = [
        {"data": [observation()], "count": 1, "next_cursor": "cursor-1"},
        {"data": [], "count": 0, "next_cursor": None},
    ]

    first = collect_once(FakeAPI(pages), db)
    second = collect_once(
        FakeAPI([{"data": [observation()], "count": 1, "next_cursor": None}]), db
    )

    assert first["rows_new"] == 1
    assert second["rows_new"] == 0
    assert db.cursor == "cursor-1"
    assert len(db.rows) == 1
    assert len(db.runs) == 2
    assert all(run["status"] == "succeeded" for run in db.runs)
