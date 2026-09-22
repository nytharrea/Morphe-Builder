from pathlib import Path

from pydantic import SecretStr

from morphe_builder.apk import patcher


def test_redact_replaces_only_listed_secrets():
    cmd = ["java", "-jar", "x.jar", "--keystore-password", "hunter2", "--other", "value"]
    result = patcher._redact(cmd, {"hunter2"})
    assert result == ["java", "-jar", "x.jar", "--keystore-password", "***", "--other", "value"]
    # original is untouched
    assert cmd[4] == "hunter2"


def test_redact_leaves_command_unchanged_when_no_secrets_given():
    cmd = ["java", "-jar", "x.jar"]
    assert patcher._redact(cmd, set()) == cmd


class _FakeProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self):
        pass


def _patch_common(monkeypatch, tmp_path, output_lines, returncode=0):
    """Stub out java entirely - patch_apk() only ever sees a fake process
    whose captured stdout lines and exit code we control, plus a real file
    on disk at the path the fake output claims to have saved to."""
    apk_path = tmp_path / "Youtube-patched.apk"
    apk_path.write_bytes(b"fake patched apk")

    lines = [line.format(apk_path=apk_path) + "\n" for line in output_lines]
    captured_cmd = {}

    def fake_popen(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return _FakeProcess(lines, returncode=returncode)

    monkeypatch.setattr(patcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(patcher.log, "patch_line", lambda line: None)
    return apk_path, captured_cmd


def test_falls_back_to_default_testkey_when_no_keystore_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.settings, "ks_password", None)
    monkeypatch.setattr(patcher.settings, "ks_alias", None)
    monkeypatch.setattr(patcher.settings, "key_password", None)

    warnings = []
    monkeypatch.setattr(patcher.log, "warn", warnings.append)

    apk_path, captured = _patch_common(monkeypatch, tmp_path, ["INFO: Saved to {apk_path}"])
    patcher.patch_apk("desktop.jar", ["patch1"], "input.apk")

    assert "--keystore" not in captured["cmd"]
    assert any("testkey" in w.lower() or "default" in w.lower() for w in warnings)


def test_uses_custom_keystore_when_fully_configured(monkeypatch, tmp_path):
    keystore = tmp_path / "release.keystore"
    keystore.write_bytes(b"fake keystore")

    monkeypatch.setattr(patcher.settings, "ks_path", keystore)
    monkeypatch.setattr(patcher.settings, "ks_password", SecretStr("storepass123"))
    monkeypatch.setattr(patcher.settings, "ks_alias", "release-key")
    monkeypatch.setattr(patcher.settings, "key_password", SecretStr("keypass456"))

    monkeypatch.setattr(patcher.log, "lock", lambda msg: None)

    apk_path, captured = _patch_common(monkeypatch, tmp_path, ["INFO: Saved to {apk_path}"])
    patcher.patch_apk("desktop.jar", ["patch1"], "input.apk")

    cmd = captured["cmd"]
    assert "--keystore" in cmd
    assert str(keystore) in cmd
    assert "storepass123" in cmd  # the real subprocess argv - never logged, see the redaction test below
    assert "--keystore-entry-alias" in cmd
    assert "release-key" in cmd


def test_keystore_missing_file_falls_back_even_if_credentials_are_set(monkeypatch, tmp_path):
    # ks_path points at a file that doesn't exist on disk - same as a
    # misconfigured or not-yet-decoded keystore secret.
    monkeypatch.setattr(patcher.settings, "ks_path", tmp_path / "does-not-exist.keystore")
    monkeypatch.setattr(patcher.settings, "ks_password", SecretStr("storepass123"))
    monkeypatch.setattr(patcher.settings, "ks_alias", "release-key")
    monkeypatch.setattr(patcher.settings, "key_password", SecretStr("keypass456"))
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)

    apk_path, captured = _patch_common(monkeypatch, tmp_path, ["INFO: Saved to {apk_path}"])
    patcher.patch_apk("desktop.jar", ["patch1"], "input.apk")

    assert "--keystore" not in captured["cmd"]


