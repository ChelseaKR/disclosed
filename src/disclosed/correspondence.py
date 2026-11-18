"""Which two federal columns are asking the same question, decided in public and cited.

:mod:`disclosed.crosswalk` compares two federal sources and reports where they disagree, and its
strength is that no threshold of this project's is involved: there is nothing to argue with except
the records themselves. That strength survives only as long as the *correspondence* is not a
judgement made inside a tuple. Deciding that ``CONTROL`` and ``school.ownership`` are the same
question is a claim, and until this module existed it was a claim nobody could check, made by a
line of code with a comment beside it.

So the correspondence itself is a cited artifact. Every candidate pair carries each side's own
published definition, quoted verbatim from the committed hash-pinned corpus, with the locator its
publisher uses; a verdict of :data:`COMPARABLE` or :data:`NOT_COMPARABLE`; and a reason written to
be disagreed with.

**A ``not_comparable`` pair is a finding, not a gap.** It is a published statement that two
collections from the same department are not asking the same question about the same institution,
and before this register such pairs were invisible: they were simply absent from the compared set,
which reads on a page exactly like a pair nobody thought of. Four of the six entries below are
``not_comparable`` and each one says why.

**The conservative answer to the open question.** A pair with no citable definition on one side is
``not_comparable``, full stop. That excludes pairs a reader would say obviously mean the same
thing, and the cost is named here rather than traded away quietly: the alternative is a
maintainer's judgement recorded in the citation slot, which is a weaker kind of claim wearing the
same word. Nothing in this register is cited to this project.

:data:`REGISTER_VERSION` exists so that widening the comparison and the data moving are
distinguishable on the page. Without it, adding a pair and an institution changing its answer look
identical in a diff of the counts, and the first would be read as the second.

The definitions are read from ``corpus/passages.json`` and ``corpus/manifest.json``, which are
plain JSON and committed. This module deliberately does not import :mod:`disclosed.ask.corpus`:
that module needs a defensive XML parser to *extract* passages from the publishers' workbooks, and
the grading path has no dependencies and is not about to acquire one in order to read a citation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

__all__ = [
    "COMPARABLE",
    "NOT_COMPARABLE",
    "REGISTER",
    "REGISTER_VERSION",
    "VERDICTS",
    "Correspondence",
    "Side",
    "comparable_entries",
    "load_definitions",
    "register_payload",
]

REGISTER_VERSION: Final[str] = "1"
"""Bumped whenever an entry is added, removed, or has its verdict changed.

Not a version of the data and not a version of the rules: a version of *what is being compared*.
The page states it, so a reader meeting a bigger disagreement count can tell whether more
institutions disagreed or more questions were asked.
"""

COMPARABLE: Final[str] = "comparable"
"""Both sides publish a definition, and the definitions are of the same question."""

NOT_COMPARABLE: Final[str] = "not_comparable"
"""The two sides are not asking the same question, or one of them does not ask it at all."""

VERDICTS: Final[frozenset[str]] = frozenset({COMPARABLE, NOT_COMPARABLE})


@dataclass(frozen=True, slots=True)
class Side:
    """One publisher's half of a candidate pair."""

    publisher: str
    key: str
    """The column, named as this project addresses it, or empty when the publisher has none.

    Empty is a real answer and is not the same as a column that exists and means something else.
    ``INSTSIZE`` against ``student.size`` is two questions; a level against nothing is one
    question asked once.

    A key here does not imply an adapter reads the column. A ``not_comparable`` pair is never
    compared, so carrying its column through the pipeline would be dead weight; the key is there
    so a reader can go to the publisher's file and find the thing the verdict is about.
    """

    passage_id: str
    """The committed corpus passage carrying this side's own definition, or empty when the
    publisher has no column here to define."""


@dataclass(frozen=True, slots=True)
class Correspondence:
    """One candidate pair, its two definitions, and the verdict on whether they correspond."""

    label: str
    scorecard: Side
    ipeds: Side
    verdict: str
    reason: str
    """Why the verdict is what it is. Written for the reader who thinks it is wrong."""

    note: str
    """For a comparable pair, why a disagreement here is worth a reader's attention. Empty for a
    pair that is not compared, because there is no disagreement to characterise."""


