"""
API tests for the article-input flow. The app under test contains only the
article router, with an in-memory database and a stubbed fetcher.
"""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api import article_routes
from src.database.models import Article, Base
from src.ingestion.article_fetcher import FailureReason, FetchResult
from src.processing.article_text import content_hash

URL = "https://tw.stock.yahoo.com/news/star-river-123.html"
BODY = "\n\n".join(["星河公司今日宣布推出新產品，預計下季供貨，首批以北美客戶為主。"] * 6)
FETCHED_AT = datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc)
PUBLISHED_AT = datetime(2026, 9, 19, 5, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine):
    app = FastAPI()
    app.include_router(article_routes.router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[article_routes.get_session] = session
    return TestClient(app)


def confirm_payload(**overrides):
    payload = {
        "url": URL,
        "title": "星河公司宣布新產品",
        "body": BODY,
        "fetched_body_hash": content_hash(BODY),
        "published_at": PUBLISHED_AT.isoformat(),
        "fetched_at": FETCHED_AT.isoformat(),
    }
    payload.update(overrides)
    return payload


def count_articles(engine):
    with Session(engine) as db:
        return db.execute(select(func.count()).select_from(Article)).scalar_one()


# ------------------------------------------------------------- page


def test_check_page_renders_with_limits(client):
    response = client.get("/check")
    assert response.status_code == 200
    assert "貼上新聞網址" in response.text
    assert 'id="analyze-button"' in response.text
    assert "tw.stock.yahoo.com" in response.text
    # Pasting text can be chosen up front as well as after a failed fetch.
    assert 'id="to-paste"' in response.text


# ------------------------------------------------------------- preview


def test_preview_returns_fetched_article(client, monkeypatch):
    fake = FetchResult(
        url=URL, fetched_at=FETCHED_AT, ok=True, final_url=URL, source="Yahoo奇摩新聞／股市",
        title="星河公司宣布新產品", published_at=PUBLISHED_AT, body=BODY,
        body_hash=content_hash(BODY), paragraph_count=6, warnings=["示範警告"],
    )
    monkeypatch.setattr(article_routes, "fetch_article", lambda url: fake)

    data = client.post("/articles/preview", json={"url": URL}).json()

    assert data["ok"] is True and data["failure_reason"] is None
    assert data["title"] == "星河公司宣布新產品"
    assert data["body"] == BODY
    assert data["paragraph_count"] == 6
    assert data["char_count"] == len(BODY.replace("\n", ""))
    assert data["warnings"] == ["示範警告"]


def test_preview_failure_is_a_normal_response_with_reason(client, monkeypatch):
    fake = FetchResult(
        url=URL, fetched_at=FETCHED_AT, failure_reason=FailureReason.HTTP_ERROR,
        message="網站回應錯誤（HTTP 403）。", title="仍可預填的標題",
    )
    monkeypatch.setattr(article_routes, "fetch_article", lambda url: fake)

    response = client.post("/articles/preview", json={"url": URL})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["failure_reason"] == "http_error"
    assert data["title"] == "仍可預填的標題"
    assert data["body"] == "" and data["body_hash"] is None


def test_preview_requires_a_url_field(client):
    assert client.post("/articles/preview", json={}).status_code == 422


# ------------------------------------------------------------- confirm


def test_confirm_unedited_fetch(client, engine):
    response = client.post("/articles", json=confirm_payload())

    assert response.status_code == 201
    data = response.json()
    assert data["created"] is True
    assert data["input_origin"] == "fetched"
    assert data["source"] == "Yahoo奇摩新聞／股市"  # derived on the server, not trusted from the client
    assert data["fetched_at"] and data["published_at"]
    assert data["content_hash"] == content_hash(BODY)
    assert data["paragraph_count"] == 6
    assert count_articles(engine) == 1


def test_confirming_identical_input_again_reuses_the_row(client, engine):
    first = client.post("/articles", json=confirm_payload()).json()
    second = client.post("/articles", json=confirm_payload())

    assert second.status_code == 200
    assert second.json()["id"] == first["id"] and second.json()["created"] is False
    assert count_articles(engine) == 1


def test_edited_body_is_marked_and_stored_as_a_new_version(client, engine):
    original = client.post("/articles", json=confirm_payload()).json()
    edited_body = BODY + "\n\n編輯者補上的一段文字。"

    response = client.post("/articles", json=confirm_payload(body=edited_body))

    data = response.json()
    assert response.status_code == 201
    assert data["input_origin"] == "fetched_edited"
    assert data["id"] != original["id"]
    assert data["content_hash"] == content_hash(edited_body)
    assert count_articles(engine) == 2
    # The original row is untouched, so earlier evidence still points at the right text.
    assert client.get(f"/articles/{original['id']}").json()["body"] == BODY


def test_windows_line_endings_do_not_count_as_an_edit(client):
    body = BODY.replace("\n", "\r\n")
    assert client.post("/articles", json=confirm_payload(body=body)).json()["input_origin"] == "fetched"


def test_pasted_text_has_no_fetch_metadata(client):
    payload = {
        "url": None, "title": "  星河公司   宣布新產品 ", "body": BODY,
        "fetched_body_hash": None, "published_at": PUBLISHED_AT.isoformat(),
        "fetched_at": FETCHED_AT.isoformat(), "fetch_failure_reason": "http_error",
    }
    data = client.post("/articles", json=payload).json()

    assert data["input_origin"] == "pasted"
    assert data["title"] == "星河公司 宣布新產品"
    assert data["url"] is None and data["source"] is None
    assert data["fetched_at"] is None and data["published_at"] is None
    assert data["fetch_failure_reason"] == "http_error"


def test_pasted_text_after_failed_fetch_keeps_the_url_as_provenance(client):
    payload = confirm_payload(fetched_body_hash=None, fetch_failure_reason="robots_disallowed")
    data = client.post("/articles", json=payload).json()
    assert data["input_origin"] == "pasted"
    assert data["url"] == URL
    assert data["source"] is None  # the URL is the user's claim, not verified by us
    assert data["fetch_failure_reason"] == "robots_disallowed"


def test_fetch_claim_without_url_is_treated_as_pasted(client):
    data = client.post("/articles", json=confirm_payload(url=None)).json()
    assert data["input_origin"] == "pasted"


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"title": ""}, "title_required"),
        ({"title": "   \n "}, "title_required"),
        ({"title": "長" * 501}, "title_too_long"),
        ({"body": "太短了。"}, "body_too_short"),
        ({"body": "   \n\n  "}, "body_too_short"),
        ({"body": "星" * 20_001}, "body_too_long"),
        ({"url": "javascript:alert(1)"}, "invalid_url"),
        ({"url": "ftp://tw.stock.yahoo.com/a"}, "invalid_url"),
    ],
)
def test_invalid_confirmations_are_rejected_with_a_readable_message(client, engine, overrides, code):
    response = client.post("/articles", json=confirm_payload(**overrides))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["message"]
    assert count_articles(engine) == 0


def test_body_is_never_silently_truncated(client):
    body = "星" * 19_999
    data = client.post("/articles", json=confirm_payload(body=body, fetched_body_hash=None)).json()
    assert data["body"] == body and data["char_count"] == 19_999


# ------------------------------------------------------------- read back


def test_get_article_and_404(client):
    created = client.post("/articles", json=confirm_payload()).json()
    fetched = client.get(f"/articles/{created['id']}")
    assert fetched.status_code == 200 and fetched.json()["body"] == BODY
    assert client.get("/articles/9999").status_code == 404
