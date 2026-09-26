import re
import subprocess
import threading
from pathlib import Path

from .. import log
from ..settings import settings


def _redact(cmd: list[str], secrets: set[str]) -> list[str]:
    return ["***" if part in secrets else part for part in cmd]


def patch_apk(
    desktop: str,
    patches: list[str],
    apk: str,
    exclude: list[str] | None = None,
    enable: list[str] | None = None,
    options: dict[str, str | None] | None = None,
    arch: str = "arm64-v8a",
) -> str:
    log.patch(f"Patching APK & stripping unused architectures ({arch} only)...")

    ks_path = settings.ks_path
    ks_password = settings.ks_password.get_secret_value() if settings.ks_password else None
    ks_alias = settings.ks_alias
    key_password = settings.key_password.get_secret_value() if settings.key_password else None

    cmd = ["java", "-jar", desktop, "patch"]

    for p in patches:
        cmd += ["--patches", p]

    if arch:
        cmd += ["--striplibs", arch]

    if ks_path and ks_path.exists() and ks_password and ks_alias and key_password:
        log.lock("Custom keystore detected! Signing with your private key...")
        cmd += [
            "--keystore",
            str(ks_path),
            "--keystore-password",
            ks_password,
            "--keystore-entry-alias",
            ks_alias,
            "--keystore-entry-password",
            key_password,
        ]
    else:
        log.warn("Custom keystore credentials missing or file not found. Falling back to default Morphe testkey.")

    for p in exclude or []:
        cmd += ["--disable", p]

    for p in enable or []:
        cmd += ["--enable", p]

    options_by_patch: dict[str, list[tuple[str, str | None]]] = {}
    for dotted_key, value in (options or {}).items():
        patch_name, sep, option_key = dotted_key.partition(".")
        if not sep:
            raise ValueError(
                f'apk_source options key {dotted_key!r} must be "Patch name.optionKey" (no "." found).'
            )
        options_by_patch.setdefault(patch_name, []).append((option_key, value))

    for patch_name, patch_options in options_by_patch.items():
        # -O is associated with the -e immediately before it, not looked up
        # by patch name - so this patch needs its own -e here even if it's
        # already in `enable` above (harmless: enabling an already-enabled
        # patch again is a no-op), grouped with all of its own -O flags
        # together, matching revanced-cli's own documented convention:
        # -e "Patch name" -Okey1=value1 -Okey2=value2
        cmd += ["--enable", patch_name]
        for option_key, value in patch_options:
            cmd.append(f"-O{option_key}" if value is None else f"-O{option_key}={value}")

    cmd.append(apk)

    secret_values = {v for v in (ks_password, key_password) if v}
    log.step(f"Executing command: {' '.join(_redact(cmd, secret_values))}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("subprocess.Popen returned no stdout pipe even though stdout=PIPE was requested.")

    # A plain process.wait(timeout=...) wouldn't catch a hang here: the
    # blocking call is the line-by-line `for line in process.stdout`
    # iteration below, which would already be stuck before we ever reached
    # a wait(). This watchdog timer kills the process - which closes its
    # stdout, so the for-loop below sees EOF and returns - if it goes
    # completely silent for patch_timeout seconds; it resets on every line
    # received, so a slow but still-progressing patch run is never killed
    # for simply taking a while, only for going fully unresponsive.
    timed_out = threading.Event()

    def _kill_on_timeout() -> None:
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(settings.patch_timeout, _kill_on_timeout)
    watchdog.start()
    try:
        output_lines = []
        for line in process.stdout:
            watchdog.cancel()
            log.patch_line(line)
            output_lines.append(line)
            watchdog = threading.Timer(settings.patch_timeout, _kill_on_timeout)
            watchdog.start()

        process.wait()
    finally:
        watchdog.cancel()

    if timed_out.is_set():
        raise RuntimeError(
            f"Patch CLI timed out (no output for {settings.patch_timeout:.0f}s) and was killed - likely hung."
        )

    output = "".join(output_lines)

    if "Applying 0 patches" in output:
        raise RuntimeError("Applying 0 patches. No compatible patch found or version not supported.")

    if process.returncode != 0:
        raise RuntimeError(f"Patch failed (exit {process.returncode}):\n{output}")

    match = re.search(r"INFO:\s+Saved to\s+([^\r\n]+\.apk)", output, re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Cannot find patched APK path in output:\n{output}")

    patched_apk = match.group(1).strip()

    if not Path(patched_apk).exists():
        raise RuntimeError(f"Patched APK does not exist:\n{patched_apk}")

    log.success("Patch done")
    log.saved(f"Output: {patched_apk}")

    return patched_apk
