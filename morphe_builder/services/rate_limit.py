from __future__ import annotations

import asyncio
import random


class TokenBucket:
    def __init__(self, rate_per_minute: float, burst: float | None = None):
        self.capacity = burst or max(1.0, rate_per_minute)
        self.tokens = self.capacity
        self.rate = rate_per_minute / 60.0
        self.updated = asyncio.get_event_loop().time()

    async def acquire(self) -> None:
        while True:
            now = asyncio.get_event_loop().time()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep(max(0.05, (1 - self.tokens) / self.rate))


def jitter(base_seconds: float, spread: float = 0.6) -> float:
    return max(0.0, base_seconds + random.uniform(-spread, spread))
