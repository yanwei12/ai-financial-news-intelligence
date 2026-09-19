import pytest

from src.api import ratelimit


@pytest.fixture(autouse=True)
def _fresh_rate_limits(monkeypatch):
    """Request limits are per process; without this, one test's requests would count against the next."""
    for name in ("RATE_LIMIT_PREVIEW", "RATE_LIMIT_CONFIRM", "RATE_LIMIT_ANALYZE",
                 "HEADLINE_DAILY_LIMIT", "TRUST_PROXY_HEADERS"):
        monkeypatch.delenv(name, raising=False)
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()
