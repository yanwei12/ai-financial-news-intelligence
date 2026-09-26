from fastapi.testclient import TestClient

from src.api.main import app


def test_home_and_old_dashboard_open_checker():
    client = TestClient(app)
    for path in ("/", "/dashboard"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/check"
    page = client.get("/check")
    assert page.status_code == 200
    assert 'id="analysis-form"' in page.text
    assert 'href="/dashboard"' not in page.text


def test_sentiment_routes_removed_and_headline_routes_preserved():
    client = TestClient(app)
    assert client.post("/analyze", json={}).status_code == 404
    assert client.get("/analytics/sentiment").status_code == 404
    paths = client.get("/openapi.json").json()["paths"]
    assert "/analyze" not in paths
    assert "/analytics/sentiment" not in paths
    for path in ("/articles/preview", "/articles/prepare", "/headline/analyze", "/news"):
        assert path in paths
