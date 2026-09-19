"""
Collect real articles for annotation.

    python -m evaluation.articles discover            # list candidates from Yahoo Taiwan feeds
    python -m evaluation.articles fetch URL           # fetch one article and show its paragraphs
    python -m evaluation.articles show 3cd732d6       # show a cached article (hash prefix)
    python -m evaluation.articles show 3cd732d6 --template evaluation/annotations/real --annotator max

`discover` is only a pre-filter for a human. It flags headlines that use a
certainty word (確定, 正式…) or a scope word (全面, 所有…) when the body also
contains hedging or limiting words. It does not decide any label: a flagged
article may have no real gap, and an unflagged one may have one. Plan §9: pick
articles by hand, because 100 fetched articles are not 100 useful samples.

Fetched text goes to a local, git-ignored cache (evaluation/cache/<hash>.json).
Fetching follows the same safety rules as the web app (allowlist, robots.txt).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import feedparser

from evaluation.validate_annotations import DEFAULT_CACHE_DIR
from src.ingestion.article_fetcher import FetchError, FetchResult, fetch_article, fetch_document
from src.ingestion.article_sources import find_source
from src.processing.article_text import content_hash
from src.processing.cleaner import PREPARATION_VERSION, prepare_article

DEFAULT_FEEDS = (
    "https://tw.stock.yahoo.com/rss?category=tw-market",
    "https://tw.stock.yahoo.com/rss?category=intl-markets",
    "https://tw.stock.yahoo.com/rss?category=research",
)

HEADLINE_CERTAINTY = ("確定", "正式", "拍板", "敲定", "證實", "定案")
HEADLINE_SCOPE = ("全面", "所有", "全球", "全數", "全部", "一律", "通通", "全線", "各國")
# "預計" is left out on purpose: it is common in fully confirmed news ("預計下季供貨").
BODY_HEDGES = ("評估", "考慮", "研議", "傳出", "可能", "規劃", "尚未", "未定", "據傳", "尚無", "仍在")
BODY_LIMITS = ("部分", "僅限", "僅適用", "限於", "特定", "除外", "不包括", "不含")


@dataclass(frozen=True)
class Cues:
    headline_certainty: tuple[str, ...]
    headline_scope: tuple[str, ...]
    body_hedges: tuple[str, ...]
    body_limits: tuple[str, ...]

    @property
    def score(self) -> int:
        """0 = not a candidate. A certainty word needs hedging in the body; a scope word needs limits."""
        score = 0
        if self.headline_certainty and self.body_hedges:
            score += 2 + min(len(self.body_hedges), 3)
        if self.headline_scope and self.body_limits:
            score += 2 + min(len(self.body_limits), 3)
        return score


def find_cues(title: str, body: str) -> Cues:
    def present(words: tuple[str, ...], text: str) -> tuple[str, ...]:
        return tuple(w for w in words if w in text)

    return Cues(
        present(HEADLINE_CERTAINTY, title),
        present(HEADLINE_SCOPE, title),
        present(BODY_HEDGES, body),
        present(BODY_LIMITS, body),
    )


# ---------------------------------------------------------------- cache


def cache_article(result: FetchResult, cache_dir: Path) -> str:
    """Store a fetched article under its body hash and return the hash."""
    digest = content_hash(result.body)
    prepared = prepare_article(result.title or "", result.body, "blank_lines")
    cache_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "url": result.url,
        "final_url": result.final_url,
        "source": result.source,
        "title": result.title,
        "published_at": result.published_at.isoformat() if result.published_at else None,
        "fetched_at": result.fetched_at.isoformat(),
        "content_hash": digest,
        "document_id": prepared.document_id,
        "preparation_version": PREPARATION_VERSION,
        "body": result.body,
    }
    (cache_dir / f"{digest}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return digest


def load_cached(prefix: str, cache_dir: Path) -> dict:
    matches = sorted(cache_dir.glob(f"{prefix}*.json")) if prefix else []
    if not matches:
        raise SystemExit(f"no cached article starts with {prefix!r} in {cache_dir}")
    if len(matches) > 1:
        raise SystemExit(f"{prefix!r} matches {len(matches)} cached articles; use a longer prefix")
    return json.loads(matches[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------- discover


def feed_links(feed_bytes: bytes) -> list[str]:
    """Article links on supported hosts. Section and home pages have no /news/ path or .html suffix."""
    links = []
    for entry in feedparser.parse(feed_bytes).entries:
        link = (entry.get("link") or "").strip()
        host = urlsplit(link).hostname
        if host and find_source(host) and ("/news/" in link or link.endswith(".html")):
            links.append(link)
    return links


def interleave(lists: list[list[str]]) -> list[str]:
    """Round-robin across feeds, without repeats, so a limit is spread over all of them."""
    seen: set[str] = set()
    merged: list[str] = []
    for index in range(max((len(group) for group in lists), default=0)):
        for group in lists:
            if index < len(group) and group[index] not in seen:
                seen.add(group[index])
                merged.append(group[index])
    return merged


def discover(
    feeds: tuple[str, ...],
    *,
    limit: int,
    delay: float,
    cache_dir: Path,
    show_all: bool = False,
    fetch_feed: Callable = fetch_document,
    fetch_one: Callable[[str], FetchResult] = fetch_article,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> list[dict]:
    per_feed: list[list[str]] = []
    for feed in feeds:
        try:
            _, content = fetch_feed(feed)
        except FetchError as error:
            out(f"feed skipped ({error.reason.value}): {feed}")
            continue
        per_feed.append(feed_links(content))
    links = interleave(per_feed)[:limit]
    out(f"{len(links)} article links from {len(per_feed)} feed(s); fetching one by one...")

    rows: list[dict] = []
    failed = 0
    for index, link in enumerate(links):
        if index:
            sleep(delay)
        result = fetch_one(link)
        if not result.ok or not result.title:
            failed += 1
            continue
        digest = cache_article(result, cache_dir)
        cues = find_cues(result.title, result.body)
        rows.append({"hash": digest, "title": result.title, "url": result.final_url or result.url,
                     "chars": len(result.body.replace("\n", "")), "cues": cues, "score": cues.score})

    rows.sort(key=lambda row: -row["score"])
    shown = rows if show_all else [row for row in rows if row["score"] > 0]
    out(f"\ncached {len(rows)} article(s), {failed} failed; {len(shown)} flagged as candidates\n")
    for rank, row in enumerate(shown, 1):
        cues: Cues = row["cues"]
        out(f"{rank:2d}. [{row['score']}] {row['hash'][:8]}  {row['title']}")
        out(f"      標題線索: {'、'.join(cues.headline_certainty + cues.headline_scope) or '-'}"
            f"   內文線索: {'、'.join(cues.body_hedges + cues.body_limits) or '-'}   ({row['chars']} 字)")
        out(f"      {row['url']}")
    out("\n下一步: python -m evaluation.articles show <hash 前 8 碼>  讀完整段落，自己決定值不值得標。")
    return rows


# ---------------------------------------------------------------- show / template


def show(prefix: str, cache_dir: Path, template_dir: Path | None, annotator: str | None,
         sample_id: str | None, group_id: str | None, out: Callable[[str], None] = print) -> None:
    record = load_cached(prefix, cache_dir)
    prepared = prepare_article(record["title"], record["body"], "blank_lines")
    out(f"標題: {record['title']}")
    out(f"網址: {record['url']}")
    out(f"發布: {record['published_at'] or '未知'}   來源: {record['source']}")
    out(f"content_hash: {record['content_hash']}")
    out(f"document_id : {prepared.document_id}\n")
    for paragraph in prepared.paragraphs:
        out(f"{paragraph.id}  {paragraph.text}\n")

    if template_dir is None:
        return
    if not annotator:
        raise SystemExit("--template needs --annotator")
    sample = sample_id or f"r-{record['content_hash'][:8]}"
    target = template_dir / f"{sample}.{annotator}.json"
    if target.exists():
        raise SystemExit(f"{target} already exists; not overwriting your annotation")
    skeleton = {
        "sample_id": sample,
        "group_id": group_id or sample,
        "annotator": annotator,
        "is_synthetic": False,
        "article": {
            "title": record["title"],
            "document_id": prepared.document_id,
            "paragraph_mode": "blank_lines",
            "preparation_version": prepared.preparation_version,
            "url": record["final_url"] or record["url"],
            "content_hash": record["content_hash"],
        },
        # Deliberately empty: validation fails until you add at least one claim.
        "claims": [],
    }
    template_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    out(f"已建立標註空白檔: {target}\n填好 claims 後執行: python -m evaluation.validate_annotations {template_dir}")


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    commands = parser.add_subparsers(dest="command", required=True)

    find = commands.add_parser("discover", help="list candidate articles from Yahoo Taiwan feeds")
    find.add_argument("--feed", action="append", help="RSS URL (repeatable); default: three Yahoo feeds")
    find.add_argument("--limit", type=int, default=30, help="how many articles to fetch (default 30)")
    find.add_argument("--delay", type=float, default=1.0, help="seconds between fetches (default 1.0)")
    find.add_argument("--all", action="store_true", help="also list articles with no cues")

    one = commands.add_parser("fetch", help="fetch one article, cache it and show its paragraphs")
    one.add_argument("url")

    view = commands.add_parser("show", help="show a cached article with paragraph ids")
    view.add_argument("hash_prefix")
    for command in (one, view):
        command.add_argument("--template", type=Path, help="write an empty annotation file into this folder")
        command.add_argument("--annotator")
        command.add_argument("--sample-id")
        command.add_argument("--group-id")

    args = parser.parse_args(argv)
    if args.command == "discover":
        discover(tuple(args.feed or DEFAULT_FEEDS), limit=args.limit, delay=args.delay,
                 cache_dir=args.cache_dir, show_all=args.all)
        return 0
    if args.command == "fetch":
        result = fetch_article(args.url)
        if not result.ok or not result.title:
            print(f"fetch failed: {result.failure_reason.value if result.failure_reason else 'no title'}"
                  f" - {result.message}", file=sys.stderr)
            return 1
        prefix = cache_article(result, args.cache_dir)
    else:
        prefix = args.hash_prefix
    show(prefix, args.cache_dir, args.template, args.annotator, args.sample_id, args.group_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
