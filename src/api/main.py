from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.api.schemas import NewsDetail, NewsListItem
from src.api.article_routes import router as article_router
from src.api.paragraphs import router as paragraph_router
from src.api.headline import router as headline_router
from src.database.database import create_tables, engine
from src.database.models import News


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Adds missing tables without altering or deleting existing ones.
    create_tables()
    yield


app = FastAPI(
    title="新聞照妖鏡",
    description="比對新聞標題與正文，提供判斷理由及原文證據。",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(article_router)
app.include_router(paragraph_router)
app.include_router(headline_router)

_API_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_API_DIR / "static")), name="static")


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency：每個 request 取得一個獨立的 Session，
    request 結束後自動關閉。這是 FastAPI + SQLAlchemy 的標準寫法。
    """
    with Session(engine) as session:
        yield session


@app.get("/news", response_model=list[NewsListItem])
def list_news(
    source: str | None = None,
    language: str | None = None,
    db: Session = Depends(get_db),
) -> list[News]:

    statement = select(News)

    if source:
        statement = statement.where(
            News.source == source
        )

    if language:
        statement = statement.where(
            News.language == language
        )

    statement = statement.order_by(
        News.id.desc()
    )

    return list(
        db.execute(statement).scalars().all()
    )


@app.get("/news/{news_id}", response_model=NewsDetail)
def get_news_detail(news_id: int, db: Session = Depends(get_db)) -> News:
    """取得單篇新聞完整資訊，找不到時回傳 404。"""
    news = db.get(News, news_id)
    if news is None:
        raise HTTPException(status_code=404, detail="News not found")
    return news


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def home() -> RedirectResponse:
    """Send the home page and old dashboard bookmarks to the headline checker."""
    return RedirectResponse(url="/check", status_code=307)
