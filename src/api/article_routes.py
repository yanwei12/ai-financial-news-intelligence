"""
Routes for the article-input flow.

    GET  /check              page: paste URL → preview → confirm
    POST /articles/preview   fetch one article from a URL (nothing is saved)
    POST /articles           save the text the user confirmed
    GET  /articles/{id}      read a confirmed article back
"""

from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.api.article_schemas import (
    ArticleOut,
    ConfirmRequest,
    ConfirmResponse,
    PreviewRequest,
    PreviewResponse,
)
from src.database.article_repository import ArticleRepository
from src.database.database import engine
from src.database.models import Article
from src.ingestion.article_fetcher import MAX_BODY_CHARS, MIN_BODY_CHARS, fetch_article
from src.ingestion.article_sources import SOURCES, find_source
from src.processing.article_text import (
    collapse_inline_whitespace,
    content_hash,
    count_chars,
    count_paragraphs,
    normalize_body,
)

TITLE_MAX_CHARS = 500

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def _reject(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


@router.get("/check")
def check_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="check.html",
        context={
            "sources": SOURCES,
            "min_chars": MIN_BODY_CHARS,
            "max_chars": MAX_BODY_CHARS,
            "title_max": TITLE_MAX_CHARS,
        },
    )


@router.post("/articles/preview", response_model=PreviewResponse)
def preview_article(payload: PreviewRequest) -> PreviewResponse:
    result = fetch_article(payload.url)
    return PreviewResponse(
        ok=result.ok,
        url=result.url,
        final_url=result.final_url,
        source=result.source,
        title=result.title,
        published_at=result.published_at,
        fetched_at=result.fetched_at,
        body=result.body,
        body_hash=result.body_hash,
        paragraph_count=result.paragraph_count,
        char_count=count_chars(result.body),
        warnings=result.warnings,
        failure_reason=result.failure_reason.value if result.failure_reason else None,
        message=result.message,
    )


@router.post("/articles", response_model=ConfirmResponse)
def confirm_article(
    payload: ConfirmRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> ConfirmResponse:
    title = collapse_inline_whitespace(payload.title)
    if not title:
        raise _reject("title_required", "請填寫標題。")
    if len(title) > TITLE_MAX_CHARS:
        raise _reject("title_too_long", f"標題不可超過 {TITLE_MAX_CHARS} 字。")

    body = normalize_body(payload.body)
    chars = count_chars(body)
    if chars < MIN_BODY_CHARS:
        raise _reject(
            "body_too_short",
            f"內文只有 {chars} 字，少於 {MIN_BODY_CHARS} 字，無法判斷。請貼上完整正文。",
        )
    if chars > MAX_BODY_CHARS:
        raise _reject(
            "body_too_long",
            f"內文有 {chars} 字，超過 {MAX_BODY_CHARS} 字上限。第一版不會靜默截斷。",
        )

    url = (payload.url or "").strip() or None
    known_source = None
    if url is not None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise _reject("invalid_url", "來源網址格式不正確。")
        known_source = find_source(parts.hostname)

    digest = content_hash(body)
    fetched = payload.fetched_body_hash is not None and url is not None
    # A source name is only recorded for text we fetched ourselves. For pasted
    # text the URL is the user's claim about provenance, not something we checked.
    source = known_source.name if fetched and known_source else None
    if not fetched:
        origin = "pasted"
    elif digest == payload.fetched_body_hash:
        origin = "fetched"
    else:
        origin = "fetched_edited"

    article, created = ArticleRepository(session).save_confirmed(
        url=url,
        title=title,
        source=source,
        # Times only mean something when they came from a fetch.
        published_at=payload.published_at if fetched else None,
        fetched_at=payload.fetched_at if fetched else None,
        body=body,
        content_hash=digest,
        input_origin=origin,
        fetch_failure_reason=None if fetched else payload.fetch_failure_reason,
    )
    response.status_code = 201 if created else 200
    return ConfirmResponse(**_article_fields(article), created=created)


@router.get("/articles/{article_id}", response_model=ArticleOut)
def get_article(article_id: int, session: Session = Depends(get_session)) -> ArticleOut:
    article = ArticleRepository(session).get(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return ArticleOut(**_article_fields(article))


def _article_fields(article: Article) -> dict:
    return {
        "id": article.id,
        "url": article.url,
        "title": article.title,
        "source": article.source,
        "published_at": article.published_at,
        "fetched_at": article.fetched_at,
        "body": article.body,
        "char_count": count_chars(article.body),
        "paragraph_count": count_paragraphs(article.body),
        "content_hash": article.content_hash,
        "input_origin": article.input_origin,
        "fetch_failure_reason": article.fetch_failure_reason,
        "created_at": article.created_at,
    }
