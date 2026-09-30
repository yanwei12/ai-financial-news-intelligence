"""
Single-article fetcher for the headline-consistency checker.

Given a news URL, it returns the title, publish time and body of that one
article, or a specific failure reason. It never guesses: if the body cannot be
found or looks incomplete, the result says so instead of passing partial text
off as a success.

Safety rules (only user-submitted single URLs are fetched, never crawled):
  * only hosts listed in article_sources.SOURCES,
  * http/https on the default ports, no credentials in the URL,
  * every host (including each redirect hop) must resolve to public addresses,
  * robots.txt is honoured for our user agent,
  * bounded redirects, download size and total time.

Known gap: the address check and the connection are two separate DNS lookups,
so a hostile DNS server could still race them. The host allowlist is the main
protection; pin the resolved IP if this ever serves untrusted traffic.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from src.ingestion.article_sources import SOURCES, SourceConfig, find_source
from src.processing.article_text import (
    collapse_inline_whitespace,
    content_hash,
    count_chars,
)

USER_AGENT = (
    "HeadlineMirrorBot/0.1 "
    "(research prototype; fetches one article when a user submits its URL)"
)
ROBOTS_TOKEN = "HeadlineMirrorBot"

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10
TOTAL_TIMEOUT = 20
MAX_REDIRECTS = 3
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_URL_LENGTH = 2048
ROBOTS_TTL_SECONDS = 3600

# Body length limits, counted in non-whitespace characters.
MIN_BODY_CHARS = 100
SHORT_BODY_CHARS = 300
MAX_BODY_CHARS = 20_000

TRUNCATION_MARKERS = (
    "繼續閱讀", "閱讀全文", "閱讀更多", "看更多", "點擊查看", "全文請見", "登入後", "訂閱後",
)

Resolver = Callable[[str, int], list]
RobotsCache = dict[str, tuple[float, RobotFileParser]]

_robots_cache: RobotsCache = {}


class FailureReason(str, Enum):
    INVALID_URL = "invalid_url"
    SOURCE_NOT_ALLOWED = "source_not_allowed"
    BLOCKED_ADDRESS = "blocked_address"
    ROBOTS_DISALLOWED = "robots_disallowed"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    NOT_HTML = "not_html"
    TOO_LARGE = "too_large"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    NO_BODY = "no_body"
    BODY_TOO_SHORT = "body_too_short"
    BODY_TOO_LONG = "body_too_long"


def failure_message(
    reason: FailureReason, *, status: int | None = None, chars: int | None = None
) -> str:
    """Traditional Chinese explanation shown to the user."""
    if reason is FailureReason.SOURCE_NOT_ALLOWED:
        names = "、".join(f"{s.name}（{'、'.join(s.domains)}）" for s in SOURCES)
        return f"目前只支援下列網站的文章網址：{names}。其他網站請改貼內文。"
    if reason is FailureReason.HTTP_ERROR:
        return (
            f"網站回應錯誤（HTTP {status}）。文章可能已下架、需要登入，"
            "或網站拒絕存取。"
        )
    if reason is FailureReason.BODY_TOO_SHORT:
        return (
            f"擷取到的正文只有 {chars} 字，可能不完整（例如付費牆或內容載入失敗）。"
            f"少於 {MIN_BODY_CHARS} 字不會當作可用正文。"
        )
    if reason is FailureReason.BODY_TOO_LONG:
        return (
            f"正文有 {chars} 字，超過 {MAX_BODY_CHARS} 字的上限。"
            "第一版不分析過長文章，也不會靜默截斷。"
        )
    return {
        FailureReason.INVALID_URL: "網址格式不正確，請貼上完整的 http(s) 新聞網址。",
        FailureReason.BLOCKED_ADDRESS: "這個網址指向不允許存取的位址，已停止擷取。",
        FailureReason.ROBOTS_DISALLOWED: "該網站的 robots.txt 不允許擷取這個頁面。",
        FailureReason.TIMEOUT: "連線逾時，網站沒有及時回應。",
        FailureReason.NETWORK_ERROR: "無法連線到網站，請稍後再試。",
        FailureReason.NOT_HTML: "網址指向的不是網頁內容。",
        FailureReason.TOO_LARGE: "網頁檔案過大，已停止下載。",
        FailureReason.TOO_MANY_REDIRECTS: "網址轉址次數過多，已停止擷取。",
        FailureReason.NO_BODY: "找不到文章正文。這可能不是單篇新聞頁，或網站版面已改版。",
    }[reason]


class FetchError(Exception):
    def __init__(self, reason: FailureReason, status: int | None = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.status = status


@dataclass
class FetchResult:
    url: str
    fetched_at: datetime
    ok: bool = False
    failure_reason: FailureReason | None = None
    message: str = ""
    final_url: str | None = None
    source: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    body: str = ""
    body_hash: str | None = None
    paragraph_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Page:
    final_url: str
    source: SourceConfig
    content: bytes


@dataclass
class _Extracted:
    title: str | None
    published_at: datetime | None
    paragraphs: list[str]


def _default_resolver(host: str, port: int) -> list:
    return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)


# ---------------------------------------------------------------- URL safety


def _check_url(url: str, resolver: Resolver) -> SourceConfig:
    """Validate one URL (the submitted one or a redirect target)."""
    if not url or len(url) > MAX_URL_LENGTH:
        raise FetchError(FailureReason.INVALID_URL)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError(FailureReason.INVALID_URL) from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(FailureReason.INVALID_URL)
    if parts.username or parts.password or port not in (None, 80, 443):
        raise FetchError(FailureReason.INVALID_URL)

    source = find_source(parts.hostname)
    if source is None:
        raise FetchError(FailureReason.SOURCE_NOT_ALLOWED)

    try:
        infos = resolver(parts.hostname, port or (443 if parts.scheme == "https" else 80))
    except OSError:
        raise FetchError(FailureReason.NETWORK_ERROR) from None
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not address.is_global:
            raise FetchError(FailureReason.BLOCKED_ADDRESS)
    return source


# ---------------------------------------------------------------- HTTP layer


def _read_limited(resp: Any, deadline: float) -> bytes:
    length = resp.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > MAX_HTML_BYTES:
        raise FetchError(FailureReason.TOO_LARGE)
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > MAX_HTML_BYTES:
            raise FetchError(FailureReason.TOO_LARGE)
        if time.monotonic() > deadline:
            raise FetchError(FailureReason.TIMEOUT)
        chunks.append(chunk)
    return b"".join(chunks)


def _request(
    url: str,
    *,
    session: Any,
    resolver: Resolver,
    robots_cache: RobotsCache,
    deadline: float,
    check_robots: bool,
    html_only: bool,
) -> _Page:
    """GET a URL, validating and re-validating every redirect hop by hand."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        source = _check_url(current, resolver)
        if check_robots and not _robots_allow(
            current, session=session, resolver=resolver,
            robots_cache=robots_cache, deadline=deadline,
        ):
            raise FetchError(FailureReason.ROBOTS_DISALLOWED)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError(FailureReason.TIMEOUT)
        try:
            resp = session.get(
                current,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"},
                timeout=(CONNECT_TIMEOUT, min(READ_TIMEOUT, remaining)),
                stream=True,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise FetchError(FailureReason.TIMEOUT) from None
        except requests.RequestException:
            raise FetchError(FailureReason.NETWORK_ERROR) from None

        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise FetchError(FailureReason.HTTP_ERROR, resp.status_code)
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise FetchError(FailureReason.HTTP_ERROR, resp.status_code)
            content_type = resp.headers.get("Content-Type", "").lower()
            if html_only and "html" not in content_type:
                raise FetchError(FailureReason.NOT_HTML)
            return _Page(current, source, _read_limited(resp, deadline))
        finally:
            resp.close()
    raise FetchError(FailureReason.TOO_MANY_REDIRECTS)


def _robots_allow(
    url: str,
    *,
    session: Any,
    resolver: Resolver,
    robots_cache: RobotsCache,
    deadline: float,
) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = robots_cache.get(origin)
    if cached and cached[0] > time.monotonic():
        return cached[1].can_fetch(ROBOTS_TOKEN, url)

    parser = RobotFileParser()
    try:
        page = _request(
            f"{origin}/robots.txt", session=session, resolver=resolver,
            robots_cache=robots_cache, deadline=deadline,
            check_robots=False, html_only=False,
        )
    except FetchError as error:
        # RFC 9309: a missing robots.txt (4xx) means "no restrictions".
        # Any other failure means we cannot confirm permission, so stop.
        if error.reason is FailureReason.HTTP_ERROR and 400 <= (error.status or 0) < 500:
            parser.parse([])
        else:
            raise
    else:
        parser.parse(page.content.decode("utf-8", errors="replace").splitlines())
    robots_cache[origin] = (time.monotonic() + ROBOTS_TTL_SECONDS, parser)
    return parser.can_fetch(ROBOTS_TOKEN, url)


# ---------------------------------------------------------------- Extraction


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp to UTC. Timestamps without a zone are unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _json_ld_article(soup: BeautifulSoup) -> dict:
    """Return the first schema.org NewsArticle/Article object on the page."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            stack.extend(item.get("@graph", []))
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(k in ("NewsArticle", "Article", "ReportageNewsArticle") for k in kinds):
                return item
    return {}


def _extract_paragraphs(article: Any, source: SourceConfig) -> list[str]:
    paragraphs: list[str] = []
    for container in article.select(", ".join(source.body_selectors)):
        for element in container.find_all(source.block_tags, recursive=False):
            for line_break in element.find_all("br"):
                line_break.replace_with(" ")
            text = collapse_inline_whitespace(element.get_text())
            if not text:
                continue
            if element.name != "p" and text.rstrip(":：") in source.stop_headings:
                return paragraphs
            if source.skip_classes & set(element.get("class") or []):
                continue
            paragraphs.append(text)
    return paragraphs


def _extract(content: bytes, source: SourceConfig) -> _Extracted:
    soup = BeautifulSoup(content, "html.parser")
    ld = _json_ld_article(soup)
    article = soup.select_one(source.article_selector)

    title = None
    if article is not None:
        heading = article.select_one(source.title_selector)
        if heading is not None:
            title = collapse_inline_whitespace(heading.get_text()) or None
    if not title and isinstance(ld.get("headline"), str):
        title = collapse_inline_whitespace(ld["headline"]) or None
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = collapse_inline_whitespace(og_title["content"]) or None

    published_at = _parse_datetime(ld.get("datePublished"))
    if published_at is None:
        meta_time = soup.find("meta", property="article:published_time")
        published_at = _parse_datetime(meta_time.get("content") if meta_time else None)
    if published_at is None and article is not None:
        time_tag = article.find("time", attrs={"datetime": True})
        published_at = _parse_datetime(time_tag.get("datetime") if time_tag else None)

    paragraphs = _extract_paragraphs(article, source) if article is not None else []
    return _Extracted(title, published_at, paragraphs)


# ---------------------------------------------------------------- Public API


def fetch_article(
    url: str,
    *,
    session: Any | None = None,
    resolver: Resolver | None = None,
    robots_cache: RobotsCache | None = None,
) -> FetchResult:
    """
    Fetch and extract one article. Expected problems (blocked site, timeout,
    unusable body) come back as ok=False with a failure_reason; they are not
    raised.
    """
    url = (url or "").strip()
    result = FetchResult(url=url, fetched_at=datetime.now(timezone.utc))
    own_session = session is None
    session = session or requests.Session()
    try:
        page = _request(
            url,
            session=session,
            resolver=resolver or _default_resolver,
            robots_cache=_robots_cache if robots_cache is None else robots_cache,
            deadline=time.monotonic() + TOTAL_TIMEOUT,
            check_robots=True,
            html_only=True,
        )
    except FetchError as error:
        return _fail(result, error.reason, status=error.status)
    finally:
        if own_session:
            session.close()

    result.final_url = page.final_url
    result.source = page.source.name

    extracted = _extract(page.content, page.source)
    result.title = extracted.title
    result.published_at = extracted.published_at

    if not extracted.paragraphs:
        # Not an article page (home page, section list): its title is site
        # noise, so do not offer it to prefill the paste form.
        result.title = None
        return _fail(result, FailureReason.NO_BODY)
    body = "\n\n".join(extracted.paragraphs)
    chars = count_chars(body)
    if chars < MIN_BODY_CHARS:
        return _fail(result, FailureReason.BODY_TOO_SHORT, chars=chars)
    if chars > MAX_BODY_CHARS:
        return _fail(result, FailureReason.BODY_TOO_LONG, chars=chars)

    if chars < SHORT_BODY_CHARS:
        result.warnings.append(f"正文很短（{chars} 字），請對照原網頁確認是否完整。")
    last = extracted.paragraphs[-1]
    for marker in TRUNCATION_MARKERS:
        if marker in last:
            result.warnings.append(f"文末出現「{marker}」，正文可能被截斷，請對照原網頁確認。")
            break
    if not extracted.title:
        result.warnings.append("找不到標題，請在下一步手動補上。")

    result.ok = True
    result.body = body
    result.body_hash = content_hash(body)
    result.paragraph_count = len(extracted.paragraphs)
    return result


def _fail(
    result: FetchResult,
    reason: FailureReason,
    *,
    status: int | None = None,
    chars: int | None = None,
) -> FetchResult:
    result.ok = False
    result.failure_reason = reason
    result.message = failure_message(reason, status=status, chars=chars)
    return result
