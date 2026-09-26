"""Read-only schemas for the existing RSS news archive."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict

class NewsListItem(BaseModel):
    """回傳給 GET /news 的單筆新聞摘要格式。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str
    source: str
    language: str | None
    published_at: datetime | None
    


class NewsDetail(BaseModel):
    """回傳給 GET /news/{news_id} 的完整新聞格式。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str
    source: str
    author: str | None
    published_at: datetime | None
    description: str | None
    content: str | None
    category: str | None
    created_at: datetime
    language: str | None
