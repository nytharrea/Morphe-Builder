import json
import zipfile
from pathlib import Path

from morphe_builder.apk import verify


def _write_json(path, data):
    path.write_text(json.dumps(data))


def _setup_paths(monkeypatch, tmp_path, known=None, pending=None):
    known_path = tmp_path / "known.json"
    pending_path = tmp_path / "pending.json"
    _write_json(known_path, known or {})
    _write_json(pending_path, pending or {})
    monkeypatch.setattr(verify.settings, "known_signatures_path", known_path)
    monkeypatch.setattr(verify.settings, "pending_signatures_path", pending_path)
    monkeypatch.setattr(verify.settings, "skip_signature_verify", False)
    return known_path, pending_path


def test_skip_signature_verify_bypasses_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(verify.settings, "skip_signature_verify", True)
    monkeypatch.setattr(
        verify, "get_apk_certificate_fingerprints", lambda p: (_ for _ in ()).throw(AssertionError)
    )
    warnings = []
    monkeypatch.setattr(verify.log, "warn", warnings.append)

    verify.verify_apk_signature("reddit.apk", "reddit")

    assert any("SKIP_SIGNATURE_VERIFY" in w for w in warnings)


def test_no_pinned_entry_records_pending_and_raises(monkeypatch, tmp_path):
    known_path, pending_path = _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(verify, "get_apk_certificate_fingerprints", lambda p: ["abc123fingerprint"])
    monkeypatch.setattr(verify, "_resolve_verifiable_apk", lambda p: (p, None))

    try:
        verify.verify_apk_signature("reddit.apk", "reddit")
        raise AssertionError("expected an Exception for the missing pin")
    except AssertionError:
        raise
    except Exception as e:
        assert "No pinned signature" in str(e)
        assert "has been recorded" in str(e)

    pending = json.loads(pending_path.read_text())
    assert pending["reddit"] == "abc123fingerprint"
    # known_signatures.json must be left completely untouched
    assert json.loads(known_path.read_text()) == {}


def test_repeated_pending_fingerprint_says_was_already_recorded(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path, pending={"reddit": "abc123fingerprint"})
    monkeypatch.setattr(verify, "get_apk_certificate_fingerprints", lambda p: ["abc123fingerprint"])
    monkeypatch.setattr(verify, "_resolve_verifiable_apk", lambda p: (p, None))

    try:
        verify.verify_apk_signature("reddit.apk", "reddit")
        raise AssertionError("expected an Exception")
    except AssertionError:
        raise
    except Exception as e:
        assert "was already" in str(e)


def test_matching_pinned_signature_passes(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path, known={"reddit": "abc123fingerprint"})
    monkeypatch.setattr(verify, "get_apk_certificate_fingerprints", lambda p: ["abc123fingerprint"])
    monkeypatch.setattr(verify, "_resolve_verifiable_apk", lambda p: (p, None))

    successes = []
    monkeypatch.setattr(verify.log, "success", successes.append)

    verify.verify_apk_signature("reddit.apk", "reddit")  # must not raise
    assert len(successes) == 1


def test_mismatched_signature_raises(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path, known={"reddit": "expected-fingerprint"})
    monkeypatch.setattr(verify, "get_apk_certificate_fingerprints", lambda p: ["different-fingerprint"])
    monkeypatch.setattr(verify, "_resolve_verifiable_apk", lambda p: (p, None))

    try:
        verify.verify_apk_signature("reddit.apk", "reddit")
        raise AssertionError("expected a mismatch Exception")
    except AssertionError:
        raise
    except Exception as e:
        assert "MISMATCH" in str(e)


def test_pinned_signature_can_match_any_of_multiple_certificates(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path, known={"reddit": "second-fingerprint"})
    monkeypatch.setattr(
        verify, "get_apk_certificate_fingerprints", lambda p: ["first-fingerprint", "second-fingerprint"]
    )
    monkeypatch.setattr(verify, "_resolve_verifiable_apk", lambda p: (p, None))
    monkeypatch.setattr(verify.log, "success", lambda msg: None)

    verify.verify_apk_signature("reddit.apk", "reddit")  # must not raise


def test_resolve_verifiable_apk_plain_apk_file(tmp_path):
    apk = tmp_path / "youtube.apk"
    apk.write_bytes(b"not actually a zip")
    path, temp_dir = verify._resolve_verifiable_apk(str(apk))
    assert path == str(apk)
    assert temp_dir is None


def test_resolve_verifiable_apk_zip_with_manifest_is_used_directly(tmp_path):
    apk = tmp_path / "youtube.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("AndroidManifest.xml", "x")
    path, temp_dir = verify._resolve_verifiable_apk(str(apk))
    assert path == str(apk)
    assert temp_dir is None


def test_resolve_verifiable_apk_bundle_extracts_base_apk(tmp_path):
    bundle = tmp_path / "reddit.apkm"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("info.json", "{}")
        zf.writestr("base.apk", "base-apk-content")
        zf.writestr("split_config.arm64_v8a.apk", "split-content")

    path, temp_dir = verify._resolve_verifiable_apk(str(bundle))
    try:
        assert path.endswith("base.apk")
        assert Path(path).exists()
        assert temp_dir is not None
    finally:
        if temp_dir:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


def test_resolve_verifiable_apk_bundle_without_base_apk_raises(tmp_path):
    bundle = tmp_path / "weird.apkm"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("readme.txt", "nothing useful here")

    try:
        verify._resolve_verifiable_apk(str(bundle))
        raise AssertionError("expected an Exception")
    except AssertionError:
        raise
    except Exception as e:
        assert "No verifiable .apk" in str(e)


def test_cert_der_bytes_accepts_raw_bytes():
    assert verify._cert_der_bytes(b"der-bytes-here") == b"der-bytes-here"


def test_cert_der_bytes_accepts_public_bytes_style_object():
    class FakeCryptographyCert:
        def public_bytes(self, encoding):
            return b"from-public-bytes"

    assert verify._cert_der_bytes(FakeCryptographyCert()) == b"from-public-bytes"


def test_cert_der_bytes_accepts_dump_style_object():
    class FakeAsn1Cert:
        def dump(self):
            return b"from-dump"

    assert verify._cert_der_bytes(FakeAsn1Cert()) == b"from-dump"


def test_cert_der_bytes_raises_on_unrecognized_object():
    try:
        verify._cert_der_bytes(object())
        raise AssertionError("expected a TypeError")
    except AssertionError:
        raise
    except TypeError:
        pass
