"""Minimal FlareSolverr v1 client used by the APKMirror source.

FlareSolverr owns the browser/Cloudflare session. This project only asks it
for rendered HTML plus cookies, then continues with normal HTTP downloads.
"""

from __future__ import annotations

import asyncio
from typing import Any

from curl_cffi.requests import AsyncSession

from .settings import settings


class FlareSolverrError(RuntimeError):
    pass


class FlareSolverrChallengeError(FlareSolverrError):
    """FlareSolverr responded, but the page is still behind a challenge."""


_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required! | cloudflare",
    "verify you are human",
    "cf-browser-verification",
    "cf_chl_",
    "ddos protection by cloudflare",
    "performing security verification",
    "verifies you are not a bot",
)


def _looks_like_challenge(status: int | None, html: str) -> bool:
    if status in (401, 403, 503):
        return True
    lowered = (html or "").lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


async def request_get(
    url: str, *, session: str | None = None, max_timeout_ms: int | None = None
) -> dict[str, Any]:
    """Return FlareSolverr's ``solution`` object for ``cmd=request.get``."""
    base = settings.flaresolverr_url.rstrip("/")
    if not base:
        raise FlareSolverrError("FLARESOLVERR_URL is empty")

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout_ms or settings.flaresolverr_max_timeout_ms,
        "session": session or settings.flaresolverr_session,
    }

    async with AsyncSession(
        timeout=(settings.flaresolverr_max_timeout_ms / 1000) + 30, impersonate="firefox"
    ) as client:
        res = await client.post(f"{base}/v1", json=payload)
        if res.status_code >= 400:
            raise FlareSolverrError(f"FlareSolverr HTTP {res.status_code}: {res.text[:500]}")
        data = res.json()

    if data.get("status") != "ok":
        raise FlareSolverrError(f"FlareSolverr failed: {data.get('message') or data}")

    solution = data.get("solution") or {}
    status = solution.get("status")
    html = solution.get("response") or ""
    if _looks_like_challenge(status, html):
        raise FlareSolverrChallengeError(f"FlareSolverr still sees a challenge for {url} (HTTP {status})")
    return solution


async def wait_until_ready(*, attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Best-effort readiness loop for CI before the first APKMirror request."""
    base = settings.flaresolverr_url.rstrip("/")
    for _ in range(attempts):
        try:
            async with AsyncSession(timeout=5, impersonate="firefox") as client:
                res = await client.get(base)
                if res.status_code < 500:
                    return
        except Exception:
            pass
        await asyncio.sleep(delay_seconds)
    raise FlareSolverrError(f"FlareSolverr did not become ready at {base}")
