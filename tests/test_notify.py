from pydantic import SecretStr

from morphe_builder import notify


class _SpyApprise:
    """Stands in for apprise.Apprise - records what got added and what
    notify() asked it to send, without needing a real apprise install or
    real network targets to validate against."""

    instances = []

    def __init__(self):
        self.added: list[str] = []
        self.notified_with: list[str] = []
        self.notify_result = True
        _SpyApprise.instances.append(self)

    def add(self, url: str) -> bool:
        self.added.append(url)
        return True

    def __len__(self) -> int:
        return len(self.added)

    async def async_notify(self, body: str = "", **kwargs) -> bool:
        self.notified_with.append(body)
        return self.notify_result


def _reset_settings(monkeypatch):
    monkeypatch.setattr(notify.settings, "discord_webhook_url", SecretStr(""))
    monkeypatch.setattr(notify.settings, "telegram_bot_token", SecretStr(""))
    monkeypatch.setattr(notify.settings, "telegram_chat_id", "")
    monkeypatch.setattr(notify.settings, "apprise_urls", SecretStr(""))
    _SpyApprise.instances = []
    monkeypatch.setattr(notify.apprise, "Apprise", _SpyApprise)


# ---- _truncate --------------------------------------------------------------


def test_truncate_leaves_short_text_unchanged():
    assert notify._truncate("short message", 2000) == "short message"


def test_truncate_shortens_long_text_and_marks_it():
    text = "x" * 3000
    result = notify._truncate(text, 100)
    assert len(result) <= 100
    assert result.endswith("... (truncated)")


# ---- _build_apprise: which targets get added from settings -----------------


def test_build_apprise_empty_when_nothing_configured(monkeypatch):
    _reset_settings(monkeypatch)
    apobj = notify._build_apprise()
    assert len(apobj) == 0


def test_build_apprise_adds_discord_url_when_configured(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(
        notify.settings, "discord_webhook_url", SecretStr("https://discord.com/api/webhooks/1/tok")
    )
    apobj = notify._build_apprise()
    assert apobj.added == ["https://discord.com/api/webhooks/1/tok"]


def test_build_apprise_adds_telegram_only_when_both_token_and_chat_id_present(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(notify.settings, "telegram_bot_token", SecretStr("123:ABC"))
    monkeypatch.setattr(notify.settings, "telegram_chat_id", "")  # chat id missing
    apobj = notify._build_apprise()
    assert apobj.added == []  # not added - both are required together

    monkeypatch.setattr(notify.settings, "telegram_chat_id", "987654321")
    apobj = notify._build_apprise()
    assert apobj.added == ["tgram://123:ABC/987654321"]


def test_build_apprise_splits_multiple_apprise_urls_on_whitespace_and_commas(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(
        notify.settings, "apprise_urls", SecretStr("ntfy://topic1, slack://token@channel\nmailto://user@host")
    )
    apobj = notify._build_apprise()
    assert apobj.added == ["ntfy://topic1", "slack://token@channel", "mailto://user@host"]


def test_build_apprise_combines_all_configured_targets(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(
        notify.settings, "discord_webhook_url", SecretStr("https://discord.com/api/webhooks/1/tok")
    )
    monkeypatch.setattr(notify.settings, "telegram_bot_token", SecretStr("123:ABC"))
    monkeypatch.setattr(notify.settings, "telegram_chat_id", "999")
    monkeypatch.setattr(notify.settings, "apprise_urls", SecretStr("ntfy://topic1"))
    apobj = notify._build_apprise()
    assert len(apobj) == 3


# ---- notify(): behavior around sending --------------------------------------


async def test_notify_does_nothing_when_no_targets_configured(monkeypatch):
    _reset_settings(monkeypatch)
    await notify.notify("hello")
    assert _SpyApprise.instances[0].notified_with == []


async def test_notify_sends_truncated_body_when_targets_configured(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(
        notify.settings, "discord_webhook_url", SecretStr("https://discord.com/api/webhooks/1/tok")
    )
    await notify.notify("hello world")
    assert _SpyApprise.instances[0].notified_with == ["hello world"]


async def test_notify_warns_when_apprise_reports_failure(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(
        notify.settings, "discord_webhook_url", SecretStr("https://discord.com/api/webhooks/1/tok")
    )

    class _FailingApprise(_SpyApprise):
        async def async_notify(self, body="", **kwargs):
            self.notified_with.append(body)
            return False

    monkeypatch.setattr(notify.apprise, "Apprise", _FailingApprise)
    warnings = []
    monkeypatch.setattr(notify.log, "warn", warnings.append)

    await notify.notify("hello")
    assert any("failed" in w.lower() for w in warnings)


async def test_notify_catches_and_warns_on_exception_instead_of_raising(monkeypatch):
    _reset_settings(monkeypatch)
    monkeypatch.setattr(
        notify.settings, "discord_webhook_url", SecretStr("https://discord.com/api/webhooks/1/tok")
    )

    class _ExplodingApprise(_SpyApprise):
        async def async_notify(self, body="", **kwargs):
            raise RuntimeError("network exploded")

    monkeypatch.setattr(notify.apprise, "Apprise", _ExplodingApprise)
    warnings = []
    monkeypatch.setattr(notify.log, "warn", warnings.append)

    await notify.notify("hello")  # must not raise
    assert any("network exploded" in w for w in warnings)


# ---- format_summary / format_all_failed -------------------------------------


def test_format_summary_lists_matched_apps_and_release_url():
    matched = [
        {"display_name": "YouTube", "version": "19.35.36"},
        {"display_name": "Reddit", "version": "2024.15.0"},
    ]
    text = notify.format_summary("Build #42", "https://github.com/o/r/releases/tag/build-42", matched, [])
    assert "Build #42" in text
    assert "2 app(s) patched" in text
    assert "YouTube — 19.35.36" in text
    assert "Reddit — 2024.15.0" in text
    assert "https://github.com/o/r/releases/tag/build-42" in text
    assert "failed" not in text.lower()


def test_format_summary_includes_failed_apps_section_when_present():
    text = notify.format_summary("Build #42", "https://example.com", [], ["gboard", "brave"])
    assert "2 app(s) failed" in text
    assert "gboard" in text
    assert "brave" in text


def test_format_all_failed_lists_every_failed_key():
    text = notify.format_all_failed("Build #42", ["youtube", "reddit"])
    assert "no apps were patched successfully" in text
    assert "youtube" in text
    assert "reddit" in text


def test_format_all_failed_handles_empty_failed_list():
    text = notify.format_all_failed("Build #42", [])
    assert "no apps were patched successfully" in text
    assert "Check the Actions run log" in text
