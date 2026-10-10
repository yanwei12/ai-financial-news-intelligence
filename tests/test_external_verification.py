import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.ai import external_verification as provider, headline
from src.api.article_routes import get_session
from src.api.external_verification import router, _search_lock
from src.api.headline import router as headline_router
from src.database.models import Base, HeadlineAnalysis, ExternalVerification

ID = "a" * 64
SNAPSHOT = {"verdict": "supported", "prepared": {"title": "公司漲價", "original_text": "公司宣布部分產品漲價。"},
            "claims": [{"claim": "公司漲價", "external_verification": {"status": "not_enabled", "sources": []}}]}
URL = f"/headline/history/{ID}/claims/0/verify"


def grounded():
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "SUPPORTED\n公司宣布部分產品漲價。"}]},
        "groundingMetadata": {"webSearchQueries": ["公司 漲價 公告"],
            "groundingChunks": [{"web": {"uri": "https://example.com/announcement", "title": "公告"}}],
            "groundingSupports": [{"segment": {"text": "公司宣布部分產品漲價。", "startIndex": 10, "endIndex": 46}, "groundingChunkIndices": [0]}],
            "searchEntryPoint": {"renderedContent": "<div>Google Search</div>"}}}], "usageMetadata": {"totalTokenCount": 100}}


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(HeadlineAnalysis(cache_key=ID, document_id="b" * 64, result_json=json.dumps(SNAPSHOT)))
        session.commit()
    app = FastAPI(); app.include_router(router); app.include_router(headline_router)
    def db():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = db
    monkeypatch.setattr(provider, "settings", lambda: ("fake-key", "gemini-test"))
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_chinese_grounding_maps_only_metadata_sources():
    result = provider.parse_response(grounded(), "gemini-test")
    assert result["status"] == "completed" and result["verdict"] == "supported"
    assert result["sources"][0]["url"] == "https://example.com/announcement"
    assert result["sources"][0]["published_at"] is None
    assert result["evidence"][0]["text"] == "公司宣布部分產品漲價。"
    assert result["usage"]["totalTokenCount"] == 100


@pytest.mark.parametrize("change", ["queries", "supports", "bad_index", "fabricated_text", "unsafe_url", "negative_index"])
def test_no_grounding_never_claims_verified(change):
    data = grounded(); meta = data["candidates"][0]["groundingMetadata"]
    if change == "queries": meta["webSearchQueries"] = []
    if change == "supports": meta["groundingSupports"] = []
    if change == "bad_index": meta["groundingSupports"][0]["groundingChunkIndices"] = [99]
    if change == "negative_index": meta["groundingSupports"][0]["groundingChunkIndices"] = [-1]
    if change == "fabricated_text": meta["groundingSupports"][0]["segment"]["text"] = "模型未輸出的文字"
    if change == "unsafe_url": meta["groundingChunks"][0]["web"]["uri"] = "javascript:alert(1)"
    result = provider.parse_response(data, "gemini-test")
    assert result["status"] == "no_evidence" and result["verdict"] == "insufficient"
    assert result["sources"] == [] and result["evidence"] == []


@pytest.mark.parametrize("url", ["http://example.com", "https://127.0.0.1/", "https://foo.local/", "https://localhost/", "https://example.com@127.0.0.1", "https://example.com\\@evil.com", "https://example.com:bad", "data:text/html,x"])
def test_unsafe_urls(url):
    assert provider.safe_url(url) is None


def test_api_cache_history_and_expiration(client, monkeypatch):
    client, engine = client
    call = Mock(return_value=provider.parse_response(grounded(), "gemini-test"))
    monkeypatch.setattr(provider, "verify", call)
    first = client.post(URL)
    assert first.status_code == 200 and first.json()["cached"] is False
    assert client.post(URL).json()["cached"] is True and call.call_count == 1
    restored = client.get(f"/headline/history/{ID}").json()
    assert restored["verdict"] == "supported"  # internal verdict remains unchanged
    assert restored["claims"][0]["external_verification"]["sources"] == first.json()["sources"]
    with Session(engine) as session:
        row = session.scalar(select(ExternalVerification))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        session.commit()
    assert client.post(URL).json()["cached"] is False and call.call_count == 2
    monkeypatch.setattr(provider, "settings", lambda: ("fake-key", "gemini-different"))
    assert client.post(URL).json()["cached"] is False and call.call_count == 3


def test_failure_retry_preserves_analysis(client, monkeypatch):
    client, engine = client
    call = Mock(side_effect=headline.AnalysisError("timeout", "搜尋逾時", 504))
    monkeypatch.setattr(provider, "verify", call)
    assert client.post(URL).status_code == 504
    with Session(engine) as session:
        assert session.scalar(select(ExternalVerification)) is None
        assert json.loads(session.get(HeadlineAnalysis, ID).result_json) == SNAPSHOT
    call.side_effect = None; call.return_value = provider.parse_response(grounded(), "gemini-test")
    assert client.post(URL).status_code == 200


def test_not_found_busy_and_validation(client, monkeypatch):
    client, _ = client; call = Mock(); monkeypatch.setattr(provider, "verify", call)
    assert client.post(URL.replace(ID, "c" * 64)).status_code == 404
    assert client.post(URL.replace("/0/", "/1/")).status_code == 404
    assert client.post(URL.replace("/0/", "/-1/")).status_code == 422
    with _search_lock:
        response = client.post(URL)
        assert response.status_code == 429 and response.headers["retry-after"] == "10"
    call.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429, 500])
def test_provider_errors_hide_secrets(monkeypatch, status):
    post = Mock(return_value=Mock(status_code=status, text="SECRET"))
    monkeypatch.setattr(provider.requests, "post", post)
    with pytest.raises(headline.AnalysisError) as exc:
        provider.verify(SNAPSHOT, 0, "SECRET", "gemini-test")
    assert "SECRET" not in str(exc.value)
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_args.kwargs["json"]["tools"] == [{"google_search": {}}]


def test_no_key_timeout_and_bad_json(monkeypatch):
    post = Mock(side_effect=requests.Timeout("SECRET")); monkeypatch.setattr(provider.requests, "post", post)
    with pytest.raises(headline.AnalysisError) as exc: provider.verify(SNAPSHOT, 0, "", "gemini-test")
    assert exc.value.status == 503; post.assert_not_called()
    with pytest.raises(headline.AnalysisError) as exc: provider.verify(SNAPSHOT, 0, "SECRET", "gemini-test")
    assert exc.value.status == 504 and "SECRET" not in str(exc.value)
    post.side_effect = None; post.return_value = Mock(status_code=200)
    post.return_value.json.side_effect = ValueError("SECRET")
    with pytest.raises(headline.AnalysisError) as exc: provider.verify(SNAPSHOT, 0, "SECRET", "gemini-test")
    assert exc.value.code == "invalid_output"


@pytest.mark.parametrize("data", [{}, {"candidates": []}, {"candidates": [{"finishReason": "MAX_TOKENS"}]}, {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]}])
def test_malformed_provider_output(data):
    with pytest.raises(headline.AnalysisError): provider.parse_response(data, "gemini-test")
