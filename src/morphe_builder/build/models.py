"""Typed structures for the build planner and release manifest."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class BuildRecord:
    """One build's recorded state inside a release's build-manifest.json."""

    build_key: str
    app_version: str
    patches: dict[str, str]  # patch_source key -> release tag (e.g. "v1.5")
    config_hash: str
    apk_name: str
    display_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildRecord:
        return cls(
            build_key=str(data["build_key"]),
            app_version=str(data["app_version"]),
            patches={str(k): str(v) for k, v in (data.get("patches") or {}).items()},
            config_hash=str(data.get("config_hash") or ""),
            apk_name=str(data["apk_name"]),
            display_name=str(data.get("display_name") or data["build_key"]),
        )


@dataclass
class Manifest:
    """Full snapshot stored as build-manifest.json on each GitHub Release."""

    schema_version: int = 1
    release_tag: str = ""
    builds: dict[str, BuildRecord] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "release_tag": self.release_tag,
            "builds": {k: v.to_dict() for k, v in self.builds.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        builds_raw = data.get("builds") or {}
        builds = {str(k): BuildRecord.from_dict(v) for k, v in builds_raw.items()}
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            release_tag=str(data.get("release_tag") or ""),
            builds=builds,
        )


DecisionKind = Literal["build", "skip"]


@dataclass(frozen=True)
class BuildDecision:
    build_key: str
    decision: DecisionKind
    reason: str
    app_version: str
    previous_app_version: str | None
    patches: dict[str, str]
    previous_patches: dict[str, str] | None
    config_hash: str
    previous_config_hash: str | None
    previous_apk_name: str | None
    display_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildDecision:
        return cls(
            build_key=str(data["build_key"]),
            decision=data["decision"],
            reason=str(data.get("reason") or ""),
            app_version=str(data["app_version"]),
            previous_app_version=data.get("previous_app_version"),
            patches={str(k): str(v) for k, v in (data.get("patches") or {}).items()},
            previous_patches=(
                {str(k): str(v) for k, v in data["previous_patches"].items()}
                if data.get("previous_patches") is not None
                else None
            ),
            config_hash=str(data.get("config_hash") or ""),
            previous_config_hash=data.get("previous_config_hash"),
            previous_apk_name=data.get("previous_apk_name"),
            display_name=str(data.get("display_name") or data["build_key"]),
        )


@dataclass
class BuildPlan:
    """Output of the planner: what to build this run and what to carry over."""

    release_tag: str
    release_name: str
    decisions: list[BuildDecision] = field(default_factory=list)
    previous_release_tag: str | None = None
    previous_release_id: int | None = None
    force_rebuild: bool = False

    @property
    def to_build(self) -> list[str]:
        return [d.build_key for d in self.decisions if d.decision == "build"]

    @property
    def to_skip(self) -> list[str]:
        return [d.build_key for d in self.decisions if d.decision == "skip"]

    def decision_for(self, build_key: str) -> BuildDecision | None:
        for d in self.decisions:
            if d.build_key == build_key:
                return d
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_tag": self.release_tag,
            "release_name": self.release_name,
            "previous_release_tag": self.previous_release_tag,
            "previous_release_id": self.previous_release_id,
            "force_rebuild": self.force_rebuild,
            "decisions": [d.to_dict() for d in self.decisions],
            "to_build": self.to_build,
            "to_skip": self.to_skip,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildPlan:
        decisions = [BuildDecision.from_dict(d) for d in (data.get("decisions") or [])]
        return cls(
            release_tag=str(data["release_tag"]),
            release_name=str(data["release_name"]),
            decisions=decisions,
            previous_release_tag=data.get("previous_release_tag"),
            previous_release_id=data.get("previous_release_id"),
            force_rebuild=bool(data.get("force_rebuild")),
        )
