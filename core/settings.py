"""Ortam değişkenleri ve ayar yöneticisi."""

import os

FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
BUILD_DIR = os.getenv("BUILD_DIR", "build")
