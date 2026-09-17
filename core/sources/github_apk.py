"""GitHub Releases üzerinden APK sağlayan modül."""

import logging
import re

import requests

logger = logging.getLogger("morphe.sources.github")


class GitHubSourceProvider:
    def __init__(self, token: str | None = None):
        self.token = token

    def get_release_asset_url(self, repo: str, asset_pattern: str) -> str:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        for asset in data.get("assets", []):
            if re.search(asset_pattern, asset["name"]):
                return asset["browser_download_url"]

        raise RuntimeError(f"Eşleşen varlık bulunamadı: {asset_pattern} ({repo})")
