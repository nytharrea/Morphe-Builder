"""FlareSolverr v3.5.2 REST API istemci modülü."""

import logging
from typing import Any

import requests

logger = logging.getLogger("morphe.flaresolverr")


class FlareSolverrClient:
    """FlareSolverr REST API üzerinden Cloudflare engellerini aşan istemci."""

    def __init__(self, endpoint: str = "http://localhost:8191", timeout: int = 60):
        self.endpoint = endpoint.rstrip("/")
        self.api_url = f"{self.endpoint}/v1"
        self.timeout = timeout
        self.session_id: str | None = None

    def create_session(self, session_id: str | None = None) -> str:
        """Kalıcı bir Chromium tarayıcı oturumu açar."""
        payload: dict[str, Any] = {"cmd": "sessions.create"}
        if session_id:
            payload["session"] = session_id

        response = requests.post(self.api_url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "ok":
            raise RuntimeError(f"Oturum oluşturulamadı: {data.get('message')}")

        self.session_id = data.get("session")
        logger.info("FlareSolverr oturumu başlatıldı: %s", self.session_id)
        return str(self.session_id)

    def destroy_session(self) -> None:
        """Açık oturumu kapatır ve kaynakları serbest bırakır."""
        if not self.session_id:
            return

        payload = {"cmd": "sessions.destroy", "session": self.session_id}
        try:
            requests.post(self.api_url, json=payload, timeout=self.timeout)
            logger.info("FlareSolverr oturumu sonlandırıldı: %s", self.session_id)
        except Exception as exc:
            logger.warning("Oturum kapatma uyarısı: %s", exc)
        finally:
            self.session_id = None

    def get_solution(self, url: str) -> dict[str, Any]:
        """Hedef URL'yi çözer ve çözüm kümesini döner."""
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self.timeout * 1000,
        }
        if self.session_id:
            payload["session"] = self.session_id

        logger.debug("Cloudflare meydan okuması çözülüyor: %s", url)
        response = requests.post(self.api_url, json=payload, timeout=self.timeout + 15)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "ok":
            raise RuntimeError(f"FlareSolverr isteği çözemedi ({url}): {data.get('message')}")

        return data.get("solution", {})

    def resolve_page(self, url: str) -> tuple[str, dict[str, str], str]:
        """Çözülen sayfanın HTML içeriğini, çerezlerini ve User-Agent bilgisini döner."""
        solution = self.get_solution(url)
        html_content = solution.get("response", "")
        user_agent = solution.get("userAgent", "")

        cookies_dict: dict[str, str] = {}
        for cookie in solution.get("cookies", []):
            cookies_dict[cookie["name"]] = cookie["value"]

        return html_content, cookies_dict, user_agent
