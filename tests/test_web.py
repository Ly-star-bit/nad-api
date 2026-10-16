"""The web page is served at / to browsers, while API clients keep getting the JSON index (no network)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)  # not used as a context manager, so the lifespan (upstream clients, Overture warmup) never runs


def test_root_serves_page_to_browsers():
    r = client.get("/", headers={"accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "v1/verify" in r.text


def test_root_keeps_json_index_for_api_clients():
    r = client.get("/", headers={"accept": "*/*"})
    assert r.status_code == 200
    assert r.json()["docs"] == "/docs"


def test_batch_page():
    r = client.get("/batch")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "v1/verify/batch" in r.text


def test_shared_assets_are_served_and_revalidated():
    for path, ctype in (
        ("/static/style.css", "text/css"), ("/static/common.js", "javascript"), ("/static/map.js", "javascript"),
        ("/static/vendor/leaflet/leaflet.js", "javascript"), ("/static/vendor/leaflet/leaflet.css", "text/css"),
    ):
        r = client.get(path)
        assert r.status_code == 200
        assert ctype in r.headers["content-type"]
        assert r.headers["cache-control"] == "no-cache"
