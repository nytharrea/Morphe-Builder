from curl_cffi.requests import AsyncSession

from .settings import settings

IMPERSONATE = "firefox"


def github_headers(base: dict[str, str] | None = None) -> dict[str, str]:
    """Build headers for a GitHub API request, adding Authorization only if
    a token is actually configured. Sending `Authorization: Bearer ` with
    an empty value (the old behavior when GITHUB_TOKEN was unset) is a
    malformed credential that GitHub can reject outright; omitting the
    header entirely falls back to a normal anonymous request instead."""
    headers = dict(base or {})
    token = settings.github_token.get_secret_value()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def new_session(
    *, timeout: float | None = 30, follow_redirects: bool = True, impersonate: str | None = IMPERSONATE, **kwargs
) -> AsyncSession:
    return AsyncSession(
        timeout=timeout,  # type: ignore[arg-type]
        allow_redirects=follow_redirects,
        impersonate=impersonate,
        **kwargs,
    )
