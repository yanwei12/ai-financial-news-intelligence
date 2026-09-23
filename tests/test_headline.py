import json
from unittest.mock import Mock

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.ai import headline
from src.api.article_routes import get_session
from src.api.headline import router, _analysis_lock
from src.database.models import Base
from src.processing.cleaner import prepare_article


def output(verdict="missing_conditions", ids=None):
    return json.dumps({"summary": "標題省略評估條件。", "claims": [{
        "claim": "公司全面漲價", "verdict": verdict,
        "explanation": "內文只說正在評估北美部分產品。",
        "gap_type": {"missing_conditions": "scope", "supported": "none", "contradicted": "contradiction", "insufficient": "insufficient"}[verdict],
        "evidence_ids": ["P001"] if ids is None else ids,
    }]}, ensure_ascii=False)


@pytest.fixture
def article():
    return prepare_article("公司全面漲價", "正在評估北美部分產品，尚未決定。\n\n其他市場不在範圍內。")


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(router)
    def db():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = db
    monkeypatch.setattr(headline, "settings", lambda: ("fake-key", "gemini-test"))
    with TestClient(app) as client:
        yield client
    engine.dispose()


def payload(article):
    return {"title": article.title, "content": article.original_text,
            "paragraph_mode": article.paragraph_mode, "document_id": article.document_id}


@pytest.mark.parametrize("verdict", ["supported", "missing_conditions", "contradicted", "insufficient"])
def test_four_verdicts_and_source_evidence(article, verdict):
    result = headline.validate_result(output(verdict), article)
    assert result["verdict"] == verdict
    assert result["claims"][0]["evidence"][0]["text"] == article.paragraphs[0].text


@pytest.mark.parametrize("raw", ["not json", "{}", output(ids=["P999"]), output(ids=[]),
                                  output().replace('"scope"', '"none"')])
def test_rejects_bad_output(article, raw):
    with pytest.raises(headline.AnalysisError) as e:
        headline.validate_result(raw, article)
    assert e.value.code == "invalid_output"


def test_insufficient_can_have_no_evidence(article):
    assert headline.validate_result(output("insufficient", []), article)["claims"][0]["evidence"] == []


def test_api_cache_and_invalidation(client, monkeypatch, article):
    call = Mock(return_value=(output(), {"totalTokenCount": 100}, "gemini-test-001"))
    monkeypatch.setattr(headline, "call_gemini", call)
    first = client.post("/headline/analyze", json=payload(article))
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert first.json()["prepared"]["original_text"] == article.original_text
    second = client.post("/headline/analyze", json=payload(article))
    assert second.json()["cached"] is True
    assert call.call_count == 1
    stale = payload(article) | {"title": "改過的標題"}
    assert client.post("/headline/analyze", json=stale).status_code == 409
    changed = prepare_article("改過的標題", article.original_text)
    assert client.post("/headline/analyze", json=payload(changed)).status_code == 200
    assert call.call_count == 2


def test_invalid_response_not_cached(client, monkeypatch, article):
    call = Mock(return_value=(output(ids=["P999"]), {}, "test"))
    monkeypatch.setattr(headline, "call_gemini", call)
    assert client.post("/headline/analyze", json=payload(article)).status_code == 502
    call.return_value = (output(), {}, "test")
    assert client.post("/headline/analyze", json=payload(article)).json()["cached"] is False


def test_busy_and_limits(client, article):
    with _analysis_lock:
        assert client.post("/headline/analyze", json=payload(article)).status_code == 429
    assert client.post("/headline/analyze", json=payload(article) | {"content": "字" * 20001}).status_code == 422
    many = prepare_article("標題", "\n\n".join(["段落"] * 401))
    assert client.post("/headline/analyze", json=payload(many)).status_code == 422


def test_missing_key_does_not_call_network(article, monkeypatch):
    post = Mock()
    monkeypatch.setattr(headline.requests, "post", post)
    with pytest.raises(headline.AnalysisError) as e:
        headline.call_gemini(article, "", "gemini-test")
    assert e.value.status == 503
    post.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_provider_errors_do_not_leak_response(article, monkeypatch, status):
    response = Mock(status_code=status, text="SECRET")
    monkeypatch.setattr(headline.requests, "post", Mock(return_value=response))
    with pytest.raises(headline.AnalysisError) as e:
        headline.call_gemini(article, "SECRET", "gemini-test")
    assert "SECRET" not in str(e.value)
    assert e.value.status == (429 if status == 429 else 502)


def test_timeout_is_explicit(article, monkeypatch):
    monkeypatch.setattr(headline.requests, "post", Mock(side_effect=requests.Timeout("SECRET")))
    with pytest.raises(headline.AnalysisError) as e:
        headline.call_gemini(article, "SECRET", "gemini-test")
    assert e.value.status == 504
    assert "SECRET" not in str(e.value)


def test_request_and_incomplete_response(article, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"candidates": [{"finishReason": "MAX_TOKENS"}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(headline.requests, "post", post)
    with pytest.raises(headline.AnalysisError):
        headline.call_gemini(article, "SECRET", "gemini-test")
    args = post.call_args.kwargs
    assert args["allow_redirects"] is False
    assert "SECRET" not in post.call_args.args[0]
    sent = json.loads(args["json"]["contents"][0]["parts"][0]["text"])
    assert sent["paragraphs"][0]["text"] == article.paragraphs[0].text


def test_combined_app_pages_and_routes(monkeypatch):
    from src.api.main import app
    called = Mock()
    monkeypatch.setattr("src.api.main.create_tables", called)
    with TestClient(app) as client:
        assert client.get("/paragraphs").status_code == 200
        assert 'id="analyze-button"' in client.get("/paragraphs").text
        assert 'id="analyze-button"' in client.get("/check").text
        assert "post" in client.get("/openapi.json").json()["paths"]["/headline/analyze"]
    called.assert_called_once()


def test_billing_failure_is_actionable_and_not_cached(client, monkeypatch, article):
    response = Mock(status_code=402, text="SECRET provider details")
    monkeypatch.setattr(headline.requests, "post", Mock(return_value=response))
    failed = client.post("/headline/analyze", json=payload(article))
    assert failed.status_code == 402
    assert failed.json()["detail"]["code"] == "billing_required"
    assert "付款" in failed.json()["detail"]["message"]
    assert "SECRET" not in failed.text
    monkeypatch.setattr(headline, "call_gemini", Mock(return_value=(output(), {}, "test")))
    recovered = client.post("/headline/analyze", json=payload(article))
    assert recovered.status_code == 200
    assert recovered.json()["cached"] is False


def test_truncated_output_explains_usage(article, monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"candidates": [{"finishReason": "MAX_TOKENS"}]}
    monkeypatch.setattr(headline.requests, "post", Mock(return_value=response))
    with pytest.raises(headline.AnalysisError) as error:
        headline.call_gemini(article, "SECRET", "gemini-test")
    assert error.value.code == "output_truncated"
    assert "token" in error.value.message
