"""Local prototype endpoint with persistent successful-result cache."""
import hashlib
import json
import os
from datetime import datetime, timezone
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.ai import headline
from src.api.article_routes import get_session
from src.api.paragraphs import PrepareArticleRequest
from src.api.ratelimit import limit_requests
from src.database.article_repository import ArticleRepository
from src.database.models import ArticleAnalysisLink, HeadlineAnalysis
from src.ingestion.article_fetcher import MAX_BODY_CHARS
from src.processing.article_text import count_chars
from src.processing.cleaner import PreparedArticle, prepare_article

router = APIRouter()
_analysis_lock = Lock()
analyze_limit = limit_requests("ANALYZE", 6)

# Same unit as /check: characters other than whitespace. Raw length is only capped
# generously, so blank lines between paragraphs never push a valid article over.
MAX_ANALYSIS_CHARS = MAX_BODY_CHARS
MAX_RAW_CHARS = 3 * MAX_ANALYSIS_CHARS
MAX_PARAGRAPHS = 400
DEFAULT_DAILY_LIMIT = 50


class AnalyzeHeadlineRequest(PrepareArticleRequest):
    content: str = Field(min_length=1, max_length=MAX_RAW_CHARS)
    document_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Set when the text came from a confirmed article, so the result can be found again.
    article_id: int | None = Field(default=None, ge=1)


def daily_limit() -> int:
    """New model calls allowed per UTC day (HEADLINE_DAILY_LIMIT); cached results are free."""
    raw = os.environ.get("HEADLINE_DAILY_LIMIT", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_DAILY_LIMIT


def analyses_today(session: Session) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return session.execute(
        select(func.count()).select_from(HeadlineAnalysis).where(HeadlineAnalysis.created_at >= start)
    ).scalar_one()


def _check_saved_article(session: Session, payload: AnalyzeHeadlineRequest, article: PreparedArticle) -> None:
    """The text being analysed must be exactly the saved article, or the link would be false."""
    if payload.article_id is None:
        return
    stored = ArticleRepository(session).get(payload.article_id)
    if stored is None:
        raise HTTPException(404, "找不到這篇已確認的文章。")
    same = prepare_article(stored.title, stored.body, payload.paragraph_mode).document_id == article.document_id
    if not same:
        raise HTTPException(409, "送出的內容與已確認的文章不同，無法把分析結果關聯到那篇文章。")


def _link(session: Session, article_id: int | None, cache_key: str) -> None:
    if article_id is not None and session.get(ArticleAnalysisLink, (article_id, cache_key)) is None:
        session.add(ArticleAnalysisLink(article_id=article_id, cache_key=cache_key))


@router.post("/headline/analyze", dependencies=[Depends(analyze_limit)])
def analyze_headline(payload: AnalyzeHeadlineRequest, session: Session = Depends(get_session)):
    try:
        article = prepare_article(payload.title, payload.content, payload.paragraph_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if count_chars(payload.content) > MAX_ANALYSIS_CHARS:
        raise HTTPException(422, f"第一版分析最多 {MAX_ANALYSIS_CHARS:,} 字（不含空白）；不會截斷正文。")
    if len(article.paragraphs) > MAX_PARAGRAPHS:
        raise HTTPException(422, f"第一版分析最多支援 {MAX_PARAGRAPHS} 段；不會截斷正文。")
    if article.document_id != payload.document_id:
        raise HTTPException(409, "文章已變更，請重新產生段落預覽。")
    _check_saved_article(session, payload, article)
    try:
        key, model = headline.settings()
    except headline.AnalysisError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
    cache_key = hashlib.sha256(f"{article.document_id}:{model}:{headline.PROMPT_VERSION}".encode()).hexdigest()
    if not _analysis_lock.acquire(blocking=False):
        raise HTTPException(429, "已有分析進行中，請稍候再試。")
    try:
        cached = session.get(HeadlineAnalysis, cache_key)
        if cached:
            _link(session, payload.article_id, cache_key)
            session.commit()
            return {**json.loads(cached.result_json), "cached": True, "article_id": payload.article_id}
        limit = daily_limit()
        if analyses_today(session) >= limit:
            raise HTTPException(429, {"code": "daily_limit",
                                      "message": f"今日分析次數已達上限（{limit} 次，以 UTC 日期計）。已分析過的文章仍可查看。"})
        try:
            result = headline.analyze(article, key, model)
        except headline.AnalysisError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
        session.add(HeadlineAnalysis(cache_key=cache_key, document_id=article.document_id,
                                     result_json=json.dumps(result, ensure_ascii=False)))
        session.flush()                      # the link points at this row
        _link(session, payload.article_id, cache_key)
        session.commit()
        return {**result, "cached": False, "article_id": payload.article_id}
    finally:
        _analysis_lock.release()


@router.get("/articles/{article_id}/analyses")
def list_article_analyses(article_id: int, session: Session = Depends(get_session)):
    """Every saved analysis of one confirmed article, newest first."""
    if ArticleRepository(session).get(article_id) is None:
        raise HTTPException(404, "Article not found")
    rows = session.execute(
        select(HeadlineAnalysis, ArticleAnalysisLink.created_at)
        .join(ArticleAnalysisLink, ArticleAnalysisLink.cache_key == HeadlineAnalysis.cache_key)
        .where(ArticleAnalysisLink.article_id == article_id)
        .order_by(ArticleAnalysisLink.created_at.desc())
    ).all()
    analyses = []
    for analysis, linked_at in rows:
        result = json.loads(analysis.result_json)
        analyses.append({
            "cache_key": analysis.cache_key, "document_id": analysis.document_id,
            "verdict": result["verdict"], "summary": result["summary"], "claims": len(result["claims"]),
            "model": result.get("model"), "prompt_version": result.get("prompt_version"),
            "analyzed_at": result.get("analyzed_at"), "latency_ms": result.get("latency_ms"),
            "linked_at": linked_at.isoformat(),
        })
    return {"article_id": article_id, "analyses": analyses}
