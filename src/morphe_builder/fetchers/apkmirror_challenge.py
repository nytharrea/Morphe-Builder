"""Cloudflare challenge-page detection and the escalating cooldown built on
top of it. Deliberately has no dependency on apkmirror_parse.py or lxml:
looks_like_challenge() takes a page-text-extraction function as a
parameter instead of importing one, so this module only knows about
"challenge detected or not", never how a page got turned into text.
"""

import asyncio
import time
from collections.abc import Callable

from tenacity.stop import stop_base
from tenacity.wait import wait_base

from .. import log

_CHALLENGE_MARKERS = [
    "just a moment",
    "checking your browser",
    "attention required! | cloudflare",
    "verify you are human",
    "cf-browser-verification",
    "cf_chl_",
    "ddos protection by cloudflare",
    "performing security verification",
    "verifies you are not a bot",
]

_challenge_hits = 0
_cooldown_until = 0.0


def challenge_hits() -> int:
    """How many challenges have been hit so far this run - used only for
    log messages (the retry loops themselves key off ChallengePresent's own
    .cooldown, not this counter)."""
    return _challenge_hits


def looks_like_challenge(html: str, page_text: Callable[[str], str]) -> bool:
    if not html:
        return False
    content = page_text(html) or html[:1000].lower()
    return any(marker in content for marker in _CHALLENGE_MARKERS)


async def apply_global_cooldown() -> None:
    now = time.monotonic()
    if now < _cooldown_until:
        remaining = _cooldown_until - now
        log.wait(f"Global cooldown active, waiting {remaining:.0f}s...")
        await asyncio.sleep(remaining)


class ChallengePresent(Exception):
    """Raised internally when a fetched page is still a Cloudflare
    challenge (or FlareSolverr couldn't clear it). Carries the escalated
    cooldown so the wait/stop strategies below don't have to recompute
    (and re-escalate) it themselves."""

    def __init__(self, cooldown: float):
        super().__init__("Cloudflare challenge page detected")
        self.cooldown = cooldown


def register_challenge() -> float:
    global _challenge_hits, _cooldown_until
    _challenge_hits += 1
    cooldown = min(15.0 * (2 ** (_challenge_hits - 1)), 120.0)
    _cooldown_until = time.monotonic() + cooldown
    return cooldown


class ChallengeCooldownWait(wait_base):
    def __call__(self, retry_state) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        return exc.cooldown if isinstance(exc, ChallengePresent) else 0.0


class BudgetExceeded(stop_base):
    def __init__(self, deadline: float | None):
        self.deadline = deadline

    def __call__(self, retry_state) -> bool:
        if self.deadline is None or retry_state.outcome is None:
            return False
        exc = retry_state.outcome.exception()
        cooldown = exc.cooldown if isinstance(exc, ChallengePresent) else 0.0
        return time.monotonic() + cooldown >= self.deadline
