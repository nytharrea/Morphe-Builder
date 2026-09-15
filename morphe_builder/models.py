from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Arch(StrEnum):
    ARM64_V8A = "arm64-v8a"
    ARMEABI_V7A = "armeabi-v7a"
    X86_64 = "x86_64"
    X86 = "x86"


class VersionPolicy(StrEnum):
    MAX_PATCHES_THEN_VERSION = "max_patches_then_version"
    LATEST_COMPATIBLE = "latest_compatible"
    PINNED = "pinned"


class SourceRef(BaseModel):
    owner: str
    repo: str
    asset_match: str | None = None
    sha256: str | None = None


class PatchSpec(BaseModel):
    sources: list[str] = Field(default_factory=list)
    enable: list[str] = Field(default_factory=list)
    disable: list[str] = Field(default_factory=list)
    options: dict[str, str] = Field(default_factory=dict)


class AppSpec(BaseModel):
    key: str
    pkg: str
    display_name: str
    source_app: str
    arch: Arch = Arch.ARM64_V8A
    enabled: bool = True
    version_policy: VersionPolicy = VersionPolicy.MAX_PATCHES_THEN_VERSION
    force_version: str | None = None
    force_build: str | None = None
    patch: PatchSpec = Field(default_factory=PatchSpec)
    icon: str | None = None
    release_tag: str | None = None


class BuildToolAsset(BaseModel):
    name: str
    tag: str = ""
    body: str = ""
    prerelease: bool = False
    sha256: str | None = None


class BuildResult(BaseModel):
    app_key: str
    app_name: str
    pkg: str
    display_name: str
    version: str
    name: str
    path: Path
    sha256: str | None = None
    patch_sources: list[str] = Field(default_factory=list)
    source_signatures: list[str] = Field(default_factory=list)


ReleaseStatus = Literal["success", "partial", "failed"]
