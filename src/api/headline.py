"""Local prototype endpoint with persistent successful-result cache."""
import hashlib
import json
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from pydantic import Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from src.api.external_verification import attach_verifications
from src.ai import headline
from src.api.article_routes import get_session
from src.api.paragraphs import PrepareArticleRequest
from src.database.models import HeadlineAnalysis
from src.processing.cleaner import prepare_article

router = APIRouter()
_analysis_lock = Lock()


class AnalyzeHeadlineRequest(PrepareArticleRequest):
    content: str = Field(min_length=1, max_length=20000)
    document_id: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/headline/analyze")
def analyze_headline(payload: AnalyzeHeadlineRequest, session: Session = Depends(get_session)):
    try:
        article = prepare_article(payload.title, payload.content, payload.paragraph_mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if article.document_id != payload.document_id:
        raise HTTPException(409, "文章已變更，請重新產生段落預覽。")
    if len(article.paragraphs) > 400:
        raise HTTPException(422, "第一版分析最多支援 400 段；不會截斷正文。")
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
            return {**attach_verifications(session, cache_key, json.loads(cached.result_json)), "cached": True}
        try:
            result = headline.analyze(article, key, model)
        except headline.AnalysisError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
        session.add(HeadlineAnalysis(cache_key=cache_key, document_id=article.document_id,
                                     result_json=json.dumps(result, ensure_ascii=False)))
        session.commit()
        return {**result, "cached": False, "analysis_id": cache_key}
    finally:
        _analysis_lock.release()


@router.get("/headline/history")
def analysis_history(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                     session: Session = Depends(get_session)):
    rows = session.scalars(select(HeadlineAnalysis).order_by(
        HeadlineAnalysis.created_at.desc(), HeadlineAnalysis.cache_key.desc()
    ).offset(offset).limit(limit + 1)).all()
    items = []
    for row in rows[:limit]:
        result = json.loads(row.result_json)
        items.append({"id": row.cache_key, "title": result.get("prepared", {}).get("title", "未命名新聞"),
                      "verdict": result.get("verdict"), "analyzed_at": result.get("analyzed_at"),
                      "model": result.get("model_version") or result.get("model")})
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}


@router.get("/headline/history/{analysis_id}")
def analysis_snapshot(analysis_id: str = Path(pattern=r"^[a-f0-9]{64}$"),
                      session: Session = Depends(get_session)):
    row = session.get(HeadlineAnalysis, analysis_id)
    if row is None:
        raise HTTPException(404, "找不到這筆分析紀錄。")
    return {**attach_verifications(session, row.cache_key, json.loads(row.result_json)), "cached": True}