REGISTER: Final[tuple[Correspondence, ...]] = (
    Correspondence(
        label="Sector",
        scorecard=Side(
            publisher="College Scorecard",
            key="school.ownership",
            passage_id="scorecard-data-dictionary:CONTROL",
        ),
        ipeds=Side(
            publisher="IPEDS",
            key="ipeds.CONTROL",
            passage_id="ipeds-hd2023-dictionary:CONTROL",
        ),
        verdict=COMPARABLE,
        reason=(
            "Both sides define the column as control of the institution and publish the same "
            "three codes with the same meanings: 1 public, 2 private nonprofit, 3 private "
            "for-profit. The Scorecard's own dictionary names IPEDS as its source for this "
            "column, which makes a disagreement here a disagreement between a collection and "
            "the collection it says it came from."
        ),
        note=(
            "Sector decides which rules an institution answers to and which peer group it is "
            "compared against, here and in most other analyses of federal education data. Two "
            "arms of the Department of Education classifying the same school differently is a "
            "fact about the data, not a rounding difference."
        ),
    ),
    Correspondence(
        label="State",
        scorecard=Side(
            publisher="College Scorecard",
            key="school.state",
            passage_id="scorecard-data-dictionary:STABBR",
        ),
        ipeds=Side(
            publisher="IPEDS",
            key="ipeds.STABBR",
            passage_id="ipeds-hd2023-dictionary:STABBR",
        ),
        verdict=COMPARABLE,
        reason=(
            "Both sides define the column as the US Postal Service state abbreviation. The "
            "vocabulary is the Postal Service's and belongs to neither publisher, which is the "
            "cleanest case a correspondence can have."
        ),
        note=(
            "State is the coarsest grouping either source publishes and is used to build peer "
            "groups. A multi-campus institution can reasonably be filed under different states by "
            "different collections, so this is reported as a discrepancy to check rather than as "
            "an error by either source."
        ),
    ),
    Correspondence(
        label="Degree awarded",
        scorecard=Side(
            publisher="College Scorecard",
            key="school.degrees_awarded.predominant",
            passage_id="scorecard-data-dictionary:PREDDEG",
        ),
        ipeds=Side(
            publisher="IPEDS",
            key="ipeds.HDEGOFR1",
            passage_id="ipeds-hd2023-dictionary:HDEGOFR1",
        ),
        verdict=NOT_COMPARABLE,
        reason=(
            "The nearest columns the two collections publish are answers to different questions. "
            "The Scorecard's is the degree an institution predominantly *awards*, derived from "
            "what it graduated; IPEDS's is the highest degree it *offers*, derived from what it "
            "teaches. A college that offers a doctorate and predominantly awards bachelor's "
            "degrees answers both correctly and differently, so every such institution would be "
            "published as a disagreement. The codes also enumerate different things, which is "
            "the visible half of the same problem."
        ),
        note="",
    ),
    Correspondence(
        label="Undergraduate enrollment",
        scorecard=Side(
            publisher="College Scorecard",
            key="latest.student.size",
            passage_id="scorecard-data-dictionary:UGDS",
        ),
        ipeds=Side(
            publisher="IPEDS",
            key="ipeds.INSTSIZE",
            passage_id="ipeds-hd2023-dictionary:INSTSIZE",
        ),
        verdict=NOT_COMPARABLE,
        reason=(
            "A count against a band, over different populations and different terms. The "
            "Scorecard publishes a headcount of undergraduate certificate- and degree-seeking "
            "students; IPEDS publishes a size category based on total students enrolled for "
            "credit in a stated fall. Making them agree would need a rule of this project's "
            "about which counts fall in which band, and a numeric pair that needs a tolerance of "
            "ours to agree is evidence that it is not comparable rather than an argument for the "
            "tolerance."
        ),
        note="",
    ),
    Correspondence(
        label="Institution name",
        scorecard=Side(
            publisher="College Scorecard",
            key="school.name",
            passage_id="scorecard-data-dictionary:INSTNM",
        ),
        ipeds=Side(
            publisher="IPEDS",
            key="ipeds.INSTNM",
            passage_id="ipeds-hd2023-dictionary:INSTNM",
        ),
        verdict=NOT_COMPARABLE,
        reason=(
            "Both definitions say the column is the institution's name and neither publishes a "
            "controlled vocabulary for it. The two collections punctuate, abbreviate and order "
            "campus qualifiers differently, so a comparison would produce hundreds of findings "
            "that are all about typography. This is the oldest exclusion in the crosswalk and it "
            "is recorded here as a verdict rather than left as a sentence in a docstring."
        ),
        note="",
    ),
    Correspondence(
        label="Institutional level",
        scorecard=Side(publisher="College Scorecard", key="", passage_id=""),
        ipeds=Side(
            publisher="IPEDS",
            key="ipeds.ICLEVEL",
            passage_id="ipeds-hd2023-dictionary:ICLEVEL",
        ),
        verdict=NOT_COMPARABLE,
        reason=(
            "IPEDS classifies every institution as four-year, two-year or less-than-two-year, "
            "and the College Scorecard capture this project holds publishes no counterpart at "
            "all. One side asking a question the other does not ask is the most common shape a "
            "non-correspondence takes, and it is the one most likely to be mistaken for "
            "agreement, because a column nobody compares leaves no trace on a page."
        ),
        note="",
    ),
)


