from dataclasses import dataclass

from curl_cffi.requests import AsyncSession
from tenacity import retry, stop_after_attempt

from . import retry as retry_conf
from .settings import settings


class FlareSolverrError(Exception):
    pass


@dataclass
class Solution:
    url: str
    status: int
    html: str
    cookies: list[dict]
    user_agent: str
    screenshot_base64: str | None = None


def _endpoint() -> str:
    return settings.flaresolverr_url.rstrip("/")


@retry(
    stop=stop_after_attempt(4),
    wait=retry_conf.incrementing(start=1.0, increment=1.5, max=6.0),
    before_sleep=retry_conf.before_sleep("Could not reach FlareSolverr"),
    reraise=True,
)
async def _post(payload: dict) -> dict:
    client_timeout = (payload.get("maxTimeout", 60_000) / 1000) + 30
    async with AsyncSession(timeout=client_timeout) as client:
        response = await client.post(_endpoint(), json=payload)

    if response.status_code >= 400:
        raise FlareSolverrError(f"FlareSolverr HTTP {response.status_code}: {response.text[:200]}")

    try:
        return response.json()
    except Exception as e:
        raise FlareSolverrError(f"FlareSolverr returned a non-JSON response: {e}") from e


async def create_session(session: str | None = None) -> str:
    payload: dict = {"cmd": "sessions.create"}
    if session:
        payload["session"] = session

    data = await _post(payload)
    if data.get("status") != "ok" or not data.get("session"):
        raise FlareSolverrError(data.get("message") or "sessions.create did not return a session id")
    return data["session"]


async def destroy_session(session: str) -> None:
    data = await _post({"cmd": "sessions.destroy", "session": session})
    if data.get("status") != "ok":
        raise FlareSolverrError(data.get("message") or "sessions.destroy failed")


async def solve_get(
    url: str,
    *,
    session: str | None = None,
    max_timeout_ms: int = 60_000,
    wait_seconds: float | None = None,
    return_screenshot: bool = False,
    disable_media: bool = False,
    tabs_till_verify: int | None = None,
) -> Solution:
    payload: dict = {"cmd": "request.get", "url": url, "maxTimeout": max_timeout_ms}
    if session:
        payload["session"] = session
    if wait_seconds:
        payload["waitInSeconds"] = wait_seconds
    if return_screenshot:
        payload["returnScreenshot"] = True
    if disable_media:
        payload["disableMedia"] = True
    if tabs_till_verify:
        payload["tabs_till_verify"] = tabs_till_verify

    data = await _post(payload)

    if data.get("status") != "ok":
        raise FlareSolverrError(data.get("message") or "FlareSolverr could not resolve the challenge")

    solution = data.get("solution") or {}
    return Solution(
        url=solution.get("url") or url,
        status=int(solution.get("status") or 0),
        html=solution.get("response") or "",
        cookies=solution.get("cookies") or [],
        user_agent=solution.get("userAgent") or "",
        screenshot_base64=solution.get("screenshot"),
    )
