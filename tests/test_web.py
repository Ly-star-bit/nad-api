"""The web page is served at / to browsers, while API clients keep getting the JSON index (no network)."""

import json

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
    for path, ctype in (("/static/style.css", "text/css"), ("/static/common.js", "javascript"), ("/static/map.js", "javascript")):
        r = client.get(path)
        assert r.status_code == 200
        assert ctype in r.headers["content-type"]
        assert r.headers["cache-control"] == "no-cache"
    for path, ctype in (("/static/vendor/leaflet/leaflet.js", "javascript"), ("/static/vendor/leaflet/leaflet.css", "text/css")):
        r = client.get(path)
        assert r.status_code == 200
        assert ctype in r.headers["content-type"]
        assert "max-age" in r.headers["cache-control"]


def test_responses_are_compressed():
    r = client.get("/static/vendor/leaflet/leaflet.js", headers={"accept-encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert len(r.content) > 100_000  # httpx decompressed it
    r = client.get("/batch", headers={"accept-encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"


def test_batch_streams_one_line_per_address():
    # invalid input is answered before any upstream call, so no lifespan / network is needed
    app.state.upstream, app.state.overture = None, None
    r = client.post("/v1/verify/batch?stream=true", json={"addresses": [{"address": "asdkjh"}, {"address": "qwe zzz"}]})
    assert r.headers["content-type"].startswith("application/x-ndjson")
    items = [json.loads(line) for line in r.text.splitlines()]
    assert sorted(it["index"] for it in items) == [0, 1]
    assert all(it["result"]["status"] == "invalid_input" for it in items)


def test_oversized_input_is_rejected():
    r = client.post("/v1/verify", json={"address": "1 " + "x" * 5000})
    assert r.status_code == 422
    r = client.get("/v1/verify", params={"address": "1 " + "x" * 5000})
    assert r.status_code == 422
