"""
Per-client request limits for endpoints that cost something (fetching other
sites, calling the model).

In memory and per process, so it fits the single-worker setup the README
describes. It is a guard against a runaway script or an open demo link, not a
substitute for a gateway. Limits come from environment variables read on each
request, so they can be changed without a code edit:

    RATE_LIMIT_PREVIEW, RATE_LIMIT_CONFIRM, RATE_LIMIT_ANALYZE   requests per minute
    a value of 0 turns that limit off; anything unusable falls back to the default
    TRUST_PROXY_HEADERS=1   take the client address from X-Forwarded-For
                            (only behind a proxy you control; otherwise it can be forged)
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from typing import Callable

from fastapi import HTTPException, Request

_limiters: list["RateLimiter"] = []


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, max_clients: int = 10_000) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        _limiters.append(self)

    def hit(self, key: str) -> float | None:
        """Record a request. Returns None if allowed, else seconds until it would be."""
        now = self.clock()
        if len(self._hits) >= self.max_clients:
            self._forget_idle(now)
        stamps = self._hits.setdefault(key, deque())
        while stamps and now - stamps[0] >= self.window:
            stamps.popleft()
        if len(stamps) >= self.limit:
            return self.window - (now - stamps[0])
        stamps.append(now)
        return None

    def _forget_idle(self, now: float) -> None:
        for key in [k for k, s in self._hits.items() if not s or now - s[-1] >= self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


def reset_all() -> None:
    """Clear every limiter (tests call this so limits never leak between tests)."""
    for limiter in _limiters:
        limiter.reset()


def client_key(request: Request) -> str:
    if os.environ.get("TRUST_PROXY_HEADERS") == "1":
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def _configured(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name, "").strip()
    return int(raw) if raw.isdigit() else default


def limit_requests(name: str, default_per_minute: int) -> Callable[[Request], None]:
    """FastAPI dependency: at most RATE_LIMIT_<name> requests per minute per client."""
    env_name = f"RATE_LIMIT_{name}"
    state: dict[str, RateLimiter] = {}

    def dependency(request: Request) -> None:
        limit = _configured(env_name, default_per_minute)
        if limit == 0:
            return
        limiter = state.get("limiter")
        if limiter is None or limiter.limit != limit:
            if limiter is not None:
                _limiters.remove(limiter)
            limiter = state["limiter"] = RateLimiter(limit)
        retry_after = limiter.hit(client_key(request))
        if retry_after is not None:
            seconds = max(1, math.ceil(retry_after))
            raise HTTPException(
                status_code=429,
                detail={"code": "rate_limited", "message": f"請求太頻繁，請 {seconds} 秒後再試。"},
                headers={"Retry-After": str(seconds)},
            )

    return dependency
