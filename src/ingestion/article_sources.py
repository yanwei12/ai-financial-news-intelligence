"""Site-specific extraction rules; other public article URLs use generic extraction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceConfig:
    name: str
    domains: tuple[str, ...]
    generic: bool = False
    # The element that wraps one article page.
    article_selector: str = "article"
    title_selector: str = "h1"
    # Containers inside the article that hold body text.
    body_selectors: tuple[str, ...] = ("div.atoms",)
    # Tags inside a container that count as body paragraphs.
    block_tags: tuple[str, ...] = ("p", "h2", "h3", "h4", "h5", "h6")
    # Optional direct-child selector for layouts with a non-paragraph lead.
    block_selector: str | None = None
    # Paragraphs carrying one of these CSS classes are page furniture.
    skip_classes: frozenset[str] = frozenset()
    # A heading with this exact text ends the body (related-article lists).
    stop_headings: tuple[str, ...] = ()


SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        name="Yahoo奇摩新聞／股市",
        domains=("tw.stock.yahoo.com", "tw.news.yahoo.com"),
        skip_classes=frozenset({"read-more-vendor"}),
        stop_headings=("延伸閱讀", "相關新聞", "相關報導", "更多報導", "推薦閱讀"),
    ),
    SourceConfig(
        name="公視新聞",
        domains=("news.pts.org.tw",),
        article_selector="article:has(.post-article)",
        body_selectors=(".post-article",),
        block_selector=":scope > div.articleimg, :scope > p, :scope > h2, :scope > h3",
        stop_headings=("延伸閱讀", "相關新聞", "相關報導"),
    ),
    SourceConfig(
        name="自由時報",
        domains=("news.ltn.com.tw",),
        article_selector="div.article",
        body_selectors=(".article_wrap .text",),
        skip_classes=frozenset({"before_ir", "after_ir", "appE1121"}),
        stop_headings=("延伸閱讀", "相關新聞", "相關報導"),
    ),
)


def find_source(host: str) -> SourceConfig | None:
    """Return the source whose domain list contains this exact host."""
    host = host.lower().rstrip(".")
    for source in SOURCES:
        if host in source.domains:
            return source
    return None
