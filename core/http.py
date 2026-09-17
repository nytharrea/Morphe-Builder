"""FlareSolverr çerezlerini taşıyan ve akış indiren ağ katmanı."""

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.clients.flaresolverr import FlareSolverrClient

logger = logging.getLogger("morphe.http")


class NetworkSessionManager:
    """Hibrit oturum senkronizasyonu ve doğrudan ikili dosya akış yöneticisi."""

    def __init__(self, solver_endpoint: str = "http://localhost:8191"):
        self.solver = FlareSolverrClient(endpoint=solver_endpoint)
        self.session = requests.Session()
        self._init_session()

    def _init_session(self) -> None:
        retries = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_page_with_solver(self, url: str) -> str:
        """Sayfayı FlareSolverr ile çözer, çerez ve UA bilgisini oturuma aktarır."""
        html, cookies, user_agent = self.solver.resolve_page(url)
        self.session.cookies.update(cookies)
        if user_agent:
            self.session.headers.update({"User-Agent": user_agent})
        return html

    def download_file(
        self, url: str, destination_path: str, referer: str | None = None
    ) -> None:
        """Büyük APK dosyalarını bellek tüketmeden doğrudan diske akıtır."""
        headers: dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        if referer:
            headers["Referer"] = referer

        logger.info("APK indirme akışı başlatılıyor: %s -> %s", url, destination_path)
        with self.session.get(
            url, headers=headers, stream=True, timeout=180
        ) as response:
            response.raise_for_status()
            with open(destination_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        output_file.write(chunk)
        logger.info("İndirme başarıyla tamamlandı: %s", destination_path)
