"""
Article links, daily cap, unified length limit, latency record, explanation
grounding and request limits for the analysis endpoint. The model is faked; no
network is used.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.ai import headline
from src.api import article_routes
from src.api import headline as headline_api
from src.api.article_routes import get_session
from src.api.headline import router as headline_router
from src.api.ratelimit import RateLimiter
from src.database.article_repository import ArticleRepository
from src.database.models import ArticleAnalysisLink, Base, HeadlineAnalysis
from src.ingestion.article_fetcher import FailureReason, FetchResult
from src.processing.article_text import content_hash
from src.processing.cleaner import prepare_article
from tests.test_headline import output

TITLE = "公司全面漲價"
BODY = "正在評估北美部分產品，尚未決定。\n\n其他市場不在範圍內。"


@pytest.fixture
def env(monkeypatch):
    """(client, engine) with both routers, a fake model and an in-memory database."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(article_routes.router)
    app.include_router(headline_router)

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = db
    monkeypatch.setattr(headline, "settings", lambda: ("fake-key", "gemini-test"))
    call = Mock(return_value=(output(), {"totalTokenCount": 100}, "gemini-test-001"))
    monkeypatch.setattr(headline, "call_gemini", call)
    with TestClient(app) as client:
        client.call = call
        yield client, engine
    engine.dispose()


def save_article(engine, title=TITLE, body=BODY):
    with Session(engine) as session:
        article, _ = ArticleRepository(session).save_confirmed(
            url=None, title=title, source=None, published_at=None, fetched_at=None, body=body,
            content_hash=content_hash(body), input_origin="pasted", fetch_failure_reason=None)
        return article.id


def request_for(title=TITLE, body=BODY, **extra):
    prepared = prepare_article(title, body)
    return {"title": title, "content": body, "paragraph_mode": "blank_lines",
            "document_id": prepared.document_id, **extra}


def count(engine, model):
    with Session(engine) as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


# ------------------------------------------------------------- linking to a saved article


def test_analysis_is_linked_to_the_saved_article_and_can_be_listed(env):
    client, engine = env
    article_id = save_article(engine)
    response = client.post("/headline/analyze", json=request_for(article_id=article_id))
    assert response.status_code == 200 and response.json()["article_id"] == article_id

    listed = client.get(f"/articles/{article_id}/analyses").json()
    assert listed["article_id"] == article_id and len(listed["analyses"]) == 1
    item = listed["analyses"][0]
    assert item["verdict"] == "missing_conditions" and item["claims"] == 1
    assert item["model"] == "gemini-test" and item["prompt_version"] == headline.PROMPT_VERSION
    assert isinstance(item["latency_ms"], int)


def test_repeating_the_analysis_does_not_duplicate_the_link_or_call_the_model(env):
    client, engine = env
    article_id = save_article(engine)
    client.post("/headline/analyze", json=request_for(article_id=article_id))
    again = client.post("/headline/analyze", json=request_for(article_id=article_id))
    assert again.json()["cached"] is True and client.call.call_count == 1
    assert count(engine, ArticleAnalysisLink) == 1 and count(engine, HeadlineAnalysis) == 1


def test_a_cached_analysis_can_be_linked_to_an_article_saved_later(env):
    client, engine = env
    client.post("/headline/analyze", json=request_for())                       # analysed before it was saved
    article_id = save_article(engine)
    linked = client.post("/headline/analyze", json=request_for(article_id=article_id))
    assert linked.json()["cached"] is True and client.call.call_count == 1
    assert len(client.get(f"/articles/{article_id}/analyses").json()["analyses"]) == 1


def test_a_second_model_gives_a_second_analysis_of_the_same_article(env, monkeypatch):
    client, engine = env
    article_id = save_article(engine)
    client.post("/headline/analyze", json=request_for(article_id=article_id))
    monkeypatch.setattr(headline, "settings", lambda: ("fake-key", "gemini-other"))
    client.post("/headline/analyze", json=request_for(article_id=article_id))
    models = {a["model"] for a in client.get(f"/articles/{article_id}/analyses").json()["analyses"]}
    assert models == {"gemini-test", "gemini-other"}


def test_linking_to_a_different_text_or_a_missing_article_is_refused(env):
    client, engine = env
    article_id = save_article(engine)
    other = request_for(title="完全不同的標題", article_id=article_id)
    assert client.post("/headline/analyze", json=other).status_code == 409
    assert client.post("/headline/analyze", json=request_for(article_id=999)).status_code == 404
    assert client.call.call_count == 0                                           # refused before any model call
    assert count(engine, ArticleAnalysisLink) == 0