def comparable_entries() -> tuple[Correspondence, ...]:
    """The pairs that may be compared, in register order."""
    return tuple(entry for entry in REGISTER if entry.verdict == COMPARABLE)


def load_definitions(corpus_dir: Path) -> dict[str, dict[str, str]]:
    """Every cited definition, with the provenance of the document it was quoted from.

    Keyed by passage id. A passage the corpus does not carry is absent from the result rather
    than filled in with a placeholder: a citation that does not resolve must be visible as a
    citation that does not resolve, and ``tests/test_correspondence.py`` is where it becomes a
    failure instead of a blank on a page.
    """
    passages = json.loads((corpus_dir / "passages.json").read_text(encoding="utf-8"))
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    documents = {str(entry["id"]): entry for entry in manifest.get("documents", [])}
    wanted = {side.passage_id for entry in REGISTER for side in (entry.scorecard, entry.ipeds)}
    found: dict[str, dict[str, str]] = {}
    for passage in passages:
        passage_id = str(passage.get("id", ""))
        if passage_id not in wanted:
            continue
        document = documents.get(str(passage.get("document", "")), {})
        found[passage_id] = {
            "definition": str(passage.get("text", "")),
            "locator": str(passage.get("locator", "")),
            "document": str(passage.get("document", "")),
            "publisher": str(document.get("publisher", "")),
            "title": str(document.get("title", "")),
            "url": str(document.get("url", "")),
            "retrieved": str(document.get("retrieved", "")),
            "sha256": str(document.get("sha256", "")),
        }
    return found


def _side_payload(side: Side, definitions: Mapping[str, dict[str, str]]) -> dict[str, Any]:
    """One side of a pair, with its definition or an explicit statement that it has none.

    ``None`` and never an empty string, for the reason every unmeasured rate in this project is
    ``None``: a publisher that has no column here has not published an empty definition, and a
    reader meeting ``""`` would be entitled to think the definition was blank rather than absent.
    """
    cited = definitions.get(side.passage_id) if side.passage_id else None
    return {
        "publisher": side.publisher,
        "key": side.key or None,
        "definition": cited["definition"] if cited else None,
        "citation": (
            {
                "document": cited["document"],
                "locator": cited["locator"],
                "publisher": cited["publisher"],
                "title": cited["title"],
                "url": cited["url"],
                "retrieved": cited["retrieved"],
                "sha256": cited["sha256"],
            }
            if cited
            else None
        ),
    }


def register_payload(corpus_dir: Path) -> dict[str, Any]:
    """The register as a committable artifact, with both definitions quoted verbatim.

    The two counts at the end are the register's own composition, and they are stated for the
    same reason a scope block is: a reader who sees two comparable pairs has to know how many
    candidates were considered before two means anything.
    """
    definitions = load_definitions(corpus_dir)
    return {
        "register_version": REGISTER_VERSION,
        "entries": [
            {
                "label": entry.label,
                "verdict": entry.verdict,
                "reason": entry.reason,
                "note": entry.note or None,
                "scorecard": _side_payload(entry.scorecard, definitions),
                "ipeds": _side_payload(entry.ipeds, definitions),
            }
            for entry in REGISTER
        ],
        "comparable": sum(1 for entry in REGISTER if entry.verdict == COMPARABLE),
        "not_comparable": sum(1 for entry in REGISTER if entry.verdict == NOT_COMPARABLE),
    }
