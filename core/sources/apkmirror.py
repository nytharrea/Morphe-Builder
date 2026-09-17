"""APKMirror 4 aşamalı sürüm çözümleyici ve indirme sağlayıcısı."""

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.http import NetworkSessionManager

logger = logging.getLogger("morphe.sources.apkmirror")


class APKMirrorSourceProvider:
    """APKMirror üzerinden mimari ve DPI uyumlu APK'ları ayıklar."""

    BASE_URL = "https://www.apkmirror.com"

    def __init__(self, session_manager: NetworkSessionManager):
        self.http = session_manager

    def resolve_apk_download(
        self,
        org: str,
        app_slug: str,
        target_arch: str = "arm64-v8a",
        target_dpi: str = "nodpi",
        version: str | None = None,
    ) -> str:
        # 1. Aşama: Kararlı sürüm tespiti
        listing_url = f"{self.BASE_URL}/apk/{org}/{app_slug}/"
        logger.info("Sürüm listesi sorgulanıyor: %s", listing_url)
        listing_html = self.http.fetch_page_with_solver(listing_url)
        version_url = self._extract_version_page_url(listing_html, app_slug, version)

        # 2. Aşama: Mimarî varyant seçimi
        logger.info("Sürüm detay sayfası açılıyor: %s", version_url)
        version_html = self.http.fetch_page_with_solver(version_url)
        variant_url = self._extract_variant_url(version_html, target_arch, target_dpi)

        # 3. Aşama: Varyant indirme yönlendirmesi
        logger.info("Varyant sayfası açılıyor: %s", variant_url)
        variant_html = self.http.fetch_page_with_solver(variant_url)
        final_download_page_url = self._extract_final_download_page_url(variant_html)

        # 4. Aşama: Nihai ikili dosya bağlantısı
        logger.info("İndirme buton sayfası taranıyor: %s", final_download_page_url)
        final_html = self.http.fetch_page_with_solver(final_download_page_url)
        direct_stream_url = self._extract_direct_stream_url(final_html)

        return direct_stream_url

    def _extract_version_page_url(self, html: str, app_slug: str, version: str | None) -> str:
        soup = BeautifulSoup(html, "html.parser")
        app_rows = soup.find_all("div", class_="appRow")

        for row in app_rows:
            title_tag = row.find("a", class_="fontBlack")
            if not title_tag:
                continue

            title_text = title_tag.get_text(strip=True).lower()
            if "beta" in title_text or "alpha" in title_text:
                continue

            link = title_tag.get("href")
            if version:
                if version.lower() in title_text:
                    return urljoin(self.BASE_URL, link)
            else:
                return urljoin(self.BASE_URL, link)

        raise RuntimeError(f"APKMirror üzerinde kararlı sürüm tespit edilemedi: {app_slug}")

    def _extract_variant_url(self, html: str, target_arch: str, target_dpi: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("div", class_="table-row-group") or soup.find("div", class_="variants-table")
        if not table:
            raise RuntimeError("Varyant tablosu HTML içinde bulunamadı.")

        rows = table.find_all("div", class_="table-row")
        selected_url = None

        for row in rows:
            cells = row.find_all("div", class_="table-cell")
            if len(cells) < 4:
                continue

            pkg_type = cells[0].get_text(strip=True).upper()
            if "APK" not in pkg_type or "BUNDLE" in pkg_type:
                continue

            arch_text = cells[1].get_text(strip=True).lower()
            dpi_text = cells[3].get_text(strip=True).lower()

            match_arch = (target_arch.lower() in arch_text) or ("universal" in arch_text)
            match_dpi = (target_dpi.lower() in dpi_text) or ("nodpi" in dpi_text)

            if match_arch and match_dpi:
                link_tag = row.find("a", class_="accent_color")
                if link_tag and link_tag.get("href"):
                    selected_url = urljoin(self.BASE_URL, link_tag["href"])
                    break

        if not selected_url:
            raise RuntimeError(f"Hedef mimariye uygun varyant bulunamadı: {target_arch}, {target_dpi}")

        return selected_url

    def _extract_final_download_page_url(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        btn = soup.find("a", class_=re.compile("downloadButton|accent_bg"))
        if btn and btn.get("href"):
            return urljoin(self.BASE_URL, btn["href"])
        raise RuntimeError("Varyant sayfasında indirme yönlendirme butonu bulunamadı.")

    def _extract_direct_stream_url(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        btn = soup.find("a", id="download-link")
        if not btn:
            btn = soup.find("a", href=re.compile(r"download\.php\?id="))

        if btn and btn.get("href"):
            return urljoin(self.BASE_URL, btn["href"])
        raise RuntimeError("Nihai doğrudan APK akış bağlantısı ayrıştırılamadı.")
