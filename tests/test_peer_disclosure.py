"""Peer disclosure: what comparable institutions published, on every field rather than one.

``PeerGroup`` answers "did comparable institutions publish *this value*", which is the question an
implausible finding raises. It was the only peer comparison on the site, so five of the six rows
on an institution page had no context at all. "64.5% publish no admission rate" is a fact about
the country; a reader of one college's page wants to know whether the colleges most like it manage
to publish what it did not.

Three numbers on that page could be wrong in ways nobody would see, and each has a test here:

* the institution counted in its own comparison, which lets a college be part of the evidence
  about itself;
* a share taken over the whole group rather than over the institutions the field reached, which
  counts peers who were never asked as peers who failed to answer;
* an empty denominator printed as ``0%``, which says every comparable institution failed to
  publish something none of them were asked.

Every count asserted below is recomputed from the committed capture, never restated.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, peers, site
from disclosed.disclosure import CLASSIFICATIONS
from disclosed.fields import FIELDS
from disclosed.grading import grade_institution
from disclosed.peers import MIN_PEERS

_ROOT = Path(__file__).resolve().parent.parent
_LABELS = [f.label for f in FIELDS]


@pytest.fixture(scope="module")
def committed_report() -> dict[str, Any]:
    return json.loads((_ROOT / "data" / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def committed_corpus() -> list[dict[str, Any]]:
    return json.loads((_ROOT / "data" / "sample.json").read_text(encoding="utf-8"))


def _record(unit_id: str, ownership: int, level: int, state: str, **values: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": unit_id,
        "school.name": f"College {unit_id}",
        "school.ownership": ownership,
        "school.degrees_awarded.predominant": level,
        "school.state": state,
    }
    record.update(values)
    return record


def _report_from(corpus: list[dict[str, Any]], tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(corpus), encoding="utf-8")
    out = tmp_path / "report.json"
    assert cli.main(["grade", "--source", str(source), "--out", str(out)]) == 0
    payload: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return payload


class TestTheCountsAreTheCommittedCaptureRegraded:
    """Recomputed from the records, not restated from the report."""

    def test_the_committed_report_carries_a_panel_at_all(
        self, committed_report: dict[str, Any]
    ) -> None:
        """Otherwise every assertion below is a loop over nothing."""
        published = committed_report["peer_disclosure"]

        assert published["min_peers"] == MIN_PEERS
        assert sorted(published["labels"]) == sorted(_LABELS)
        assert len(published["groups"]) > 20

    def test_every_group_count_is_what_grading_the_capture_produces(
        self, committed_report: dict[str, Any], committed_corpus: list[dict[str, Any]]
    ) -> None:
        expected: dict[str, dict[str, dict[str, int]]] = {}
        for record in committed_corpus:
            _, key = peers.peer_group_for(record)
            group = expected.setdefault(
                peers.group_key(key),
                {label: dict.fromkeys(sorted(CLASSIFICATIONS), 0) for label in _LABELS},
            )
            graded = grade_institution(record)
            for result in graded.results:
                group[result.field.label][result.disclosure.value] += 1

        published = {
            key: value["counts"]
            for key, value in committed_report["peer_disclosure"]["groups"].items()
        }

        assert published == expected

    def test_every_graded_row_names_a_group_the_payload_carries(
        self, committed_report: dict[str, Any]
    ) -> None:
        groups = committed_report["peer_disclosure"]["groups"]
        named = {row["peer_group"] for row in committed_report["grades"] if "peer_group" in row}

        assert named
        assert named <= set(groups)
        assert len(named) == len(groups)

    def test_every_institution_in_the_report_is_in_exactly_one_group(
        self, committed_report: dict[str, Any]
    ) -> None:
        totals = {
            key: sum(value["counts"][_LABELS[0]].values())
            for key, value in committed_report["peer_disclosure"]["groups"].items()
        }

        assert sum(totals.values()) == len(committed_report["grades"])


class TestThePanelIsTheGroupMinusTheInstitutionItself:
    def test_the_institution_is_excluded_from_its_own_comparison(
        self, committed_report: dict[str, Any]
    ) -> None:
        """A college cannot be part of the evidence about itself.

        The same rule ``peer_context`` applies to the value comparison, checked here on the
        disclosure one: for every institution and every field, the panel is the group's published
        count with its own classification decremented by exactly one.
        """
        panels = site.peer_panels(committed_report)
        groups = committed_report["peer_disclosure"]["groups"]
        assert len(panels) == len(committed_report["grades"])

        for row in committed_report["grades"]:
            panel = panels[row["unit_id"]]
            published = groups[row["peer_group"]]["counts"]
            for label in _LABELS:
                own = row["fields"][label]
                expected = dict(published[label])
                expected[own] -= 1
                assert dict(panel[label].counts) == expected

    def test_the_counts_of_every_panel_sum_to_its_size(
        self, committed_report: dict[str, Any]
    ) -> None:
        """A panel whose parts do not add up to its whole is one where an institution has been
        dropped, and the most likely place to drop one is the state nobody thought about."""
        for panel in site.peer_panels(committed_report).values():
            for label, disclosure in panel.items():
                assert sum(disclosure.counts.values()) == disclosure.size, label

    def test_a_panel_is_exactly_one_smaller_than_its_group(
        self, committed_report: dict[str, Any]
    ) -> None:
        groups = committed_report["peer_disclosure"]["groups"]
        for row in committed_report["grades"]:
            panel = site.peer_panels(committed_report)[row["unit_id"]]
            whole = sum(groups[row["peer_group"]]["counts"][_LABELS[0]].values())

            assert panel[_LABELS[0]].size == whole - 1
            break

    def test_the_denominator_leaves_out_the_peers_nobody_asked(self) -> None:
        """Suppressed and inapplicable peers leave it, the same rule the grade and drift follow.

        A share over the whole group would count institutions that were never asked as
        institutions that failed to answer.
        """
        disclosure = peers.PeerDisclosure(
            field_label="Admission rate",
            description="somewhere",
            counts={
                "reported": 8,
                "missing": 2,
                "implausible": 0,
                "suppressed": 5,
                "not_applicable": 5,
            },
        )

        assert disclosure.size == 20
        assert disclosure.asked == 10
        assert disclosure.reporting == 8


class TestWhatThePageSays:
    def _build(self, report: dict[str, Any], tmp_path: Path) -> Path:
        out = tmp_path / "site"
        site.build(report, out, generated="2026-09-07")
        return out

    def test_every_peer_cell_on_every_page_replays_from_the_committed_report(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        """The number a reader sees, recomputed from the payload rather than trusted."""
        out = self._build(committed_report, tmp_path)
        panels = site.peer_panels(committed_report)
        checked = 0
        for row in committed_report["grades"]:
            page = out / "institution" / row["unit_id"] / "index.html"
            if not page.is_file():
                continue
            text = page.read_text(encoding="utf-8")
            for label, disclosure in panels[row["unit_id"]].items():
                if disclosure.usable and disclosure.asked:
                    expected = f"{disclosure.reporting} of {disclosure.asked} publish it"
                    assert expected in text, (row["unit_id"], label)
                    checked += 1

        assert checked > 100

    def test_a_group_too_small_to_compare_says_so_rather_than_printing_a_share(
        self, tmp_path: Path
    ) -> None:
        corpus = [_record(str(i), 1, 3, "WY", **{f.key: 0.5 for f in FIELDS}) for i in range(1, 4)]
        report = _report_from(corpus, tmp_path)

        out = self._build(report, tmp_path)
        text = (out / "institution" / "1" / "index.html").read_text(encoding="utf-8")

        assert "only 2 comparable institutions; too few to compare" in text
        assert not re.search(r"\d+ of \d+ publish it", text)

    def test_a_field_nobody_comparable_was_asked_renders_words_and_never_zero(
        self, tmp_path: Path
    ) -> None:
        """Every peer suppressed it, so the denominator is empty. A zero there would say every
        comparable institution had failed to publish something none of them were asked."""
        values = {f.key: 0.5 for f in FIELDS}
        tuition = next(f for f in FIELDS if f.label == "In-state tuition")
        corpus = [
            _record(str(i), 1, 3, "WY", **{**values, tuition.key: "PrivacySuppressed"})
            for i in range(1, 16)
        ]
        report = _report_from(corpus, tmp_path)
        panels = site.peer_panels(report)
        assert panels["1"]["In-state tuition"].asked == 0

        out = self._build(report, tmp_path)
        text = (out / "institution" / "1" / "index.html").read_text(encoding="utf-8")

        assert "no comparable institution was asked this" in text
        # A 0% share elsewhere on the page is a real measurement -- nought of fourteen peers
        # published that field. The thing that must never appear is a share over nothing.
        assert not re.search(r"\b\d+ of 0 publish it", text)

    def test_a_field_the_payload_does_not_cover_is_a_blank_cell_and_never_a_ragged_row(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        """A report whose panel covers fewer fields than a row carries.

        No report this code writes is in that state -- ``labels`` and every row's ``fields`` are
        built from the same graded results -- so this is what a report from another rules version
        would look like. Two things have to hold. The cell must stay **empty**, because the three
        sentences the column can say are all claims about a comparison that was made and this one
        was not. And the row must keep its width: a shared renderer whose cell count can fall
        below its header count produces a column of headings with nothing under them, which is a
        broken table for a screen reader and a whole field silently unaccounted for.
        """
        report = json.loads(json.dumps(committed_report))
        dropped = "In-state tuition"
        report["peer_disclosure"]["labels"] = [
            label for label in report["peer_disclosure"]["labels"] if label != dropped
        ]
        unit_id = next(
            row["unit_id"] for row in report["grades"] if row["unit_id"] in site.peer_panels(report)
        )
        assert dropped not in site.peer_panels(report)[unit_id]

        out = self._build(report, tmp_path)
        text = (out / "institution" / unit_id / "index.html").read_text(encoding="utf-8")

        table = text[text.index("<table>") : text.index("</table>")]
        assert table.count('<th scope="col">') == 4
        for row_html in table.split("<tr>")[2:]:
            assert row_html.count('<th scope="row">') + row_html.count("<td") == 4, row_html

        # The dropped field's own row carries an empty final cell and no sentence about peers.
        tuition_row = next(r for r in table.split("<tr>") if f">{dropped}</a>" in r)
        assert tuition_row.rstrip().endswith("<td></td></tr>"), tuition_row
        assert "publish it" not in tuition_row
        assert "too few to compare" not in tuition_row

    def test_the_group_is_named_once_under_the_table(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        out = self._build(committed_report, tmp_path)
        row = committed_report["grades"][0]
        text = (out / "institution" / row["unit_id"] / "index.html").read_text(encoding="utf-8")
        description = committed_report["peer_disclosure"]["groups"][row["peer_group"]][
            "description"
        ]

        assert "this institution excluded" in text
        assert html.escape(description) in text
        assert text.count("this institution excluded") == 1

    def test_the_column_header_is_declared_like_every_other(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        out = self._build(committed_report, tmp_path)
        row = committed_report["grades"][0]
        text = (out / "institution" / row["unit_id"] / "index.html").read_text(encoding="utf-8")

        assert '<th scope="col">Comparable institutions</th>' in text

    def test_the_methodology_page_explains_the_comparison(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        out = self._build(committed_report, tmp_path)
        text = (out / "methodology" / "index.html").read_text(encoding="utf-8")

        assert "how many comparable institutions published that field at all" in text


class TestAPageWithNoPeerGroupIsUnchanged:
    def test_a_report_with_no_peer_payload_renders_no_column(self, tmp_path: Path) -> None:
        """A column of dashes looks like an answer."""
        report = json.loads((_ROOT / "data" / "report.json").read_text(encoding="utf-8"))
        del report["peer_disclosure"]
        for row in report["grades"]:
            row.pop("peer_group", None)

        out = tmp_path / "site"
        site.build(report, out, generated="2026-09-07")
        text = (out / "institution" / report["grades"][0]["unit_id"] / "index.html").read_text(
            encoding="utf-8"
        )

        assert "Comparable institutions" not in text
        assert 'class="peers"' not in text

    def test_a_row_naming_a_group_the_payload_does_not_carry_gets_no_panel(
        self, committed_report: dict[str, Any]
    ) -> None:
        report = json.loads(json.dumps(committed_report))
        report["grades"][0]["peer_group"] = '[9, 9, "ZZ"]'

        panels = site.peer_panels(report)

        assert report["grades"][0]["unit_id"] not in panels

    def test_the_pages_of_institutions_with_a_group_are_the_only_ones_that_gained_a_column(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        stripped = json.loads(json.dumps(committed_report))
        del stripped["peer_disclosure"]
        for row in stripped["grades"]:
            row.pop("peer_group", None)

        with_, without = tmp_path / "a", tmp_path / "b"
        site.build(committed_report, with_, generated="2026-09-07")
        site.build(stripped, without, generated="2026-09-07")

        changed = {
            page.relative_to(with_).parts[0]
            for page in sorted(with_.rglob("index.html"))
            if page.read_bytes() != (without / page.relative_to(with_)).read_bytes()
        }

        assert changed == {"institution"}


class TestTheGroupingRefusesWhatItCannotCount:
    def test_a_corpus_and_a_grade_list_of_different_lengths_are_refused(self) -> None:
        """The two are read in parallel; a mismatch attaches one institution's classifications to
        another's peer group."""
        with pytest.raises(ValueError, match="read in parallel"):
            peers.disclosure_by_group(
                [_record("1", 1, 3, "CA")], [{}, {}], labels=["Admission rate"]
            )

    def test_a_classification_this_build_does_not_know_is_not_folded_into_one_it_does(
        self,
    ) -> None:
        """Putting it in "missing" would publish a version gap between the report and this code
        as a gap in what the publisher disclosed."""
        totals = peers.disclosure_by_group(
            [_record("1", 1, 3, "CA"), _record("2", 1, 3, "CA")],
            [{"Admission rate": "reported"}, {"Admission rate": "withheld"}],
            labels=["Admission rate"],
        )
        counts = next(iter(totals.values()))["Admission rate"]

        assert counts == {
            "implausible": 0,
            "missing": 0,
            "not_applicable": 0,
            "reported": 1,
            "suppressed": 0,
        }
        assert sum(counts.values()) == 1

    def test_every_state_is_present_with_a_zero_rather_than_omitted(self) -> None:
        totals = peers.disclosure_by_group(
            [_record("1", 1, 3, "CA")], [{"Admission rate": "reported"}], labels=["Admission rate"]
        )

        assert set(next(iter(totals.values()))["Admission rate"]) == set(CLASSIFICATIONS)


