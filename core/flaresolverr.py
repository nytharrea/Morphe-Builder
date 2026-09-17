"""FlareSolverr (v3.5.2) HTTP API istemcisi.

Camoufox + Playwright kaldirildi; Cloudflare challenge cozumu FlareSolverr
sunucusuna devredildi. Bu modul:
- calisan bir sunucu saglar (healthcheck),
- kalici, isimli bir oturum acar ("sessions.create") ki cf_clearance cerezi
  tum isteklerde paylasilsin,
- challenge'a takilan sayfalari "request.get" ile cozer ve cozumden donen
  cerezleri + user-agent'i APKMirror HTTP oturumuna aktarir,
- is bitince oturumu yok eder ("sessions.destroy").
"""

import asyncio
import contextlib

from tenacity import retry, stop_after_attempt

from . import log
from . import retry as retry_conf
from .http import new_session
from .settings import settings


class FlareSolverrError(RuntimeError):
    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class FlareSolverrClient:
    def __init__(self) -> None:
        self._base = settings.flaresolverr_url.rstrip("/")
        self._session_name = settings.flaresolverr_session
        self._max_timeout = settings.flaresolverr_max_timeout_ms
        self._started = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "FlareSolverrClient":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=retry_conf.exponential_with_jitter(max=15.0),
        before_sleep=retry_conf.before_sleep("FlareSolverr istegi"),
        reraise=True,
    )
    async def _command(self, payload: dict) -> dict:
        async with new_session(timeout=None) as client:
            res = await client.post(f"{self._base}/v1", json=payload)
            if res.status_code != 200:
                raise FlareSolverrError(f"FlareSolverr HTTP {res.status_code}")
            data = res.json()
        if data.get("status") != "ok":
            raise FlareSolverrError(
                f"FlareSolverr hatasi: {data.get('message')}",
                error_code=data.get("error"),
            )
        return data

    async def start(self) -> None:
        """Healthcheck + kalici oturum olustur (idempotent)."""
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            log.step(f"FlareSolverr baglaniliyor: {self._base} (v3.5.2)")
            await self._command({"cmd": "sessions.create", "session": self._session_name})
            self._started = True
            log.success(f"FlareSolverr oturumu hazir: {self._session_name}")

    async def close(self) -> None:
        if not self._started:
            return
        with contextlib.suppress(Exception):
            await self._command({"cmd": "sessions.destroy", "session": self._session_name})
        self._started = False

    async def solve_get(self, url: str, referer: str | None = None) -> dict:
        """Cloudflare challenge'li sayfayi FlareSolverr ile ac.

        Donen cozum: {cookies, userAgent, response, url, status}.
        """
        log.notice(f"Cloudflare challenge cozuluyor (FlareSolverr): {url}")
        payload: dict = {
            "cmd": "request.get",
            "url": url,
            "session": self._session_name,
            "maxTimeout": self._max_timeout,
        }
        if referer:
            payload["headers"] = {"Referer": referer}
        data = await self._command(payload)
        solution = data.get("solution") or {}
        if not solution.get("cookies"):
            raise FlareSolverrError("FlareSolverr cozumde cerez dondurmedi")
        return solution
