"""Build planning: fingerprint, previous-state comparison, BUILD/SKIP decisions.

Used by scripts/prepare_release.py to decide which matrix jobs actually need
to run, and by scripts/finalize_release.py to assemble a full release snapshot
(new APKs + unchanged APKs copied from the previous release).
"""

from .models import BuildDecision, BuildPlan, BuildRecord, Manifest
from .planner import create_plan
from .state import load_previous_manifest, write_manifest

__all__ = [
    "BuildDecision",
    "BuildPlan",
    "BuildRecord",
    "Manifest",
    "create_plan",
    "load_previous_manifest",
    "write_manifest",
]
