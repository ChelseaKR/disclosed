"""Session-wide fixtures for the question-answering layer's tests.

The evidence store is built once per session from the committed inputs (about a second) and the
corpus is loaded once; both are read-only, so sharing them is safe and keeps the suite fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosed.ask import corpus as corpus_module
from disclosed.ask import evidence as evidence_module

ROOT = Path(__file__).resolve().parent.parent


# The committed data/snapshots directory grows every day, and the daily-snapshot job runs this
# suite against the day's new snapshot before committing it. Assertions about what the series has
# or has not done ("the Scorecard series never moved") therefore cannot read the live directory:
# the day the series moves, the gate refuses the very snapshot that records the movement. They read
# this frozen copy instead: three Scorecard snapshots (2026-08-21, 2026-08-22, 2026-10-15), their
# provenance sidecars, and the three IPEDS years, all copied unchanged from data/snapshots.
FIXTURE_SNAPSHOTS = ROOT / "tests" / "fixtures" / "snapshots"


@pytest.fixture(scope="session")
def pinned_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The live data directory, except that ``snapshots`` is the frozen fixture."""
    data = tmp_path_factory.mktemp("pinned-data")
    for entry in (ROOT / "data").iterdir():
        if entry.name != "snapshots":
            (data / entry.name).symlink_to(entry)
    (data / "snapshots").symlink_to(FIXTURE_SNAPSHOTS)
    return data


@pytest.fixture(scope="session")
def evidence(pinned_data_dir: Path) -> evidence_module.Evidence:
    return evidence_module.build(pinned_data_dir)


@pytest.fixture(scope="session")
def corpus() -> corpus_module.Corpus:
    return corpus_module.load(ROOT / "corpus")