def test_analysis_without_an_article_id_still_works_and_lists_are_empty_or_404(env):
    client, engine = env
    assert client.post("/headline/analyze", json=request_for()).json()["article_id"] is None
    article_id = save_article(engine, title="沒分析過的文章", body=BODY + "\n\n另一段。")
    assert client.get(f"/articles/{article_id}/analyses").json()["analyses"] == []
    assert client.get("/articles/999/analyses").status_code == 404


# ------------------------------------------------------------- daily cap


def test_daily_cap_blocks_new_model_calls_but_not_cached_results(env, monkeypatch):
    client, engine = env
    monkeypatch.setenv("HEADLINE_DAILY_LIMIT", "2")
    assert client.post("/headline/analyze", json=request_for(title="標題一")).status_code == 200
    assert client.post("/headline/analyze", json=request_for(title="標題二")).status_code == 200
    blocked = client.post("/headline/analyze", json=request_for(title="標題三"))
    assert blocked.status_code == 429 and blocked.json()["detail"]["code"] == "daily_limit"
    assert client.call.call_count == 2
    assert client.post("/headline/analyze", json=request_for(title="標題一")).json()["cached"] is True


def test_yesterdays_analyses_do_not_count_toward_today(env, monkeypatch):
    client, engine = env
    monkeypatch.setenv("HEADLINE_DAILY_LIMIT", "1")
    with Session(engine) as session:
        session.add(HeadlineAnalysis(cache_key="a" * 64, document_id="b" * 64, result_json="{}",
                                     created_at=datetime.now(timezone.utc) - timedelta(days=2)))
        session.commit()
        assert headline_api.analyses_today(session) == 0
    assert client.post("/headline/analyze", json=request_for()).status_code == 200


@pytest.mark.parametrize("value, expected", [("5", 5), ("", 50), ("abc", 50), ("0", 50), ("-3", 50)])
def test_daily_limit_setting_falls_back_to_the_default(monkeypatch, value, expected):
    monkeypatch.setenv("HEADLINE_DAILY_LIMIT", value)
    assert headline_api.daily_limit() == expected


# ------------------------------------------------------------- one length rule


def test_length_limit_counts_characters_not_blank_lines(env):
    client, _ = env
    # 20,000 characters in 20 paragraphs: the blank lines push the raw length past 20,000.
    body = "\n\n".join(["星" * 1000] * 20)
    assert len(body) > 20000
    assert client.post("/headline/analyze", json=request_for(body=body)).status_code == 200
    too_long = "\n\n".join(["星" * 1000] * 20) + "\n\n多"
    refused = client.post("/headline/analyze", json=request_for(body=too_long))
    assert refused.status_code == 422 and "20,000" in refused.json()["detail"]
    huge = client.post("/headline/analyze", json=request_for(body="星" * 60001))
    assert huge.status_code == 422                                              # raw payload cap


# ------------------------------------------------------------- latency record


def test_result_records_latency():
    article = prepare_article(TITLE, BODY)
    headline_call = Mock(return_value=(output(), {}, "m"))
    original = headline.call_gemini
    headline.call_gemini = headline_call
    try:
        result = headline.analyze(article, "key", "model")
    finally:
        headline.call_gemini = original
    assert isinstance(result["latency_ms"], int) and result["latency_ms"] >= 0


# ------------------------------------------------------------- explanation grounding


def result_with(explanation="內文只說正在評估北美部分產品。", claim="公司全面漲價", summary="標題省略評估條件。"):
    return json.dumps({"summary": summary, "claims": [{
        "claim": claim, "verdict": "missing_conditions", "explanation": explanation,
        "gap_type": "scope", "evidence_ids": ["P001"]}]}, ensure_ascii=False)


ARTICLE = prepare_article(
    "台積電 TrendForce 預估 2028 年", "TrendForce 指出，CoWoS-L 至少到 2028 年，訂單約 1,200 億元，成長 3.5%。\n\n第二段。")


@pytest.mark.parametrize("explanation", [
    "內文提到 2028 年與 3.5% 的成長。",            # numbers from the body
    "訂單約 1200 億元。",                            # 1,200 written without the comma
    "訂單約１２００億元。",                          # full-width digits
    "依 P001 與 P002 判斷。",                        # paragraph ids are not article content
    "TrendForce 認為 CoWoS-L 仍是主流。",           # names from the article
    "trendforce 認為。",                              # case does not matter for names
    "這屬於 scope 落差，涉及 AI 需求。",             # lowercase words and short acronyms are not policed
])
def test_grounded_explanations_pass(explanation):
    assert headline.ungrounded_terms([explanation], ARTICLE) == []
    assert headline.validate_result(result_with(explanation), ARTICLE)["verdict"] == "missing_conditions"