def test_secret_values_never_appear_in_the_logged_command_line(monkeypatch, tmp_path):
    keystore = tmp_path / "release.keystore"
    keystore.write_bytes(b"fake keystore")

    monkeypatch.setattr(patcher.settings, "ks_path", keystore)
    monkeypatch.setattr(patcher.settings, "ks_password", SecretStr("storepass123"))
    monkeypatch.setattr(patcher.settings, "ks_alias", "release-key")
    monkeypatch.setattr(patcher.settings, "key_password", SecretStr("keypass456"))
    monkeypatch.setattr(patcher.log, "lock", lambda msg: None)

    logged_steps = []
    monkeypatch.setattr(patcher.log, "step", logged_steps.append)

    apk_path, captured = _patch_common(monkeypatch, tmp_path, ["INFO: Saved to {apk_path}"])
    patcher.patch_apk("desktop.jar", ["patch1"], "input.apk")

    # The real subprocess call must still get the real passwords (verified above) -
    # it's specifically the human-readable log line that must never contain them.
    assert len(logged_steps) == 1
    assert "storepass123" not in logged_steps[0]
    assert "keypass456" not in logged_steps[0]
    assert "***" in logged_steps[0]


def test_command_includes_patches_arch_exclude_and_enable_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)

    apk_path, captured = _patch_common(monkeypatch, tmp_path, ["INFO: Saved to {apk_path}"])
    patcher.patch_apk(
        "desktop.jar",
        ["patch-a.mpp", "patch-b.mpp"],
        "input.apk",
        exclude=["Bad patch"],
        enable=["Extra patch"],
        arch="arm64-v8a",
    )

    cmd = captured["cmd"]
    assert cmd[:4] == ["java", "-jar", "desktop.jar", "patch"]
    assert cmd.count("--patches") == 2
    assert "patch-a.mpp" in cmd and "patch-b.mpp" in cmd
    assert "--striplibs" in cmd and "arm64-v8a" in cmd
    assert "--disable" in cmd and "Bad patch" in cmd
    assert "--enable" in cmd and "Extra patch" in cmd
    assert cmd[-1] == "input.apk"


def test_raises_when_zero_patches_applied(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)
    _patch_common(monkeypatch, tmp_path, ["Applying 0 patches..."])

    try:
        patcher.patch_apk("desktop.jar", [], "input.apk")
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "0 patches" in str(e)


def test_raises_on_nonzero_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)
    _patch_common(monkeypatch, tmp_path, ["some error output"], returncode=1)

    try:
        patcher.patch_apk("desktop.jar", ["p"], "input.apk")
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "exit 1" in str(e)


def test_raises_when_saved_path_not_found_in_output(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)
    _patch_common(monkeypatch, tmp_path, ["patching finished, no path line here"])

    try:
        patcher.patch_apk("desktop.jar", ["p"], "input.apk")
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "Cannot find patched APK path" in str(e)


def test_raises_when_reported_output_file_does_not_actually_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)

    missing_path = tmp_path / "never-written.apk"
    monkeypatch.setattr(
        patcher.subprocess, "Popen", lambda cmd, **kw: _FakeProcess([f"INFO: Saved to {missing_path}\n"])
    )
    monkeypatch.setattr(patcher.log, "patch_line", lambda line: None)

    try:
        patcher.patch_apk("desktop.jar", ["p"], "input.apk")
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "does not exist" in str(e)


def test_returns_the_patched_apk_path_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(patcher.settings, "ks_path", None)
    monkeypatch.setattr(patcher.log, "warn", lambda msg: None)
    apk_path, _ = _patch_common(monkeypatch, tmp_path, ["INFO: Saved to {apk_path}"])

    result = patcher.patch_apk("desktop.jar", ["p"], "input.apk")
    assert result == str(apk_path)
    assert Path(result).exists()
