"""Local prototype endpoint with persistent successful-result cache."""
import hashlib
import json
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

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
            return {**json.loads(cached.result_json), "cached": True}
        try:
            result = headline.analyze(article, key, model)
        except headline.AnalysisError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
        session.add(HeadlineAnalysis(cache_key=cache_key, document_id=article.document_id,
                                     result_json=json.dumps(result, ensure_ascii=False)))
        session.commit()
        return {**result, "cached": False}
    finally:
        _analysis_lock.release()
