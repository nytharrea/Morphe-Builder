import json

from morphe_builder.manifest import load_build_manifest, write_build_manifest


def test_write_and_load_manifest(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    apk = dist / "YouTube-21.36.45.apk"
    apk.write_bytes(b"apk")
    result = {
        "app_key": "youtube",
        "app_name": "youtube",
        "pkg": "com.google.android.youtube",
        "display_name": "YouTube",
        "version": "21.36.45",
        "name": apk.name,
        "patch_sources": ["morphe"],
        "source_signatures": ["abc"],
    }
    path = write_build_manifest(dist, [result], tools={"desktop": {"name": "desktop.jar"}})
    data = json.loads(path.read_text())
    assert data["schema"] == 1
    assert data["apps"][0]["name"] == apk.name
    loaded = load_build_manifest(dist)
    assert loaded["apps"][0]["sha256"]
