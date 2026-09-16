import os

os.environ["SKIP_DB_STARTUP"] = "true"

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_meta_describes_safe_static_cut_and_stream() -> None:
    with TestClient(app) as client:
        payload = client.get("/v1/meta").json()
    assert payload["mode"] == "starter-and-competition-stream"
    assert payload["dataset"]["observation_rows"] == 51_840
    assert payload["dataset"]["future_included"] is False
    assert payload["links"]["stream_observations"] == "/v1/stream/observations"


def test_stations_preserve_official_text_ids() -> None:
    with TestClient(app) as client:
        payload = client.get("/v1/stations").json()
    assert payload["count"] == 12
    assert payload["data"][0]["station_id"] == "03000"


def test_observations_are_paginated_without_duplicates() -> None:
    with TestClient(app) as client:
        first = client.get("/v1/observations", params={"limit": 7}).json()
        second = client.get(
            "/v1/observations", params={"limit": 7, "cursor": first["next_cursor"]}
        ).json()
    assert first["count"] == 7
    assert second["count"] == 7
    first_keys = {(item["observed_at"], item["station_id"]) for item in first["data"]}
    second_keys = {(item["observed_at"], item["station_id"]) for item in second["data"]}
    assert first_keys.isdisjoint(second_keys)


def test_observations_filter_by_station() -> None:
    with TestClient(app) as client:
        payload = client.get(
            "/v1/observations", params={"station_id": "07107", "limit": 20}
        ).json()
    assert payload["count"] == 20
    assert {item["station_id"] for item in payload["data"]} == {"07107"}


def test_invalid_cursor_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/observations", params={"cursor": "not-a-cursor"})
    assert response.status_code == 400


def test_naive_timestamp_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/v1/observations", params={"start": "2026-08-01T00:00:00"}
        )
    assert response.status_code == 422
    assert "timezone" in response.json()["detail"]


def test_context_and_download() -> None:
    with TestClient(app) as client:
        context = client.get("/v1/context", params={"limit": 3}).json()
        download = client.get("/v1/downloads/stations.csv")
    assert context["count"] == 3
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert "sha256:" in download.headers["etag"]
