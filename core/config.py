"""Merkezi uygulama yapılandırması. Termius tamamen kaldırılmıştır."""

from typing import Any

APPLICATIONS: dict[str, dict[str, Any]] = {
    "instagram": {
        "package_name": "com.instagram.android",
        "source_provider": "apkmirror",
        "provider_config": {
            "org": "instagram",
            "app_slug": "instagram-android",
            "arch": "arm64-v8a",
            "dpi": "nodpi",
            "version": None,
        },
        "patches": [
            "disable-ads",
            "enable-debugging",
        ],
        "output_apk_name": "Instagram-Morphe-Patched.apk",
    },
    "speedtest": {
        "package_name": "com.ookla.speedtest",
        "source_provider": "apkmirror",
        "provider_config": {
            "org": "ookla",
            "app_slug": "speedtest",
            "arch": "arm64-v8a",
            "dpi": "nodpi",
            "version": None,
        },
        "patches": [
            "remove-ads",
            "unlock-premium",
        ],
        "output_apk_name": "Speedtest-Morphe-Patched.apk",
    },
    "youtube": {
        "package_name": "com.google.android.youtube",
        "source_provider": "github_apk",
        "provider_config": {
            "repo": "Morphe/App-Sources",
            "asset_pattern": r"youtube-.*\.apk",
        },
        "patches": ["video-ads", "sponsorblock"],
        "output_apk_name": "YouTube-Morphe-Patched.apk",
    },
    "youtube-music": {
        "package_name": "com.google.android.apps.youtube.music",
        "source_provider": "github_apk",
        "provider_config": {
            "repo": "Morphe/App-Sources",
            "asset_pattern": r"youtube-music-.*\.apk",
        },
        "patches": ["music-ads", "background-playback"],
        "output_apk_name": "YouTubeMusic-Morphe-Patched.apk",
    },
}