class TestTheGroupKeyIsUnambiguous:
    def test_an_absent_part_and_the_word_none_are_different_keys(self) -> None:
        """``"|".join`` renders them identically, which is this project's own defect class
        appearing in a dictionary key."""
        assert peers.group_key((None, 3, "CA")) != peers.group_key(("None", 3, "CA"))

    def test_the_key_round_trips(self) -> None:
        assert json.loads(peers.group_key((1, 3, "CA"))) == [1, 3, "CA"]

    def test_the_same_group_always_produces_the_same_key(self) -> None:
        first = peers.peer_group_for(_record("1", 1, 3, "CA"))[1]
        second = peers.peer_group_for(_record("2", 1, 3, "CA"))[1]

        assert peers.group_key(first) == peers.group_key(second)


class TestItIsDeterministic:
    def test_two_renders_of_the_same_report_are_byte_identical(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        first, second = tmp_path / "a", tmp_path / "b"
        site.build(committed_report, first, generated="2026-09-07")
        site.build(committed_report, second, generated="2026-09-07")

        for page in sorted(first.rglob("index.html")):
            assert page.read_bytes() == (second / page.relative_to(first)).read_bytes()

    def test_the_committed_report_regrades_to_itself(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        """The peer payload is part of the artifact now, so it has to survive a regrade."""
        out = tmp_path / "report.json"
        assert (
            cli.main(["grade", "--source", str(_ROOT / "data" / "sample.json"), "--out", str(out)])
            == 0
        )

        assert json.loads(out.read_text(encoding="utf-8")) == committed_report

    def test_no_page_prints_a_share_over_an_empty_denominator(
        self, committed_report: dict[str, Any], tmp_path: Path
    ) -> None:
        """The whole point, asserted over the published build rather than over a fixture."""
        out = tmp_path / "site"
        site.build(committed_report, out, generated="2026-09-07")

        for page in sorted((out / "institution").rglob("index.html")):
            text = page.read_text(encoding="utf-8")
            assert not re.search(r"\b0 of 0\b", text), page
