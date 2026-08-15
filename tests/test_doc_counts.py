"""Every figure the README states, checked against the data it is a figure about.

A project that grades other people on the gap between what they published and what their data
says cannot carry that gap in its own front page. The gap is easy to open: a rule changes, the
artifact is regenerated, the prose keeps the number it was written with, and nothing anywhere
fails. It has already happened here more than once, in the published methodology rationale that
claimed three zeros where the capture holds two, and in a README that stated a share the report
rounded differently.

So the README is treated as an artifact with a generator, like ``data/dataset.csv``. Each test
below reads a figure out of the prose and recomputes it from the committed payload, using this
project's own arithmetic (``drift.Snapshot.rate``) rather than a second implementation of it that
could be wrong in the same direction.

Two rules make this gate hard to defeat by accident:

* A pattern that no longer matches is a **failure**, never a silent pass. Reword the sentence and
  the test tells you to bring the pattern with it, in the same commit as the prose.
* Figures are compared as the strings the README prints, so a rounding change fails here rather
  than being absorbed into a comparison of floats nobody reads.

Figures that cannot be derived from committed bytes at all live in
:class:`TestTheRawDirectoryFigures`, which says so out loud instead of pretending to check them.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from disclosed import drift
from disclosed.sources import ipeds

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data"

# Whitespace-collapsed so a pattern written as flowing prose still matches after the README's
# hard wrapping puts a newline in the middle of a sentence. Reflowing a paragraph is not a change
# to a claim, and this gate should not fail as though it were.
_PROSE = re.sub(r"\s+", " ", (_ROOT / "README.md").read_text(encoding="utf-8"))
_CITATION = re.sub(r"\s+", " ", (_ROOT / "CITATION.cff").read_text(encoding="utf-8"))

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

# A figure as the README prints it, thousands separator included, without swallowing the comma or
# full stop that ends the clause it sits at the end of.
_N = r"[\d,]*\d"


def _load(name: str) -> Any:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


_REPORT: Any = _load("report.json")
_NATIONAL: Any = _load("national.json")
_SNAPSHOTS = {
    year: drift.Snapshot(**_load(f"snapshots/ipeds/{year}.json")) for year in (2021, 2022, 2023)
}

_ADMISSION = "Admission rate"
_ATHLETICS = "Equity in athletics disclosure"
_CALCULATOR = "Net price calculator"
_WEB = "Institution web address"
_ADMISSIONS_PAGE = "Admissions information"


def _stated(pattern: str) -> list[tuple[str, ...]]:
    """Every figure the README states in one shape of sentence.

    An empty result is a failure and not a skip. A gate that stops matching stops guarding, and
    the quietest way to break this file is to reword a sentence so nothing matches it any more:
    the suite would stay green while the number drifted. If this fires, bring the pattern along
    with the prose in the same commit.
    """
    found = [m.groups() for m in re.finditer(pattern, _PROSE)]
    assert found, (
        f"README no longer states anything matching {pattern!r}. The sentence moved, was "
        "reworded, or was dropped. Update this pattern in the same commit as the prose, or this "
        "gate is guarding nothing."
    )
    return found


def _field(payload: Any, label: str) -> Any:
    entry = next((f for f in payload["fields"] if f["label"] == label), None)
    assert entry is not None, f"{label!r} is no longer a field in data/national.json"
    return entry


def _classified(label: str, state: str) -> int:
    return sum(1 for g in _REPORT["grades"] if g["fields"].get(label) == state)


class TestTheSampleFigures:
    """The 600-institution College Scorecard capture, and what the front page says about it."""

    def test_the_sample_size_is_the_capture_it_describes(self) -> None:
        graded = len(_REPORT["grades"])
        assert graded == _REPORT["scope"]["institutions"]
        for (stated,) in _stated(r"In a ([\d,]+)-institution sample of the College Scorecard"):
            assert stated == f"{graded:,}"
        for (stated,) in _stated(r"Across the ([\d,]+) institutions in the committed capture"):
            assert stated == f"{graded:,}"

    def test_the_corpus_table_matches_the_report(self) -> None:
        """The table under "What is a sample and what is national" is the citable summary."""
        states = {g["state"] for g in _REPORT["grades"]}
        california = sum(1 for g in _REPORT["grades"] if g["state"] == "CA")
        pattern = r"College Scorecard \| ([\d,]+) institutions, (\d+) states, California (\d+)%"
        for institutions, state_count, share in _stated(pattern):
            assert institutions == f"{len(_REPORT['grades']):,}"
            assert state_count == str(len(states))
            assert share == f"{california / len(_REPORT['grades']):.0%}".rstrip("%")

    def test_the_national_row_of_that_table_matches_the_national_artifact(self) -> None:
        for (stated,) in _stated(r"every institution there is, ([\d,]+)"):
            assert stated == f"{_NATIONAL['scope']['institutions']:,}"

    def test_the_headline_share_with_no_admission_rate_is_the_share_in_the_report(self) -> None:
        """The first figure a reader meets, and the one most likely to be quoted back.

        "Publishes no admission rate at all" is ``MISSING`` only. The institution that published
        an exact zero did publish something, and folding it in here would overstate the finding
        by borrowing a case the next clause is about.
        """
        missing = _classified(_ADMISSION, "missing")
        graded = len(_REPORT["grades"])
        pattern = rf"\*\*({_N}) of the ({_N}), or ([\d.]+)%, publish no admission rate at all\*\*"
        for count, total, share in _stated(pattern):
            assert count == f"{missing:,}"
            assert total == f"{graded:,}"
            assert share == f"{missing / graded:.1%}".rstrip("%")

    def test_exactly_one_institution_published_a_zero_admission_rate(self) -> None:
        """A count of one is the whole point of the sentence: it is offered as an artifact a
        reader can go and look at, not as a trend."""
        zeros = [f for f in _REPORT["implausible"] if f["field"] == _ADMISSION and f["value"] == 0]
        pattern = r"\*\*(\w+) institutions? publishe?s? an admission rate of exactly zero\*\*"
        for (word,) in _stated(pattern):
            assert _WORDS[word] == len(zeros)


class TestTheNationalFigures:
    """The IPEDS corpus: the only place this project makes a claim about the population."""

    def test_the_net_price_calculator_gap_is_the_list_it_names(self) -> None:
        gap = _field(_NATIONAL, _CALCULATOR)
        assert gap["missing"] == len(_NATIONAL["gaps"][_CALCULATOR])
        for (stated,) in _stated(r"no net price\s*calculator for ([\d,]+) of them"):
            assert stated == f"{gap['missing']:,}"
        for (stated,) in _stated(r"Getting to ([\d,]+) rather than"):
            assert stated == f"{gap['missing']:,}"

    def test_the_athletics_denominator_and_gap_are_the_ones_the_rule_produced(self) -> None:
        gap = _field(_NATIONAL, _ATHLETICS)
        assert gap["missing"] == len(_NATIONAL["gaps"][_ATHLETICS])
        pattern = r"moves the denominator from ([\d,]+) to \*\*([\d,]+)\*\*, of which \*\*([\d,]+)"
        for directory, applicable, missing in _stated(pattern):
            assert directory == f"{_NATIONAL['scope']['institutions']:,}"
            assert applicable == f"{gap['applicable']:,}"
            assert missing == f"{gap['missing']:,}"

    def test_the_number_of_ipeds_disclosures_is_the_number_graded(self) -> None:
        for (word,) in _stated(r"the (\w+) public disclosure addresses"):
            assert _WORDS[word] == len(_NATIONAL["fields"])

    def test_the_one_cross_source_disagreement_is_the_one_the_readme_names(self) -> None:
        """One disagreement, on sector, about a named institution. If a regenerated crosscheck
        ever produces a second, the README's "exactly one" has to move with it."""
        contradictions = _NATIONAL["contradictions"]
        _stated(r"disagree about exactly (one) on sector")
        assert [c["field_label"] for c in contradictions] == ["Sector"]
        found = contradictions[0]
        assert f"**{found['name']}.**" in _PROSE
        assert f"files it as **{found['scorecard_value'].split(' (')[0]}**" in _PROSE
        assert f"files it as **{found['ipeds_value'].split(' (')[0]}**" in _PROSE

    def test_no_institution_is_reported_as_disagreeing_on_state(self) -> None:
        _stated(r"they agree on state for every one")
        assert not [c for c in _NATIONAL["contradictions"] if c["field_label"] == "State"]

    def test_the_committed_national_artifact_is_the_size_the_readme_claims(self) -> None:
        """ "Just under 100 KB" is a claim about a file anyone can stat, so it gets checked like
        any other. It is also the number that justifies committing the artifact at all."""
        _stated(r"`data/national\.json` is just under 100 KB and committed")
        assert 90_000 <= (_DATA / "national.json").stat().st_size < 100_000


