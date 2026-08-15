"""Every hand-typed count in this project's prose, recounted from the data it claims to describe.

`tests/test_disclosure.py::TestTheFiguresTheProseStates` already does this for the three figures
drawn from the College Scorecard capture, and it exists because one of them was wrong: the
completion-rate rationale said *three* institutions publish exactly zero, the capture holds two,
and the site printed three on the methodology page and on both institution pages citing it.

Everything that figure had in common with the rest of this project's prose, the rest of it still
had. A rationale is not a comment, it is rendered verbatim onto the methodology page. A module
docstring is where the reasoning behind a rule is kept, and the reasoning is the argument. Around
twenty more counts about the IPEDS directory, the three-year drift history and the peer groups
were typed into that prose by hand, and until the archives were committed there was no data in the
repository to check them against.

So this module counts all of them, from:

* `data/HD2023.zip` and `data/IC2023.zip` for the directory figures
* `data/snapshots/ipeds/{2021,2022,2023}.json` for the drift history the 2% threshold is argued from
* `data/report.json` for the peer group and coverage figures

Each is checked against the source, never against another copy of itself. Asserting only that two
files agree would let both drift together onto a wrong number, which is the failure this project
spends its whole time describing in other people's data.

All of them are correct today. That is not a reason to skip this: they were correct on the day the
completion-rate figure was typed too, and what changed was the rule underneath, silently.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

from disclosed import crosswalk, drift, grading, peers, scope
from disclosed.fields import field_by_key
from disclosed.site import _bound, methodology_page
from disclosed.sources import ipeds

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Counts the prose spells out rather than digits. A reword from "fourteen" to "14" should fail
# here and be corrected, not pass silently against a different number.
_NUMBER_WORDS = {11: "Eleven", 14: "fourteen"}


def _flat(text: str | None) -> str:
    """Whitespace-normalize prose so a claim wrapped across source lines still matches.

    A literal match would fail for the line wrapping rather than for the fact, and a test that
    fails for formatting is a test people learn to edit rather than to read.
    """
    return " ".join((text or "").split())


def _says(haystack: str | None, claim: str) -> None:
    flat = _flat(haystack)
    assert _flat(claim) in flat, f"prose does not state: {claim!r}"


def _absent(haystack: str | None, claim: str) -> None:
    """A reword must not be able to leave a stale count sitting beside the corrected one."""
    assert _flat(claim) not in _flat(haystack), f"prose still states: {claim!r}"


def _comments(module: str) -> str:
    """A module's own text with the comment markers stripped, whitespace-normalized.

    Three of these claims live in `#` comments rather than in docstrings: the veterans-page count,
    the argument that ATHASSOC already covers every institution the sports columns would, and the
    entire justification for `SYSTEMIC_THRESHOLD`. A comment is not less published for being a
    comment. Two of the three are paraphrased onto the methodology page almost verbatim, and all
    three are reasoning a reader is invited to check, which is the only test that matters here.
    """
    text = (ROOT / "src" / "disclosed" / module).read_text(encoding="utf-8")
    return " ".join(re.sub(r"(?m)^\s*#\s?", "", text).split())


def _raw_directory_rows() -> list[dict[str, str]]:
    """The HD archive as IPEDS shipped it, every column.

    Read raw rather than through the adapter because two published claims are about columns the
    adapter deliberately drops. `VETURL` is one of them, and the sentence about it is the reason
    that field is *not* graded, which makes it exactly the sort of claim that has to be checkable.
    """
    with zipfile.ZipFile(DATA / "HD2023.zip") as bundle:
        name = sorted(n for n in bundle.namelist() if n.lower().endswith(".csv"))[0]
        body = bundle.read(name)
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace"))))


@pytest.fixture(scope="module")
def directory() -> list[dict[str, str]]:
    return _raw_directory_rows()


@pytest.fixture(scope="module")
def merged() -> list[dict[str, Any]]:
    return ipeds.load_institutions(
        year=2023,
        cache=DATA / "HD2023.zip",
        characteristics_cache=DATA / "IC2023.zip",
    )


@pytest.fixture(scope="module")
def characteristics() -> dict[str, dict[str, Any]]:
    return ipeds.parse_characteristics((DATA / "IC2023.zip").read_bytes())


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return json.loads((DATA / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snapshots() -> dict[int, dict[str, Any]]:
    return {
        year: json.loads(
            (DATA / "snapshots" / "ipeds" / f"{year}.json").read_text(encoding="utf-8")
        )
        for year in (2021, 2022, 2023)
    }


@pytest.fixture(scope="module")
def methodology() -> str:
    """The rendered page, which is where most of these claims are actually published."""
    return methodology_page().body


def _blank(rows: list[dict[str, str]], column: str) -> int:
    return sum(1 for row in rows if not (row.get(column) or "").strip())


def _operating(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if (row.get("INSTCAT") or "").strip() != "-2" and (row.get("CYACTIVE") or "").strip() == "1"
    ]


class TestTheDirectoryFiguresTheProseStates:
    """Counts about the IPEDS directory, counted from the IPEDS directory."""

    def test_the_directory_is_still_the_size_the_prose_calls_it(
        self, directory: list[dict[str, str]]
    ) -> None:
        """6,163 is the denominator behind five separate published sentences."""
        rows = len(directory)
        assert rows == 6163
        _says(ipeds.__doc__, f"of {rows:,} directory rows have no athletics address")
        _says(ipeds.merge_characteristics.__doc__, f"of {rows:,} directory rows have no")
        _says(field_by_key("ipeds.DISAURL").rationale, f"of {rows:,} rows are blank")

    def test_the_athletics_denominator_and_the_blanks_behind_it(
        self, directory: list[dict[str, str]], merged: list[dict[str, Any]], methodology: str
    ) -> None:
        """The single most load-bearing pair of numbers in the project.

        4,469 is why the column went ungraded; 1,998 is what made it gradeable. If the rule moves
        and these do not, the methodology page explains the new rule using the old rule's reach.
        """
        blank = _blank(directory, "ATHURL")
        assert blank == 4469

        field = field_by_key("ipeds.ATHURL")
        assert field.applies_when is not None
        denominator = sum(1 for record in merged if field.applies_when(record))
        assert denominator == 1998

        rule = field.applies_when
        assert rule is not None
        _says(methodology, f"It is blank for {blank:,} of {len(directory):,} directory rows")
        _says(methodology, f"moves the denominator from {len(directory):,} to {denominator:,}")
        _says(field.rationale, f"moves the denominator from {len(directory):,} to {denominator:,}")
        _says(rule.__doc__, f"the directory alone shows {blank:,} blank athletics addresses")
        _says(ipeds.__doc__, f"{blank:,} of {len(directory):,} directory rows have no athletics")
        _says(ipeds.__doc__, f"an ungradeable column into a denominator of {denominator:,}")

    def test_the_athletics_rule_reaches_everyone_the_sports_columns_would(
        self, merged: list[dict[str, Any]]
    ) -> None:
        """The argument for using ATHASSOC rather than SPORT1-4, restated as arithmetic.

        The claim is a strict subset relation and a difference. Both are asserted, because a
        difference of 674 between two sets that had started to overlap differently would still
        print as 674 while meaning something else entirely.
        """
        associations = {
            str(r.get("id")) for r in merged if str(r.get("ipeds.ATHASSOC", "")).strip() == "1"
        }
        sports = {
            str(r.get("id"))
            for r in merged
            if any(str(r.get(f"ipeds.SPORT{n}", "")).strip() == "1" for n in (1, 2, 3, 4))
        }
        assert len(associations) == 2008
        assert len(sports) == 1334
        assert sports < associations, "the sports columns are no longer a strict subset"
        assert len(associations - sports) == 674

        rule = field_by_key("ipeds.ATHURL").applies_when
        assert rule is not None
        _says(
            rule.__doc__,
            f"the sports columns identify {len(sports):,} institutions and are a strict subset of "
            f"the {len(associations):,} this rule finds, so using them would excuse "
            f"{len(associations - sports)} institutions",
        )
        _says(_comments("sources/ipeds.py"), f"already covers all {len(sports):,} of them")

    def test_the_net_price_calculator_rule_states_who_it_leaves_out(
        self, directory: list[dict[str, str]]
    ) -> None:
        no_undergraduates = sum(1 for row in directory if (row.get("UGOFFER") or "").strip() == "2")
        assert no_undergraduates == 284
        rule = field_by_key("ipeds.NPRICURL").applies_when
        assert rule is not None
        _says(rule.__doc__, f"{no_undergraduates} rows offer no undergraduate education at all")

    def test_the_rows_the_operating_rule_deliberately_keeps(
        self, directory: list[dict[str, str]]
    ) -> None:
        """INSTCAT of -1 is "not reported", and excusing those rows would use one absence to
        excuse another. The prose says how many there are; this says the same, from the file."""
        not_reported = sum(1 for row in directory if (row.get("INSTCAT") or "").strip() == "-1")
        assert not_reported == 14
        # `_is_an_institution`, reached through a field rather than imported, so the test breaks if
        # a field ever stops applying the rule this docstring is about.
        rule = field_by_key("ipeds.WEBADDR").applies_when
        assert rule is not None
        _says(rule.__doc__, f"the {_NUMBER_WORDS[not_reported]} rows carrying it are real colleges")

    def test_the_two_url_rationales_that_argue_from_how_rare_a_blank_is(
        self, directory: list[dict[str, str]]
    ) -> None:
        """Both rationales rest entirely on their own count.

        "Only 101 of 6,163 rows are blank, which makes an absence here unusually informative" is an
        argument that stops working if the number moves. So is "the floor". These are counted
        across the whole directory rather than across the applicable set, and the prose says
        "rows", which is the directory. Both readings are defensible; only one is what was counted.
        """
        rows = len(directory)
        disability = _blank(directory, "DISAURL")
        web = _blank(directory, "WEBADDR")
        assert (disability, web) == (101, 76)
        _says(
            field_by_key("ipeds.DISAURL").rationale,
            f"Only {disability} of {rows:,} rows are blank",
        )
        _says(field_by_key("ipeds.WEBADDR").rationale, f"{web} rows in IPEDS are blank")

    def test_the_field_that_is_deliberately_not_graded_states_its_own_size(
        self, directory: list[dict[str, str]], methodology: str
    ) -> None:
        """The veterans page is the one candidate this project names and refuses to grade.

        The refusal is an argument about whether a duty exists, and it is made on the published
        page next to a count. `VETURL` is not a column the adapter keeps, so nothing else in the
        suite would ever notice this number going stale.
        """
        blank = _blank(directory, "VETURL")
        assert blank == 2377
        _says(methodology, f"The veterans information page is blank for {blank:,} institutions")
        # Stated a second time in a comment above IPEDS_FIELDS, which is prose the page paraphrases.
        _says(
            _comments("fields.py"),
            f"the veterans information page, is blank for {blank:,} institutions",
        )

    def test_the_join_states_how_many_rows_it_cannot_join(
        self, directory: list[dict[str, str]], characteristics: dict[str, dict[str, Any]]
    ) -> None:
        """ "Nothing that follows from this join is currently load-bearing" is a claim with a
        number behind it, and the number is the only thing keeping it true."""
        unmatched = [
            row for row in directory if (row.get("UNITID") or "").strip() not in characteristics
        ]
        operating = _operating(unmatched)
        title_iv = [r for r in operating if (r.get("PSET4FLG") or "").strip() == "1"]
        assert (len(unmatched), len(operating), len(title_iv)) == (114, 11, 0)
        _says(
            ipeds.merge_characteristics.__doc__,
            f"{len(unmatched)} of {len(directory):,} directory rows have no characteristics row. "
            f"{_NUMBER_WORDS[len(operating)]} of them are operating institutions and none of "
            "those participate in Title IV",
        )
        assert len(title_iv) == 0

    def test_the_suppressed_sector_codes_the_crosscheck_refuses_to_call_a_disagreement(
        self, directory: list[dict[str, str]]
    ) -> None:
        suppressed = sum(1 for row in directory if (row.get("CONTROL") or "").strip() == "-3")
        assert suppressed == 29
        _says(crosswalk._comparable.__doc__, f"``CONTROL`` of -3, of which there are {suppressed}")


class TestTheDriftHistoryTheThresholdIsArguedFrom:
    """The 2% threshold is defended with eight figures from three collection years.

    That defence is the reason a reader is asked to accept the threshold, and it was the least
    checkable prose in the project: two of its three years had no committed inputs at all. It is
    now checkable twice, here and by the replay in `tests/test_replay.py`.
    """

    def _rate(self, snapshot: dict[str, Any], label: str) -> float:
        return snapshot["reported"][label] / snapshot["applicable"][label]

    def test_the_population_shrank_by_what_the_prose_says_it_did(
        self, snapshots: dict[int, dict[str, Any]], methodology: str
    ) -> None:
        was, now = snapshots[2021]["institutions"], snapshots[2023]["institutions"]
        assert (was, now) == (6289, 6163)
        for text in (methodology, drift.__doc__):
            _says(text, f"the directory shrank from {was:,} institutions to {now:,}")

    def test_the_web_address_finding_that_was_false_is_stated_with_its_real_numbers(
        self, snapshots: dict[int, dict[str, Any]], methodology: str
    ) -> None:
        """A count that fell while the rate rose. Every figure in the sentence, recomputed."""
        label = "Institution web address"
        lost = snapshots[2021]["reported"][label] - snapshots[2023]["reported"][label]
        naive = lost / snapshots[2021]["reported"][label]
        was, now = self._rate(snapshots[2021], label), self._rate(snapshots[2023], label)
        assert lost == 130
        assert f"{naive:.1%}" == "2.1%"
        assert (f"{was:.2%}", f"{now:.2%}") == ("99.93%", "99.95%")
        assert now > was, "the sentence's whole point is that the share went up"

        _says(methodology, f"so {lost} fewer published a web address")
        _says(methodology, f"reported as a systemic {naive:.1%} collapse")
        _says(methodology, f"from {was:.2%} to {now:.2%}")
        _says(drift.__doc__, f"so {lost} fewer institutions published a web address")
        _says(drift.__doc__, f"a systemic {naive:.1%} collapse")
        _says(drift.__doc__, f"gone *up*, from {was:.2%} to {now:.2%}")

    def test_the_one_real_movement_is_stated_with_its_real_numbers(
        self, snapshots: dict[int, dict[str, Any]], methodology: str
    ) -> None:
        label = "Equity in athletics disclosure"
        was, now = self._rate(snapshots[2021], label), self._rate(snapshots[2023], label)
        gained = snapshots[2023]["reported"][label] - snapshots[2021]["reported"][label]
        assert (f"{was:.1%}", f"{now:.1%}") == ("57.1%", "59.4%")
        assert gained == 52
        _says(methodology, f"rising from {was:.1%} to {now:.1%}")
        _says(methodology, f"because {gained} is a small number next to 130")
        _says(drift.__doc__, f"climbing from {was:.1%} to {now:.1%}")
        _says(drift.__doc__, f"because {gained} is a small number next to 130")

    def test_the_two_movements_the_threshold_is_set_between(
        self, snapshots: dict[int, dict[str, Any]], methodology: str
    ) -> None:
        """ "1.75 points in a year and 2.26 across two" is the entire justification for 2%."""
        label = "Equity in athletics disclosure"
        one_year = (self._rate(snapshots[2022], label) - self._rate(snapshots[2021], label)) * 100
        two_years = (self._rate(snapshots[2023], label) - self._rate(snapshots[2021], label)) * 100
        assert (f"{one_year:.2f}", f"{two_years:.2f}") == ("1.75", "2.26")
        assert one_year < drift.SYSTEMIC_THRESHOLD * 100 <= two_years, (
            "the threshold no longer sits between the two movements the prose sets it between"
        )
        _says(methodology, f"rose {one_year:.2f} points in a year and {two_years:.2f} across two")
        _says(
            _comments("drift.py"),
            f"rose {one_year:.2f} points in one year and {two_years:.2f} across both",
        )

    def test_nothing_else_in_three_years_moved_a_whole_point(
        self, snapshots: dict[int, dict[str, Any]], methodology: str
    ) -> None:
        """The claim that makes the threshold a measurement rather than a preference.

        If a second disclosure ever moves more than a point, this sentence stops being true and
        the page goes on asserting it. That is the shape of the bug this file exists for.
        """
        athletics = "Equity in athletics disclosure"
        over_a_point = {
            f"{label} {before}->{after}": round(
                (self._rate(snapshots[after], label) - self._rate(snapshots[before], label)) * 100,
                3,
            )
            for label in snapshots[2021]["reported"]
            for before, after in ((2021, 2022), (2022, 2023))
            if abs(self._rate(snapshots[after], label) - self._rate(snapshots[before], label))
            >= 0.01
        }
        assert set(over_a_point) == {f"{athletics} 2021->2022"}, over_a_point
        _says(
            methodology,
            "every year-on-year movement in these six disclosures sits under one point except the "
            "athletics disclosure",
        )


class TestTheCaptureFiguresTheProseStates:
    """Counts about the committed College Scorecard capture that live outside `fields.py`."""

    def test_the_peer_argument_that_settled_the_west_valley_finding(
        self, report: dict[str, Any]
    ) -> None:
        """The worked example the whole peers module is built around.

        This paragraph is the project's answer to "your threshold is wrong", and it answers with
        five numbers. It had already drifted once: it used to say 78 institutions report a tuition
        between $1,108 and about $1,430, and the group in the committed report is 78 peers of which
        77 reported, ranging 1,108 to 1,571.
        """
        (finding,) = [f for f in report["implausible"] if f["name"] == "West Valley College"]
        group = finding["peers"]
        comparable = group["size"] + 1  # the institution is excluded from its own peer group
        assert (comparable, group["reporting"]) == (79, 77)
        assert group["publishing_same_value"] == 0

        values = [
            record["latest.cost.tuition.in_state"]
            for record in json.loads((DATA / "sample.json").read_text(encoding="utf-8"))
            if record.get("school.state") == "CA"
            and record.get("school.ownership") == 1
            and record.get("school.degrees_awarded.predominant") == 2
            and isinstance(record.get("latest.cost.tuition.in_state"), (int, float))
        ]
        publishing = len(values)
        lowest, highest = min(v for v in values if v), max(values)
        assert (publishing, lowest, highest) == (78, 1108, 1571)

        _says(
            peers.__doc__,
            f"Of {comparable} California public associate-predominant institutions in the "
            f"committed capture, {publishing} publish a tuition figure at all; "
            f"{group['reporting']} of those fall between ${lowest:,} and ${highest:,}",
        )
        _absent(peers.__doc__, "$1,430")

    def test_the_live_case_the_second_peer_threshold_was_added_for(
        self, report: dict[str, Any]
    ) -> None:
        """ "A peer group of 49 carrying a verdict about 6" is a fact about the committed capture,
        cited as the reason `is_usable` checks two counts. It is still in there."""
        groups = [f["peers"] for f in report["implausible"] if f.get("peers")]
        assert any(g["size"] == 49 and g["reporting"] == 6 for g in groups), (
            "the capture no longer carries the finding this docstring argues from"
        )
        _says(
            peers.PeerGroup.is_usable.__doc__,
            'a peer group of 49 carry the verdict "0 of 6 comparable institutions publish this '
            'value"',
        )

    def test_the_coverage_docstring_describes_the_capture_it_names(
        self, report: dict[str, Any]
    ) -> None:
        grades = report["grades"]
        states = {row["state"] for row in grades if row["state"]}
        california = sum(1 for row in grades if row["state"] == "CA")
        assert (len(grades), len(states)) == (600, 13)
        _says(
            scope.__doc__,
            f"capture is {len(grades)} institutions across {len(states)} states with California "
            f"at {california / len(grades):.0%}",
        )


class TestTheMethodologyPagePrintsTheRulesRatherThanRestatingThem:
    """The other half of the fix: numbers a reader checks a decision against, printed from the
    constant that made the decision instead of typed next to it."""

    def test_no_credible_bound_is_published_in_scientific_notation(self, methodology: str) -> None:
        """Four of the six Scorecard ceilings shipped as `5e+05`, `4e+05`, `1.5e+05` and `2.5e+05`
        on the page whose entire job is to be argued with by non-specialists."""
        ranges = re.findall(r"Credible range: ([^.]*?)\. ", methodology)
        assert ranges, "the page stopped stating credible ranges at all"
        assert not [text for text in ranges if "e+" in text or "e-" in text], ranges
        assert "Credible range: 1,000 to 500,000" in methodology
        assert "Credible range: 0 to 400,000" in methodology
        assert "Credible range: 0 to 150,000" in methodology
        assert "Credible range: 1 to 250,000" in methodology

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (500_000.0, "500,000"),
            (400_000.0, "400,000"),
            (150_000.0, "150,000"),
            (250_000.0, "250,000"),
            (1_000.0, "1,000"),
            (1.0, "1"),
            (0.0, "0"),
            # Not a rule anyone has written yet, and the reason this is not `,.0f`: that would
            # have fixed the exponent and published a credible range of 0.5 as "0". The defect
            # being repaired here is a formatter that was right for the values it was written
            # against and wrong for the values it met, so the replacement is checked against
            # values it has not met either.
            (0.5, "0.5"),
            (1_250_000.75, "1,250,000.75"),
        ],
    )
    def test_the_bound_formatter_states_the_bound_it_was_given(
        self, value: float, expected: str
    ) -> None:
        assert _bound(value, upper=True) == expected
        assert "e+" not in _bound(value, upper=True)

    def test_an_absent_bound_is_words_rather_than_a_number(self) -> None:
        """A URL column has no credible range, and rendering one as 0 would invent a rule."""
        assert _bound(None, upper=True) == "no upper bound"
        assert _bound(None, upper=False) == "no lower bound"

    def test_the_letter_bands_are_printed_from_the_bands_the_grader_applies(
        self, methodology: str
    ) -> None:
        """An institution reading "C, 70%" is owed the threshold the code actually applied."""
        for threshold, letter in grading.BANDS:
            _says(methodology, f"{letter}, {threshold:.0%}")
        _says(methodology, f"{grading.BELOW_EVERY_BAND} below that")
        # And the grader agrees with the page at every boundary, from both sides.
        for threshold, letter in grading.BANDS:
            assert grading._letter(threshold) == letter
            assert grading._letter(threshold - 0.001) != letter

    def test_the_systemic_threshold_is_printed_from_the_constant_that_enforces_it(
        self, methodology: str
    ) -> None:
        stated = f"{drift.SYSTEMIC_THRESHOLD * 100:g}"
        _says(methodology, f"moves by at least {stated} percentage points")
        _says(methodology, f"At {stated}% the bar flags that and nothing else")

    def test_the_peer_threshold_is_printed_from_the_constant_that_enforces_it(
        self, methodology: str
    ) -> None:
        _says(methodology, f"at least {peers.MIN_PEERS} comparable institutions exist")
