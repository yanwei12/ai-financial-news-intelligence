from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass



def _utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


class News(Base):
    """Represents a single news article ingested from an RSS feed."""
    

    __tablename__ = "news"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        unique=True,
    )

    source: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    language: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )
    
    author: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
    )

    sentiment: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    sentiment_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    ai_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    
    def __repr__(self) -> str:
        return (
            f"<News id={self.id} "
            f"title={self.title!r} "
            f"source={self.source!r}>"
        )


class HeadlineAnalysis(Base):
    """Successful analysis snapshots, keyed by input, model and prompt version."""
    __tablename__ = "headline_analyses"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class Article(Base):
    """
    One confirmed input for the headline-consistency checker.

    Separate from `News` (RSS items): this holds the full body the user
    confirmed, either fetched from a URL or pasted. A row is never updated
    after it is created, so later evidence can always point back to the exact
    text that was analysed. An edited body is a new row.
    """

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # NULL when the text was pasted without a source URL.
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # fetched | fetched_edited | pasted
    input_origin: Mapped[str] = mapped_column(String(20), nullable=False)
    # Why the automatic fetch failed, when the user pasted text instead.
    fetch_failure_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Article id={self.id} title={self.title!r} origin={self.input_origin}>"