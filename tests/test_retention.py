from morphe_builder.retention import select_releases_to_delete


def release(rid, created_at):
    return {"id": rid, "created_at": created_at, "tag_name": f"build-{rid}"}


def test_retention_keeps_latest_n():
    releases = [
        release(1, "2026-09-14T10:00:00Z"),
        release(2, "2026-09-14T11:00:00Z"),
        release(3, "2026-09-14T12:00:00Z"),
    ]
    to_delete = select_releases_to_delete(releases, keep_release_id=3, keep_latest=2)
    assert [r["id"] for r in to_delete] == [1]


def test_retention_zero_keeps_only_current():
    releases = [release(1, "2026-09-14T10:00:00Z"), release(2, "2026-09-14T11:00:00Z")]
    to_delete = select_releases_to_delete(releases, keep_release_id=2, keep_latest=0)
    assert [r["id"] for r in to_delete] == [1]
