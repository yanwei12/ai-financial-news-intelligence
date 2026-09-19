"""Article collection for annotation. Offline: feeds and fetches are faked."""

import json
from datetime import datetime, timezone

import pytest

from evaluation import articles
from evaluation.validate_annotations import main as validate_main
from src.ingestion.article_fetcher import FailureReason, FetchError, FetchResult, fetch_document
from tests.test_article_fetcher import FakeResponse, FakeSession, public_resolver

BODY_HEDGED = "\n\n".join([
    "星河公司今天表示，正在評估調整部分產品的售價，以因應原料成本上升。",
    "公司發言人指出，目前仍在蒐集意見，尚未作出最終決定，調整只適用北美地區的特定產品。",
    "分析師認為若最終調漲，幅度可能落在一成以內，仍須視下季成本而定，市場持續觀察。",
])
BODY_PLAIN = "\n\n".join(["星河公司今天宣布推出新一代伺服器產品，預計下季開始供貨。"] * 4)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>feed</title>
<item><title>a</title><link>https://tw.stock.yahoo.com/news/a.html</link></item>
<item><title>b</title><link>https://tw.news.yahoo.com/b.html</link></item>
<item><title>home</title><link>https://tw.stock.yahoo.com/</link></item>
<item><title>elsewhere</title><link>https://example.com/news/c.html</link></item>
<item><title>dup</title><link>https://tw.stock.yahoo.com/news/a.html</link></item>
</channel></rss>""".encode()


def result(url, title, body, ok=True):
    return FetchResult(url=url, fetched_at=datetime(2026, 9, 19, 6, 0, tzinfo=timezone.utc), ok=ok,
                       final_url=url, source="Yahoo奇摩新聞／股市", title=title, body=body,
                       failure_reason=None if ok else FailureReason.NO_BODY)


# ------------------------------------------------------------- cues


def test_certainty_word_needs_hedging_in_the_body():
    cues = articles.find_cues("星河公司確定調漲價格", BODY_HEDGED)
    assert cues.headline_certainty == ("確定",) and "評估" in cues.body_hedges and "尚未" in cues.body_hedges
    assert cues.score >= 4
    assert articles.find_cues("星河公司確定調漲價格", BODY_PLAIN).score == 0        # no hedging: not a candidate


def test_scope_word_needs_limits_in_the_body():
    cues = articles.find_cues("星河公司全面漲價", BODY_HEDGED)
    assert cues.headline_scope == ("全面",) and "特定" in cues.body_limits and "部分" in cues.body_limits
    assert cues.score > 0
    assert articles.find_cues("星河公司全面漲價", BODY_PLAIN).score == 0


def test_no_headline_cue_means_no_candidate_even_with_hedged_body():
    assert articles.find_cues("星河公司調整售價", BODY_HEDGED).score == 0


# ------------------------------------------------------------- feeds


def test_feed_links_keep_only_supported_article_pages_and_interleave_dedupes():
    links = articles.feed_links(RSS)
    assert links == ["https://tw.stock.yahoo.com/news/a.html", "https://tw.news.yahoo.com/b.html",
                     "https://tw.stock.yahoo.com/news/a.html"]
    merged = articles.interleave([["1", "2", "3"], ["a", "2"], []])
    assert merged == ["1", "a", "2", "3"]


# ------------------------------------------------------------- discover


def run_discover(tmp_path, fetches, **kwargs):
    lines = []
    rows = articles.discover(
        ("feed-1",), limit=10, delay=0, cache_dir=tmp_path / "cache",
        fetch_feed=lambda url: (url, RSS),
        fetch_one=lambda url: fetches[url],
        sleep=lambda s: None, out=lines.append, **kwargs)
    return rows, "\n".join(lines)


def test_discover_ranks_candidates_caches_everything_and_survives_failures(tmp_path):
    fetches = {
        "https://tw.stock.yahoo.com/news/a.html": result("https://tw.stock.yahoo.com/news/a.html", "星河公司確定調漲價格", BODY_HEDGED),
        "https://tw.news.yahoo.com/b.html": result("https://tw.news.yahoo.com/b.html", "星河公司宣布新產品", BODY_PLAIN),
    }
    rows, output = run_discover(tmp_path, fetches)

    assert [r["title"] for r in rows] == ["星河公司確定調漲價格", "星河公司宣布新產品"]   # sorted by score
    assert "1 flagged" in output or "1 flagged as candidates" in output
    assert "星河公司確定調漲價格" in output and "星河公司宣布新產品" not in output   # only flagged ones listed
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2                        # but both are cached


def test_discover_counts_failed_fetches_and_can_show_everything(tmp_path):
    fetches = {
        "https://tw.stock.yahoo.com/news/a.html": result("https://tw.stock.yahoo.com/news/a.html", "x", "", ok=False),
        "https://tw.news.yahoo.com/b.html": result("https://tw.news.yahoo.com/b.html", "星河公司宣布新產品", BODY_PLAIN),
    }
    rows, output = run_discover(tmp_path, fetches, show_all=True)
    assert len(rows) == 1 and "1 failed" in output and "星河公司宣布新產品" in output


def test_a_broken_feed_is_reported_not_fatal(tmp_path):
    def broken(url):
        raise FetchError(FailureReason.ROBOTS_DISALLOWED)

    lines = []
    rows = articles.discover(("feed-1",), limit=5, delay=0, cache_dir=tmp_path, fetch_feed=broken,
                             fetch_one=lambda u: None, sleep=lambda s: None, out=lines.append)
    assert rows == [] and any("robots_disallowed" in line for line in lines)


# ------------------------------------------------------------- show + template -> validate


def test_show_prints_paragraph_ids_and_template_fails_until_filled_then_validates(tmp_path, capsys):
    cache = tmp_path / "cache"
    digest = articles.cache_article(
        result("https://tw.stock.yahoo.com/news/a.html", "星河公司確定調漲價格", BODY_HEDGED), cache)

    out_dir = tmp_path / "real"
    articles.show(digest[:8], cache, out_dir, "max", None, None)
    shown = capsys.readouterr().out
    assert "P001" in shown and "P003" in shown and "document_id" in shown

    target = out_dir / f"r-{digest[:8]}.max.json"
    assert target.exists()
    # Empty on purpose: the validator must refuse it until a claim is added.
    assert validate_main([str(target), "--cache-dir", str(cache)]) == 1
    assert "claims" in capsys.readouterr().out

    record = json.loads(target.read_text(encoding="utf-8"))
    record["claims"] = [{"claim_id": "C1", "text": "星河公司已確定調漲價格", "relation": "supports",
                         "gap_type": "certainty", "evidence_ids": ["P001", "P002"]}]
    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert validate_main([str(target), "--cache-dir", str(cache)]) == 0
    assert "real: missing_conditions=1" in capsys.readouterr().out


def test_template_never_overwrites_an_existing_annotation(tmp_path):
    cache = tmp_path / "cache"
    digest = articles.cache_article(result("https://tw.stock.yahoo.com/news/a.html", "標題", BODY_HEDGED), cache)
    articles.show(digest[:8], cache, tmp_path / "real", "max", None, None, out=lambda s: None)
    with pytest.raises(SystemExit, match="not overwriting"):
        articles.show(digest[:8], cache, tmp_path / "real", "max", None, None, out=lambda s: None)


def test_template_needs_an_annotator_and_prefix_must_match_one_article(tmp_path):
    cache = tmp_path / "cache"
    digest = articles.cache_article(result("https://tw.stock.yahoo.com/news/a.html", "標題", BODY_HEDGED), cache)
    with pytest.raises(SystemExit, match="--annotator"):
        articles.show(digest[:8], cache, tmp_path / "real", None, None, None, out=lambda s: None)
    with pytest.raises(SystemExit, match="no cached article"):
        articles.show("zzzz", cache, None, None, None, None, out=lambda s: None)


# ------------------------------------------------------------- fetch_document


def test_fetch_document_uses_the_same_safety_rules():
    feed_url = "https://tw.stock.yahoo.com/rss?category=tw-market"
    session = FakeSession({feed_url: FakeResponse(200, RSS, "text/xml; charset=utf-8")})
    final_url, content = fetch_document(feed_url, session=session, resolver=public_resolver, robots_cache={})
    assert final_url == feed_url and content == RSS

    with pytest.raises(FetchError) as error:
        fetch_document("https://example.com/rss", session=FakeSession({}), resolver=public_resolver, robots_cache={})
    assert error.value.reason is FailureReason.SOURCE_NOT_ALLOWED

    with pytest.raises(FetchError) as error:      # a feed is not HTML, so html_only=True must refuse it
        fetch_document(feed_url, html_only=True, session=session, resolver=public_resolver, robots_cache={})
    assert error.value.reason is FailureReason.NOT_HTML
