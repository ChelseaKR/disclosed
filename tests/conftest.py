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


@pytest.fixture(scope="session")
def evidence() -> evidence_module.Evidence:
    return evidence_module.build(ROOT / "data")


@pytest.fixture(scope="session")
def corpus() -> corpus_module.Corpus:
    return corpus_module.load(ROOT / "corpus")