@pytest.mark.parametrize("explanation, missing", [
    ("營收成長35%。", ["35"]),
    ("內文提到 Nvidia 的訂單。", ["Nvidia"]),
    ("預計 2030 年量產。", ["2030"]),
    ("採用 N2P 製程。", ["N2P"]),
])
def test_ungrounded_terms_are_rejected_and_named(explanation, missing):
    assert headline.ungrounded_terms([explanation], ARTICLE) == missing
    with pytest.raises(headline.AnalysisError) as error:
        headline.validate_result(result_with(explanation), ARTICLE)
    assert error.value.code == "ungrounded_output" and missing[0] in error.value.message


def test_claim_text_and_summary_are_checked_too():
    for kwargs in ({"claim": "公司營收成長99%"}, {"summary": "整體看 Intel 的份額"}):
        with pytest.raises(headline.AnalysisError) as error:
            headline.validate_result(result_with(**kwargs), ARTICLE)
        assert error.value.code == "ungrounded_output"


def test_ungrounded_result_is_a_502_and_is_not_cached(env):
    client, engine = env
    client.call.return_value = (result_with("營收成長35%。"), {}, "m")
    response = client.post("/headline/analyze", json=request_for())
    assert response.status_code == 502 and response.json()["detail"]["code"] == "ungrounded_output"
    assert count(engine, HeadlineAnalysis) == 0


# ------------------------------------------------------------- request limits


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_rate_limiter_window_keys_and_eviction():
    clock = Clock()
    limiter = RateLimiter(2, window=60, clock=clock)
    assert limiter.hit("a") is None and limiter.hit("a") is None
    assert limiter.hit("a") == pytest.approx(60)
    assert limiter.hit("b") is None                                             # other clients are independent
    clock.now += 30
    assert limiter.hit("a") == pytest.approx(30)
    clock.now += 31
    assert limiter.hit("a") is None                                             # the oldest hits aged out

    small = RateLimiter(1, window=60, clock=clock, max_clients=3)
    for name in ("x", "y", "z"):
        small.hit(name)
    clock.now += 61
    small.hit("w")                                                              # triggers cleanup of idle clients
    assert set(small._hits) == {"w"}


def fake_fetch(monkeypatch):
    result = FetchResult(url="u", fetched_at=datetime.now(timezone.utc), ok=False,
                         failure_reason=FailureReason.NO_BODY, message="x")
    monkeypatch.setattr(article_routes, "fetch_article", lambda url: result)


def test_preview_is_limited_per_client_with_retry_after(env, monkeypatch):
    client, _ = env
    fake_fetch(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_PREVIEW", "2")
    for _ in range(2):
        assert client.post("/articles/preview", json={"url": "https://tw.stock.yahoo.com/x"}).status_code == 200
    blocked = client.post("/articles/preview", json={"url": "https://tw.stock.yahoo.com/x"})
    assert blocked.status_code == 429 and blocked.json()["detail"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1


def test_zero_turns_a_limit_off_and_junk_uses_the_default(env, monkeypatch):
    client, _ = env
    fake_fetch(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_PREVIEW", "0")
    assert all(client.post("/articles/preview", json={"url": "https://tw.stock.yahoo.com/x"}).status_code == 200
               for _ in range(25))                                              # default is 20/min
    monkeypatch.setenv("RATE_LIMIT_PREVIEW", "junk")
    statuses = [client.post("/articles/preview", json={"url": "https://tw.stock.yahoo.com/x"}).status_code
                for _ in range(25)]
    assert statuses.count(200) == 20 and statuses.count(429) == 5


def test_forwarded_header_is_ignored_unless_proxy_is_trusted(env, monkeypatch):
    client, _ = env
    fake_fetch(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_PREVIEW", "1")
    url = {"url": "https://tw.stock.yahoo.com/x"}
    assert client.post("/articles/preview", json=url, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    # Untrusted: a forged header must not buy a fresh allowance.
    assert client.post("/articles/preview", json=url, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429

    from src.api import ratelimit
    ratelimit.reset_all()
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    assert client.post("/articles/preview", json=url, headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}).status_code == 200
    assert client.post("/articles/preview", json=url, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    assert client.post("/articles/preview", json=url, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429


def test_analysis_endpoint_is_limited_before_any_model_call(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("RATE_LIMIT_ANALYZE", "1")
    assert client.post("/headline/analyze", json=request_for(title="標題一")).status_code == 200
    blocked = client.post("/headline/analyze", json=request_for(title="標題二"))
    assert blocked.status_code == 429 and blocked.json()["detail"]["code"] == "rate_limited"
    assert client.call.call_count == 1
