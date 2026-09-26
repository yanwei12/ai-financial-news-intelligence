"""Paragraph preparation routes; independent of databases and AI models."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.ingestion.article_sources import SOURCES
from src.processing.cleaner import ParagraphMode, prepare_article

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class PrepareArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=200000)
    paragraph_mode: ParagraphMode = "blank_lines"


class ParagraphResponse(BaseModel):
    id: str
    text: str


class PreparedArticleResponse(BaseModel):
    title: str
    original_text: str
    document_id: str
    paragraph_mode: ParagraphMode
    preparation_version: str
    paragraphs: list[ParagraphResponse]


@router.post("/articles/prepare", response_model=PreparedArticleResponse)
def prepare(request: PrepareArticleRequest):
    try:
        return prepare_article(request.title, request.content, request.paragraph_mode).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/paragraphs", include_in_schema=False)
def paragraph_preview(request: Request):
    return templates.TemplateResponse(request=request, name="paragraphs.html", context={"sources": SOURCES})