class TestTheDriftFigures:
    """The three IPEDS collection years, and the calibration argument built on them."""

    def _rate_change(self, label: str, earlier: int, later: int) -> float:
        moved = next(
            d
            for d in drift.compare(_SNAPSHOTS[earlier], _SNAPSHOTS[later])
            if d.field_label == label
        )
        assert moved.rate_change is not None
        return moved.rate_change

    def test_the_population_shrank_by_the_counts_stated(self) -> None:
        for was, now in _stated(rf"shrank from ({_N}) institutions to ({_N})"):
            assert was == f"{_SNAPSHOTS[2021].institutions:,}"
            assert now == f"{_SNAPSHOTS[2023].institutions:,}"

    def test_the_web_address_movement_is_the_one_that_was_misread(self) -> None:
        """The founding mistake of this module: a count that fell while the rate rose."""
        lost = _SNAPSHOTS[2021].reported[_WEB] - _SNAPSHOTS[2023].reported[_WEB]
        for (stated,) in _stated(r"so ([\d,]+) fewer published a web address"):
            assert stated == f"{lost:,}"
        for (stated,) in _stated(r"reported as a systemic ([\d.]+)% collapse"):
            assert stated == f"{lost / _SNAPSHOTS[2021].reported[_WEB]:.1%}".rstrip("%")
        was, now = _SNAPSHOTS[2021].rate(_WEB), _SNAPSHOTS[2023].rate(_WEB)
        assert was is not None and now is not None
        for stated_was, stated_now in _stated(r"\*\*up\*\*, from ([\d.]+)% to ([\d.]+)%"):
            assert stated_was == f"{was:.2%}".rstrip("%")
            assert stated_now == f"{now:.2%}".rstrip("%")

    def test_the_athletics_movement_is_the_finding_that_ranked_fourth(self) -> None:
        was, now = _SNAPSHOTS[2021].rate(_ATHLETICS), _SNAPSHOTS[2023].rate(_ATHLETICS)
        assert was is not None and now is not None
        pattern = r"athletics disclosure rising from ([\d.]+)% to ([\d.]+)%"
        for stated_was, stated_now in _stated(pattern):
            assert stated_was == f"{was:.1%}".rstrip("%")
            assert stated_now == f"{now:.1%}".rstrip("%")
        gained = _SNAPSHOTS[2023].reported[_ATHLETICS] - _SNAPSHOTS[2021].reported[_ATHLETICS]
        lost = _SNAPSHOTS[2021].reported[_WEB] - _SNAPSHOTS[2023].reported[_WEB]
        pattern = rf"because ({_N}) is a small number next to ({_N})"
        for stated_gained, stated_lost in _stated(pattern):
            assert stated_gained == f"{gained:,}"
            assert stated_lost == f"{lost:,}"

    def test_the_rise_the_direction_word_got_backwards(self) -> None:
        """A field that shed reporters while its share rose. The count was right and the word
        beside it was wrong, which is why direction is read from the rate."""
        moved = self._rate_change(_ADMISSIONS_PAGE, 2021, 2023)
        reported = [_SNAPSHOTS[year].reported[_ADMISSIONS_PAGE] for year in (2021, 2023)]
        assert reported[1] < reported[0] and moved > 0
        for (stated,) in _stated(r"beside a rise of ([\d.]+) points"):
            assert stated == f"{moved * 100:.2f}"

    def test_the_threshold_calibration_is_what_the_three_years_say(self) -> None:
        for in_a_year, across_two in _stated(r"at ([\d.]+) in a year and ([\d.]+) across two"):
            assert in_a_year == f"{self._rate_change(_ATHLETICS, 2021, 2022) * 100:.2f}"
            assert across_two == f"{self._rate_change(_ATHLETICS, 2021, 2023) * 100:.2f}"

    def test_every_other_year_on_year_movement_really_does_sit_under_one_point(self) -> None:
        """The sentence that makes the threshold defensible rather than asserted. If a new
        collection year lands and some other field moves a point, this fails and the paragraph
        has to be rewritten, which is the correct outcome."""
        _stated(r"every year-on-year movement sits under one point except the athletics")
        for earlier, later in ((2021, 2022), (2022, 2023)):
            for moved in drift.compare(_SNAPSHOTS[earlier], _SNAPSHOTS[later]):
                if moved.field_label == _ATHLETICS or moved.rate_change is None:
                    continue
                assert abs(moved.rate_change) < 0.01, (
                    f"{moved.field_label} moved {moved.rate_change:+.2%} between {earlier} and "
                    f"{later}; the README says nothing but athletics passes one point"
                )
        assert abs(self._rate_change(_ATHLETICS, 2021, 2022)) >= 0.01

    def test_the_threshold_in_the_prose_is_the_threshold_in_the_code(self) -> None:
        for (stated,) in _stated(r"The (\d+)-point threshold is a judgement call"):
            assert float(stated) / 100 == drift.SYSTEMIC_THRESHOLD
        for (stated,) in _stated(r"The (\d+)% threshold that separates"):
            assert float(stated) / 100 == drift.SYSTEMIC_THRESHOLD


