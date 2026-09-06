"""Grade institutions on what they disclose, not on how they perform."""

from importlib import metadata as _metadata

from .disclosure import Disclosure, classify
from .grading import InstitutionGrade, grade_institution, summarize

__all__ = ["Disclosure", "InstitutionGrade", "classify", "grade_institution", "summarize"]
# One source of the version. `pyproject.toml` declares it, the build records it in the
# installed distribution's metadata, and this reads it back rather than writing the number
# down a second time where it can drift. Held by tests/test_version_reality.py, which also
# holds it against the tags this repository has -- none, by ADR 0001.
try:
    __version__ = _metadata.version("disclosed")
except _metadata.PackageNotFoundError:  # pragma: no cover - uninstalled source tree
    __version__ = "0.0.0+unknown"
