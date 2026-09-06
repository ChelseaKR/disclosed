"""Grade institutions on what they disclose, not on how they perform."""

from importlib import metadata as _metadata

from .disclosure import CLASSIFICATIONS, Disclosure, classify
from .grading import InstitutionGrade, grade_institution, summarize

# The public API, and the whole of it. Everything else in this package is internal and may be
# renamed without notice. `docs/CLASSIFIER.md` documents these six names and carries a surface
# revision that `CHANGELOG.md` must name; `tests/test_classifier_library.py` holds the three
# together so the surface cannot move without somebody saying so in the changelog.
#
# `CLASSIFICATIONS` is here because a consumer reading a classified file meets those five words
# and is entitled to check against them rather than hardcoding a sixth guess about what they are.
__all__ = [
    "CLASSIFICATIONS",
    "Disclosure",
    "InstitutionGrade",
    "classify",
    "grade_institution",
    "summarize",
]
# One source of the version. `pyproject.toml` declares it, the build records it in the
# installed distribution's metadata, and this reads it back rather than writing the number
# down a second time where it can drift. Held by tests/test_version_reality.py, which also
# holds it against the tags this repository has -- none, by ADR 0001.
try:
    __version__ = _metadata.version("disclosed")
except _metadata.PackageNotFoundError:  # pragma: no cover - uninstalled source tree
    __version__ = "0.0.0+unknown"
