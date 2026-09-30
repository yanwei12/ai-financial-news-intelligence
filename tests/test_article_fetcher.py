"""
Article fetcher tests. No network: a fake session and resolver stand in for
`requests` and DNS, and the HTML fixture is a synthetic article.
"""

from pathlib import Path

import pytest
import requests

from src.ingestion import article_fetcher as af
from src.ingestion.article_fetcher import FailureReason, fetch_article

FIXTURE = (Path(__file__).parent / "fixtures" / "article_sample.html").read_bytes()
ARTICLE_URL = "https://tw.stock.yahoo.com/news/star-river-123.html"
ROBOTS_URL = "https://tw.stock.yahoo.com/robots.txt"
PUBLIC_IP = "8.8.8.8"


class FakeResponse:
    def __init__(self, status=200, body=b"", content_type="text/html; charset=utf-8", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    """Maps URL -> FakeResponse or Exception. Unknown URLs answer 404."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.routes.get(url, FakeResponse(404, b"", "text/plain"))
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def urls(self):
        return [url for url, _ in self.calls]


def public_resolver(host, port):
    return [(2, 1, 6, "", (PUBLIC_IP, port))]


def run(url=ARTICLE_URL, routes=None, resolver=public_resolver):
    session = FakeSession(routes if routes is not None else {ARTICLE_URL: FakeResponse(200, FIXTURE)})
    result = fetch_article(url, session=session, resolver=resolver, robots_cache={})
    return result, session


def page(paragraphs, extra_head=""):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    html = (
        f"<html><head>{extra_head}</head><body><article><h1>測試標題</h1>"
        f'<div class="atoms">{body}</div></article></body></html>'
    )
    return FakeResponse(200, html.encode("utf-8"))


# ------------------------------------------------------------- extraction


def test_extracts_title_time_and_body_without_page_furniture():
    result, _ = run()

    assert result.ok and result.failure_reason is None
    assert result.title == "星河公司宣布新產品"
    assert result.published_at.isoformat() == "2026-09-19T05:00:05+00:00"
    assert result.source == "Yahoo奇摩新聞／股市"
    assert result.paragraph_count == 6

    body = result.body
    # Inline links keep their text and do not gain stray spaces.
    assert "新一代伺服器產品" in body
    # <br> becomes a space, sub-headings are kept as their own paragraph.
    assert "不對外提供報價。 其餘細節以公告為準。" in body
    assert "\n\n供貨與定價\n\n" in body
    # Ads, the vendor tail and everything after the related-articles heading are gone.
    for noise in ("廣告", "更多測試報導", "延伸閱讀", "另一則不該進入正文", "首頁 財經", "版權所有"):
        assert noise not in body
    assert result.warnings == []
    assert result.body_hash and len(result.body_hash) == 64


def test_page_without_article_is_no_body():
    html = "<html><body><h1>首頁</h1><p>熱門新聞列表</p></body></html>".encode()
    result, _ = run(routes={ARTICLE_URL: FakeResponse(200, html)})
    assert not result.ok
    assert result.failure_reason is FailureReason.NO_BODY
    assert result.body == ""
    assert result.title is None  # site noise must not prefill the paste form


def test_short_body_is_rejected_but_title_is_kept_for_the_paste_form():
    result, _ = run(routes={ARTICLE_URL: page(["只有一句話。"])})
    assert result.failure_reason is FailureReason.BODY_TOO_SHORT
    assert result.title == "測試標題"
    assert "6 字" in result.message


def test_overlong_body_is_rejected_not_truncated():
    result, _ = run(routes={ARTICLE_URL: page(["星" * 5000] * 5)})
    assert result.failure_reason is FailureReason.BODY_TOO_LONG
    assert result.body == ""


def test_short_but_usable_body_warns():
    result, _ = run(routes={ARTICLE_URL: page(["星河公司今日發布快訊。" * 15])})
    assert result.ok
    assert any("正文很短" in w for w in result.warnings)


def test_truncation_marker_in_last_paragraph_warns():
    paragraphs = ["星河公司今日宣布推出新產品，預計下季供貨。" * 8, "想知道更多，請點擊查看全文請見原網站。"]
    result, _ = run(routes={ARTICLE_URL: page(paragraphs)})
    assert result.ok
    assert any("可能被截斷" in w for w in result.warnings)


def test_missing_title_falls_back_and_then_warns():
    html = (
        '<html><head><meta property="og:title" content="備用標題"></head><body><article>'
        '<div class="atoms">' + "<p>" + "星河公司今日宣布推出新產品。" * 30 + "</p>" + "</div></article></body></html>"
    )
    result, _ = run(routes={ARTICLE_URL: FakeResponse(200, html.encode())})
    assert result.ok and result.title == "備用標題"

    bare = html.replace('<meta property="og:title" content="備用標題">', "")
    result, _ = run(routes={ARTICLE_URL: FakeResponse(200, bare.encode())})
    assert result.ok and result.title is None
    assert any("找不到標題" in w for w in result.warnings)


def test_time_without_zone_is_unknown():
    head = '<meta property="article:published_time" content="2026-09-19T13:00:00">'
    result, _ = run(routes={ARTICLE_URL: page(["星河公司今日宣布推出新產品。" * 30], extra_head=head)})
    assert result.ok and result.published_at is None


# ------------------------------------------------------------- URL safety


@pytest.mark.parametrize(
    "url, reason",
    [
        ("", FailureReason.INVALID_URL),
        ("not a url", FailureReason.INVALID_URL),
        ("ftp://tw.stock.yahoo.com/a", FailureReason.INVALID_URL),
        ("https://user:pw@tw.stock.yahoo.com/a", FailureReason.INVALID_URL),
        ("https://tw.stock.yahoo.com:8443/a", FailureReason.INVALID_URL),
        ("https://[::1/a", FailureReason.INVALID_URL),
        ("http://localhost/a", FailureReason.BLOCKED_ADDRESS),
        ("http://127.0.0.1/a", FailureReason.BLOCKED_ADDRESS),
        ("http://169.254.169.254/latest/meta-data", FailureReason.BLOCKED_ADDRESS),
    ],
)
def test_rejected_urls_never_touch_the_network(url, reason):
    result, session = run(url=url)
    assert result.failure_reason is reason
    assert session.calls == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1", "fd00::1"])
def test_allowed_host_resolving_to_non_public_address_is_blocked(address):
    resolver = lambda host, port: [(2, 1, 6, "", (address, port))]
    result, session = run(resolver=resolver)
    assert result.failure_reason is FailureReason.BLOCKED_ADDRESS
    assert session.calls == []


def test_one_bad_address_among_several_blocks():
    resolver = lambda host, port: [(2, 1, 6, "", (PUBLIC_IP, port)), (2, 1, 6, "", ("10.0.0.1", port))]
    result, _ = run(resolver=resolver)
    assert result.failure_reason is FailureReason.BLOCKED_ADDRESS


def test_dns_failure_is_a_network_error():
    def resolver(host, port):
        raise OSError("no such host")

    result, _ = run(resolver=resolver)
    assert result.failure_reason is FailureReason.NETWORK_ERROR


def test_redirect_to_another_allowed_page_is_followed_and_reported():
    target = "https://tw.news.yahoo.com/moved-456.html"
    routes = {
        ARTICLE_URL: FakeResponse(301, headers={"Location": target}),
        target: FakeResponse(200, FIXTURE),
    }
    result, session = run(routes=routes)
    assert result.ok and result.final_url == target
    assert all(kwargs["allow_redirects"] is False for _, kwargs in session.calls)


def test_redirect_to_new_public_host_is_checked_and_followed():
    routes = {ARTICLE_URL: FakeResponse(302, headers={"Location": "https://evil.example/x"})}
    result, session = run(routes=routes)
    assert result.failure_reason is FailureReason.HTTP_ERROR
    assert "https://evil.example/robots.txt" in session.urls
    assert "https://evil.example/x" in session.urls


def test_redirect_to_internal_address_is_stopped():
    routes = {ARTICLE_URL: FakeResponse(302, headers={"Location": "http://169.254.169.254/latest"})}
    result, session = run(routes=routes)
    assert result.failure_reason is FailureReason.BLOCKED_ADDRESS
    assert all("169.254" not in url for url in session.urls)


def test_redirect_loop_is_bounded():
    routes = {ARTICLE_URL: FakeResponse(302, headers={"Location": ARTICLE_URL})}
    result, session = run(routes=routes)
    assert result.failure_reason is FailureReason.TOO_MANY_REDIRECTS
    assert session.urls.count(ARTICLE_URL) == af.MAX_REDIRECTS + 1


# ------------------------------------------------------------- HTTP outcomes


def test_http_error_reports_status():
    result, _ = run(routes={ARTICLE_URL: FakeResponse(403, b"denied")})
    assert result.failure_reason is FailureReason.HTTP_ERROR
    assert "403" in result.message


def test_non_html_is_rejected():
    result, _ = run(routes={ARTICLE_URL: FakeResponse(200, b"%PDF", "application/pdf")})
    assert result.failure_reason is FailureReason.NOT_HTML


def test_oversized_download_is_stopped_by_header_and_by_streaming():
    huge = af.MAX_HTML_BYTES + 1
    by_header = FakeResponse(200, b"x", headers={"Content-Length": str(huge)})
    assert run(routes={ARTICLE_URL: by_header})[0].failure_reason is FailureReason.TOO_LARGE

    streamed = FakeResponse(200, b"x" * huge)
    assert run(routes={ARTICLE_URL: streamed})[0].failure_reason is FailureReason.TOO_LARGE


@pytest.mark.parametrize(
    "error, reason",
    [
        (requests.Timeout(), FailureReason.TIMEOUT),
        (requests.ConnectionError(), FailureReason.NETWORK_ERROR),
    ],
)
def test_transport_errors_map_to_reasons(error, reason):
    result, _ = run(routes={ARTICLE_URL: error})
    assert result.failure_reason is reason


def test_requests_identify_the_bot_and_set_timeouts():
    _, session = run()
    _, kwargs = session.calls[-1]
    assert kwargs["headers"]["User-Agent"] == af.USER_AGENT
    assert kwargs["timeout"][0] == af.CONNECT_TIMEOUT
    assert kwargs["stream"] is True


def test_responses_are_closed():
    response = FakeResponse(200, FIXTURE)
    run(routes={ARTICLE_URL: response})
    assert response.closed


# ------------------------------------------------------------- robots.txt


def robots(text, status=200):
    return FakeResponse(status, text.encode(), "text/plain")


def test_robots_disallow_for_our_agent_blocks_the_article_request():
    routes = {
        ROBOTS_URL: robots("User-agent: HeadlineMirrorBot\nDisallow: /\n"),
        ARTICLE_URL: FakeResponse(200, FIXTURE),
    }
    result, session = run(routes=routes)
    assert result.failure_reason is FailureReason.ROBOTS_DISALLOWED
    assert ARTICLE_URL not in session.urls


def test_robots_rules_for_other_bots_do_not_apply_to_us():
    routes = {
        ROBOTS_URL: robots("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /api\n"),
        ARTICLE_URL: FakeResponse(200, FIXTURE),
    }
    assert run(routes=routes)[0].ok


def test_robots_wildcard_group_can_block():
    routes = {ROBOTS_URL: robots("User-agent: *\nDisallow: /news/\n"), ARTICLE_URL: FakeResponse(200, FIXTURE)}
    assert run(routes=routes)[0].failure_reason is FailureReason.ROBOTS_DISALLOWED


def test_missing_robots_means_allowed_but_server_error_means_stop():
    ok = {ROBOTS_URL: robots("", 404), ARTICLE_URL: FakeResponse(200, FIXTURE)}
    assert run(routes=ok)[0].ok

    broken = {ROBOTS_URL: robots("", 503), ARTICLE_URL: FakeResponse(200, FIXTURE)}
    result, session = run(routes=broken)
    assert result.failure_reason is FailureReason.HTTP_ERROR
    assert ARTICLE_URL not in session.urls


def test_robots_is_cached_per_host():
    session = FakeSession({ROBOTS_URL: robots("User-agent: *\nAllow: /\n"), ARTICLE_URL: FakeResponse(200, FIXTURE)})
    cache = {}
    for _ in range(2):
        fetch_article(ARTICLE_URL, session=session, resolver=public_resolver, robots_cache=cache)
    assert session.urls.count(ROBOTS_URL) == 1


@pytest.mark.parametrize("url, html, expected_source", [
    ("https://news.pts.org.tw/article/123", '<article><h1>測試</h1><div class="post-article"><div class="articleimg">LEAD</div><p>BODY</p><aside><p>NOISE</p></aside></div></article>', "公視新聞"),
    ("https://news.ltn.com.tw/news/business/breakingnews/123", '<div class="article"><h1>測試</h1><div class="article_wrap"><div class="text"><p>LEAD</p><p>BODY</p><p class="before_ir">NOISE</p><p class="appE1121">NOISE</p><div class="photo"><p>NOISE</p></div></div></div></div>', "自由時報"),
])
def test_new_sources_keep_lead_and_skip_furniture(url, html, expected_source):
    lead = "公司發布新產品並公布本季營收。" * 15
    body = "董事會表示下季將持續投資研發。" * 15
    html = html.replace("LEAD", lead).replace("BODY", body)
    result, _ = run(url=url, routes={url: FakeResponse(200, html.encode())})
    assert result.ok
    assert result.source == expected_source
    assert result.title == "測試"
    assert result.body == lead + "\n\n" + body
    assert result.paragraph_count == 2


@pytest.mark.parametrize("host", ["news.pts.org.tw", "news.ltn.com.tw"])
def test_new_sources_respect_robots_and_exact_hosts(host):
    url = "https://" + host + "/article/123"
    robots = "https://" + host + "/robots.txt"
    result, session = run(url=url, routes={robots: FakeResponse(200, b"User-agent: *\nDisallow: /", "text/plain")})
    assert result.failure_reason == FailureReason.ROBOTS_DISALLOWED
    assert url not in session.urls
    result, session = run(url="https://" + host + ".evil.example/article/123")
    assert result.failure_reason == FailureReason.HTTP_ERROR
    assert af.find_source(host + ".evil.example") is None
