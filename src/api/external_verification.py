"""Explicit per-claim verification, persisted independently of headline judgments."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ai import external_verification as provider
from src.ai.headline import AnalysisError
from src.api.article_routes import get_session
from src.database.models import ExternalVerification, HeadlineAnalysis

router = APIRouter()
_search_lock = Lock()
CACHE_HOURS = 24


def attach_verifications(session, analysis_id, result):
    rows = session.scalars(select(ExternalVerification).where(
        ExternalVerification.analysis_id == analysis_id
    ).order_by(ExternalVerification.created_at)).all()
    for row in rows:
        if 0 <= row.claim_index < len(result.get("claims", [])):
            result["claims"][row.claim_index]["external_verification"] = {
                **json.loads(row.result_json), "cached": True,
            }
    return {**result, "analysis_id": analysis_id}


@router.post("/headline/history/{analysis_id}/claims/{claim_index}/verify")
def verify_claim(analysis_id: str = Path(pattern=r"^[a-f0-9]{64}$"),
                 claim_index: int = Path(ge=0, le=11), session: Session = Depends(get_session)):
    row = session.get(HeadlineAnalysis, analysis_id)
    if row is None:
        raise HTTPException(404, "找不到這筆分析紀錄。")
    snapshot = json.loads(row.result_json)
    if claim_index >= len(snapshot.get("claims", [])):
        raise HTTPException(404, "找不到這項主張。")
    if not snapshot.get("prepared", {}).get("original_text"):
        raise HTTPException(409, "舊紀錄缺少正文，請重新分析後取證。")
    try:
        key, model = provider.settings()
        cache_key = hashlib.sha256(f"{analysis_id}:{claim_index}:{model}:{provider.PROMPT_VERSION}".encode()).hexdigest()
        if not _search_lock.acquire(blocking=False):
            raise HTTPException(429, "已有外部取證進行中，請稍後再試。", headers={"Retry-After": "10"})
        try:
            cached = session.get(ExternalVerification, cache_key)
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=CACHE_HOURS)
            if cached and cached.created_at.replace(tzinfo=None) > cutoff:
                return {**json.loads(cached.result_json), "cached": True}
            result = provider.verify(snapshot, claim_index, key, model)
            # Failure responses raise; no_evidence is a valid, explicit search outcome.
            if cached is None:
                cached = ExternalVerification(cache_key=cache_key, analysis_id=analysis_id, claim_index=claim_index)
                session.add(cached)
            cached.result_json = json.dumps(result, ensure_ascii=False)
            cached.created_at = datetime.now(timezone.utc)
            session.commit()
            return {**result, "cached": False}
        finally:
            _search_lock.release()
    except AnalysisError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