class TestTheCitationFile:
    """``CITATION.cff`` is the copy of these claims that travels furthest from the repository.

    Someone citing this project quotes the coverage sentence in that file rather than the README,
    and nothing else in the suite reads it. A citation that describes a corpus the repository no
    longer ships is a wrong claim with the author's name on it.
    """

    def test_the_citation_carries_the_coverage_the_report_carries(self) -> None:
        graded = len(_REPORT["grades"])
        states = {g["state"] for g in _REPORT["grades"]}
        match = re.search(rf"is ({_N}) institutions across ({_N}) states", _CITATION)
        assert match is not None, (
            "CITATION.cff no longer states the capture's coverage in the words this gate "
            "matches. Update the pattern in the same commit as the citation."
        )
        assert match.group(1) == f"{graded:,}"
        assert match.group(2) == str(len(states))

    def test_the_licence_is_the_same_one_in_every_file_that_states_it(self) -> None:
        """The licence is quoted in proposals and read off the repository by people deciding
        whether they may use this. Three files state it and they have to agree."""
        packaging = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert packaging["project"]["license"] == {"text": "Apache-2.0"}
        assert "license: Apache-2.0" in _CITATION
        assert "## License Apache-2.0." in _PROSE
        licence = (_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in licence and "Version 2.0, January 2004" in licence


@pytest.mark.skipif(
    not (_DATA / "HD2023.zip").exists(),
    reason=(
        "data/HD2023.zip is not committed, so these two figures are reproducible with a stated "
        "command (`make crosscheck`) rather than from committed bytes. This is the one gap in "
        "this file and it is stated rather than hidden: commit the archive and these checks run."
    ),
)
class TestTheRawDirectoryFigures:
    """The two figures taken from the directory file itself rather than from a graded artifact.

    They are the evidence for why an applicability rule was needed at all, so they are worth
    checking whenever the archive is present.
    """

    def _directory(self) -> list[dict[str, Any]]:
        return ipeds.parse_directory((_DATA / "HD2023.zip").read_bytes())

    def test_the_blank_athletics_addresses_are_the_reason_the_rule_exists(self) -> None:
        rows = self._directory()
        blank = sum(1 for r in rows if not str(r.get("ipeds.ATHURL", "")).strip())
        pattern = rf"blank for \*\*({_N}) of ({_N})\*\* directory rows"
        for stated_blank, stated_rows in _stated(pattern):
            assert stated_blank == f"{blank:,}"
            assert stated_rows == f"{len(rows):,}"

    def test_the_ungraded_calculator_blanks_are_what_the_rule_reduces(self) -> None:
        """Both ends of the sentence: what the directory shows before the rule, and how many of
        those blanks belong to institutions the statute never reached."""
        rows = self._directory()
        blank = sum(1 for r in rows if not str(r.get("ipeds.NPRICURL", "")).strip())
        graded_gap = _field(_NATIONAL, _CALCULATOR)["missing"]
        pattern = rf"\*\*({_N})\*\* of the ({_N}) directory rows carry no calculator address"
        for stated_blank, stated_rows in _stated(pattern):
            assert stated_blank == f"{blank:,}"
            assert stated_rows == f"{len(rows):,}"
        for (stated,) in _stated(rf"account for the other \*\*({_N})\*\*"):
            assert stated == f"{blank - graded_gap:,}"
