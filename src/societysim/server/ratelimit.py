"""Per-client request rate: a token bucket per IP, 429 with Retry-After when it is empty."""

import math
import time
from collections.abc import Callable
from typing import Any

from starlette.requests import Request

from societysim.server.security import PlayConfig, client_ip, error_response

MAX_CLIENTS = 10_000  # buckets kept before full (idle) ones are dropped


class RateLimiter:
    """`per_min` requests a minute per key, in bursts of up to `per_min`."""

    def __init__(self, per_min: int, clock: Callable[[], float] = time.monotonic):
        self.capacity = float(per_min)
        self.rate = per_min / 60.0  # tokens per second
        self.clock = clock
        self.buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, updated)

    def take(self, key: str) -> float:
        """0 when the request may go ahead, else seconds until it may."""
        if self.rate <= 0:
            return 60.0
        now = self.clock()
        tokens, updated = self.buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - updated) * self.rate)
        wait = 0.0 if tokens >= 1 else (1 - tokens) / self.rate
        self.buckets[key] = (tokens - 1 if wait == 0 else tokens, now)
        if len(self.buckets) > MAX_CLIENTS:
            refill = self.capacity / self.rate
            self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < refill}
        return wait


class RateLimit:
    """ASGI middleware over every HTTP request, keyed on the client IP."""

    def __init__(self, app: Any, play: PlayConfig):
        self.app, self.play = app, play
        self.limiter = RateLimiter(play.rate_per_min)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            wait = self.limiter.take(client_ip(Request(scope), self.play.trusted_proxies))
            if wait:
                headers = {"Retry-After": str(max(1, math.ceil(wait)))}
                response = error_response(
                    429, "rate_limited", "too many requests; slow down", headers
                )
                return await response(scope, receive, send)
        await self.app(scope, receive, send)
