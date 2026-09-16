from curl_cffi.requests import AsyncSession

IMPERSONATE = "firefox"


def new_session(
    *,
    timeout: float | None = 30,
    follow_redirects: bool | None = None,
    allow_redirects: bool | None = None,
    **kwargs,
) -> AsyncSession:
    """Create a curl_cffi session.

    curl_cffi uses ``allow_redirects``. The old project API used
    ``follow_redirects``, so accept both spellings and normalize here.
    """
    redirects: bool
    if allow_redirects is not None:
        redirects = allow_redirects
    elif follow_redirects is not None:
        redirects = follow_redirects
    else:
        redirects = True
    return AsyncSession(
        timeout=timeout,  # type: ignore[arg-type]
        allow_redirects=redirects,
        impersonate=IMPERSONATE,
        **kwargs,
    )
