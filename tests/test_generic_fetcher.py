import pytest
from src.ingestion import article_fetcher as af
from tests.test_article_fetcher import FakeResponse, FakeSession, public_resolver

URL = "https://general-news.example/story/123"
BODY = "新研究發現電池回收效率持續提升，團隊公布測試方法與量測結果。研究人員提醒，目前仍須在量產環境驗證成本與效益。" * 8
HTML = ('<html><head><meta charset="utf-8"><meta property="og:type" content="article"><title>電池回收研究</title></head><body><nav>導航廣告</nav><article><h1>電池回收研究</h1><p>' + BODY + '</p><p>' + BODY.replace("電池", "材料") + '</p></article><aside>側欄廣告</aside><footer>頁尾</footer></body></html>')


def fetch(html=HTML, **kwargs):
    return af.fetch_article(URL, session=FakeSession({URL: FakeResponse(200, html.encode())}), resolver=public_resolver, robots_cache={}, **kwargs)


def test_generic_article_extracts_text_and_excludes_navigation():
    result = fetch()
    assert result.ok
    assert result.source == "general-news.example"
    assert result.title == "電池回收研究"
    assert BODY in result.body
    assert "導航廣告" not in result.body and "側欄廣告" not in result.body


def test_index_and_javascript_shell_are_not_articles():
    for html in ('<h1>新聞首頁</h1><p>' + BODY + '</p>', '<article><h1>新聞</h1><div id="app"></div><script>loadArticle()</script></article>'):
        assert not fetch(html).ok


def test_paywall_metadata_is_rejected():
    html = HTML.replace('</head>', '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script></head>')
    assert fetch(html).failure_reason == af.FailureReason.RESTRICTED_CONTENT


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fc00::1", "224.0.0.1"])
def test_unknown_domain_resolving_to_private_ip_never_connects(ip):
    session = FakeSession({})
    result = af.fetch_article(URL, session=session, resolver=lambda h,p: [(2,1,6,"",(ip,p))], robots_cache={})
    assert result.failure_reason == af.FailureReason.BLOCKED_ADDRESS
    assert session.calls == []


def test_empty_dns_result_fails_closed():
    result = af.fetch_article(URL, session=FakeSession({}), resolver=lambda h,p: [], robots_cache={})
    assert result.failure_reason == af.FailureReason.NETWORK_ERROR


def test_https_connection_uses_checked_ip_and_original_tls_hostname(monkeypatch):
    calls = {}
    class Response:
        status = 200
        headers = {}
        def close(self): pass
    class Pool:
        def __init__(self, host, port, **kwargs): calls.update(host=host, port=port, options=kwargs)
        def urlopen(self, method, target, **kwargs):
            calls.update(target=target, request=kwargs)
            return Response()
        def close(self): pass
    monkeypatch.setattr(af.urllib3, "HTTPSConnectionPool", Pool)
    response = af._PinnedSession(public_resolver).get(URL, headers={}, timeout=(5,10))
    assert calls["host"] == "8.8.8.8"
    assert calls["options"]["server_hostname"] == "general-news.example"
    assert calls["options"]["assert_hostname"] == "general-news.example"
    assert calls["options"]["cert_reqs"] == "CERT_REQUIRED"
    assert calls["request"]["headers"]["Host"] == "general-news.example"
    assert calls["request"]["redirect"] is False
    response.close()


def test_rebinding_to_internal_ip_is_rejected_before_connection(monkeypatch):
    answers = iter([[(2,1,6,"",("8.8.8.8",443))], [(2,1,6,"",("127.0.0.1",443))]])
    resolver = lambda h,p: next(answers)
    af._check_url(URL, resolver, allow_generic=True)
    def forbidden(*args, **kwargs): pytest.fail("Connected after private DNS answer")
    monkeypatch.setattr(af.urllib3, "HTTPSConnectionPool", forbidden)
    with pytest.raises(af.FetchError) as error:
        af._PinnedSession(resolver).get(URL, headers={}, timeout=(5,10))
    assert error.value.reason == af.FailureReason.BLOCKED_ADDRESS


def test_malformed_metadata_does_not_crash():
    html = HTML.replace('</head>', '<script type="application/ld+json">{"@graph":null,"@type":"NewsArticle","headline":{"invalid":true}}</script></head>')
    result = fetch(html)
    assert result.ok and result.title == "電池回收研究"


def test_generic_site_robots_blocks_article():
    robots_url = "https://general-news.example/robots.txt"
    session = FakeSession({robots_url: FakeResponse(200, b"User-agent: *\nDisallow: /", "text/plain")})
    result = af.fetch_article(URL, session=session, resolver=public_resolver, robots_cache={})
    assert result.failure_reason == af.FailureReason.ROBOTS_DISALLOWED
    assert URL not in session.urls


def test_generic_redirect_to_private_dns_is_blocked():
    target = "https://private.example/story"
    session = FakeSession({URL: FakeResponse(302, headers={"Location": target})})
    def resolver(host, port):
        return [(2,1,6,"",("10.0.0.1" if host == "private.example" else "8.8.8.8",port))]
    result = af.fetch_article(URL, session=session, resolver=resolver, robots_cache={})
    assert result.failure_reason == af.FailureReason.BLOCKED_ADDRESS
    assert target not in session.urls
