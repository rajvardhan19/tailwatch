from fastapi.testclient import TestClient

from tailwatch.demo import research_demo
from tailwatch.product.server import app


def test_synthetic_run_repeats_exactly_and_never_claims_probability():
    first = research_demo()
    assert first == research_demo()
    assert first["synthetic"] and not first["production_enabled"]
    assert first["model_probability"] is None
    assert len(first["run"]["pairs"]) == 2


def test_dashboard_serves_assets_with_security_policy():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "SYNTHETIC DEMO" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/style.css").status_code == 200


def test_terminal_assets_are_local_and_land_data_carries_provenance():
    client = TestClient(app)
    page = client.get("/").text
    # Strict CSP: every script is a same-origin module, no inline code or CDN.
    assert (
        '<script type="module" src="/assets/app.js">' in page
        and "http" not in page.split("<body")[0]
    )
    assert "Simulated traffic is not real aircraft" in page
    script = client.get("/assets/globe.js")
    assert script.status_code == 200 and script.headers["cache-control"] == "no-cache"
    assert script.headers["x-content-type-options"] == "nosniff"
    land = client.get("/assets/land.json").json()
    assert "Natural Earth" in land["source"] and "public domain" in land["source"]
    points = land["points"]
    assert len(points) % 2 == 0 and len(points) > 4000
    assert all(
        -180 <= lon <= 180 and -90 <= lat <= 90
        for lon, lat in zip(points[::2], points[1::2], strict=True)
    )


def test_demo_export_places_only_its_synthetic_visit():
    [visit] = research_demo()["visits"]
    assert visit["synthetic"] is True and visit["evidence_id"] == "synthetic:visit:1"
    assert visit["origin"]["code"].startswith("SYN-") and visit["destination"]["code"].startswith(
        "SYN-"
    )
