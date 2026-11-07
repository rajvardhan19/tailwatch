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
