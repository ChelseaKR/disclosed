"""The receipt an institution can check its own page with, and the replay that checks it.

Three claims are worth more than the rest of this file put together, and each has a class here.

**A receipt never carries a reported value.** Asserted over every one of the 6,273 institutions in
the committed census rather than over a fixture, because the whole point of the rule is that it
holds for the college whose numbers nobody thought about. A fixture would prove it for the two
records somebody chose.

**A tampered receipt is caught, and the field is named.** The verifier exists to be run by
somebody who does not trust the publisher, so the interesting case is not "the receipt is right"
but "the receipt says something the capture does not support".

**The same capture produces the same bytes, in a different interpreter.** Rendering the same
receipt twice in one process proves almost nothing: iteration order over the same objects is
stable inside a process whatever the hash seed. So the determinism check runs the CLI in separate
interpreters under different ``PYTHONHASHSEED`` values, over a record with six fields, because one
field has no order to get wrong.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from disclosed import cli, receipts, site
from disclosed.disclosure import Disclosure
from disclosed.fields import FIELDS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLE = DATA / "sample.json"
CENSUS = DATA / "census" / "scorecard.json"
REPORT = DATA / "report.json"

# Every key a field entry may carry when the field is not implausible. A receipt that grew one
# more would be republishing something, and the whole argument for the file is that it does not.
_NON_IMPLAUSIBLE_KEYS = frozenset(
    {"key", "label", "classification", "rationale_anchor", "in_denominator", "weight"}
)


def _records(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        records: list[dict[str, Any]] = list(raw["records"])
        return records
    return list(raw)


@pytest.fixture(scope="module")
def sample_source() -> receipts.ReceiptSource:
    return receipts.load_source(SAMPLE, _records(SAMPLE), None)


@pytest.fixture(scope="module")
def census_source() -> receipts.ReceiptSource:
    raw = json.loads(CENSUS.read_text(encoding="utf-8"))
    return receipts.load_source(CENSUS, list(raw["records"]), raw["provenance"])


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The committed report, rendered once with receipts, for the whole module to read."""
    out = tmp_path_factory.mktemp("receipts-site")
    site.build(
        json.loads(REPORT.read_text(encoding="utf-8")),
        out,
        origin="https://example.test",
        generated="2026-09-07",
        receipts=receipts.load_source(SAMPLE, _records(SAMPLE), None),
    )
    return out


class TestTheSourcesAreThere:
    """The premise the rest of this file rests on, asserted rather than assumed."""

    def test_the_sample_and_the_census_are_both_committed(self) -> None:
        assert SAMPLE.is_file() and SAMPLE.stat().st_size > 0
        assert CENSUS.is_file() and CENSUS.stat().st_size > 0

    def test_the_census_is_big_enough_to_be_a_census(self, census_source: Any) -> None:
        """A scale check, because 'over every institution' below means nothing if it is six."""
        assert len(census_source.records) > 6_000


class TestAReceiptNeverCarriesAReportedValue:
    """The invariant, over the population rather than over a fixture.

    An implausible field carries its number because the number is the finding and an institution
    cannot argue with a bound it has not been shown. Every other classification carries none: a
    file listing 6,273 colleges' tuition, earnings and completion rates beside a letter grade is a
    performance record, and this project grades disclosure.
    """

    def test_no_field_but_an_implausible_one_carries_a_value_anywhere_in_the_census(
        self, census_source: receipts.ReceiptSource
    ) -> None:
        offenders: list[str] = []
        implausible_seen = 0
        for unit_id in census_source.records:
            receipt = census_source.receipt(unit_id)
            assert receipt is not None
            for entry in receipt["fields"]:
                if entry["classification"] == Disclosure.IMPLAUSIBLE.value:
                    implausible_seen += 1
                    assert "value" in entry
                    continue
                extra = set(entry) - _NON_IMPLAUSIBLE_KEYS
                if extra:
                    offenders.append(f"{unit_id} {entry['key']}: {sorted(extra)}")
        assert not offenders, (
            f"{len(offenders)} field entries carry more than a classification: {offenders[:5]}"
        )
        assert implausible_seen, (
            "no implausible field appeared anywhere in the census, so the branch that is allowed "
            "to carry a value was never exercised and this test proved only that an untaken "
            "branch takes nothing"
        )

    def test_a_reported_value_does_not_appear_in_the_bytes_of_its_own_receipt(self) -> None:
        """The structural check above, done again against the serialised file.

        A distinctive value, so a hit cannot be a coincidence with a weight or a schema version.
        """
        record = {
            "id": 999_001,
            "school.name": "Fixture College",
            "school.state": "CA",
            "school.ownership": 1,
            "school.degrees_awarded.predominant": 3,
            "latest.earnings.10_yrs_after_entry.median": 43_217,
            "latest.completion.completion_rate_4yr_150nt": 0.6183,
            "latest.admissions.admission_rate.overall": 0.7429,
            "latest.aid.median_debt.completers.overall": 21_853,
            "latest.cost.tuition.in_state": 13_741,
            "latest.student.size": 8_629,
        }
        identity = receipts.SourceIdentity(name="fixture.json", sha256="0" * 64, walked_at=None)
        text = receipts.dumps(receipts.receipt_for(record, source=identity))

        assert '"classification": "reported"' in text
        for value in ("43217", "0.6183", "0.7429", "21853", "13741", "8629"):
            assert value not in text, f"the receipt republished the reported value {value}"

    def test_an_implausible_value_is_in_the_receipt_with_the_bounds_it_fell_outside(self) -> None:
        """The exception, exercised. Without this the rule above is satisfied by a receipt that
        carries no values at all, which would leave a graded institution nothing to argue with."""
        record = {
            "id": 999_002,
            "school.name": "Zero Tuition College",
            "school.state": "CA",
            "school.ownership": 1,
            "school.degrees_awarded.predominant": 2,
            "latest.cost.tuition.in_state": 0,
        }
        identity = receipts.SourceIdentity(name="fixture.json", sha256="0" * 64, walked_at=None)
        receipt = receipts.receipt_for(record, source=identity)

        tuition = next(e for e in receipt["fields"] if e["key"] == "latest.cost.tuition.in_state")
        assert tuition["classification"] == Disclosure.IMPLAUSIBLE.value
        assert tuition["value"] == 0
        assert tuition["credible_max"] == 150_000.0
        assert tuition["zero_is_credible"] is False
        assert tuition["rationale"]


class TestTheReplayCatchesATamperedReceipt:
    """The verifier's reason to exist: it is run by somebody who does not trust the publisher."""

    def _receipt(self, source: receipts.ReceiptSource) -> dict[str, Any]:
        receipt = source.receipt("100654")
        assert receipt is not None
        return receipt

    def test_an_untouched_receipt_agrees(self, sample_source: receipts.ReceiptSource) -> None:
        result = receipts.verify(
            self._receipt(sample_source),
            list(sample_source.records.values()),
            source=sample_source.identity,
        )

        assert result.agreed is True
        assert result.same_source is True
        assert result.exit_code == receipts.AGREES

    def test_a_mutated_classification_is_named(self, sample_source: receipts.ReceiptSource) -> None:
        receipt = self._receipt(sample_source)
        receipt["fields"][2]["classification"] = Disclosure.MISSING.value

        result = receipts.verify(
            receipt, list(sample_source.records.values()), source=sample_source.identity
        )

        assert result.agreed is False
        assert result.exit_code == receipts.DISAGREES
        named = {d.what for d in result.disagreements}
        assert named == {receipt["fields"][2]["key"]}, (
            "only the mutated field should be named. A verifier that also reported score and "
            "letter here would be reporting the receipt's own arithmetic, which the mutation did "
            "not touch, and burying the one field that actually moved."
        )

    def test_a_rewritten_rules_version_is_a_disagreement_in_its_own_right(
        self, sample_source: receipts.ReceiptSource
    ) -> None:
        """A field that moved between two rules versions is ambiguous: the publisher may have
        changed, or we may have. The verifier says which one it cannot rule out."""
        receipt = self._receipt(sample_source)
        receipt["rules_version"] = "1999-01-01.1"

        result = receipts.verify(
            receipt, list(sample_source.records.values()), source=sample_source.identity
        )

        assert result.exit_code == receipts.DISAGREES
        assert "rules_version" in {d.what for d in result.disagreements}

    def test_an_institution_the_source_does_not_hold_is_not_a_disagreement(
        self, sample_source: receipts.ReceiptSource
    ) -> None:
        """Reported apart, because 'the grader disagrees' and 'you handed me the wrong file' are
        different answers and one of them is not about the institution at all."""
        receipt = self._receipt(sample_source)
        receipt["unit_id"] = "999999999"

        result = receipts.verify(
            receipt, list(sample_source.records.values()), source=sample_source.identity
        )

        assert result.found is False
        assert result.agreed is None
        assert result.disagreements == ()
        assert result.exit_code == receipts.NOT_IN_SOURCE

    def test_a_receipt_naming_another_capture_is_flagged_but_still_replayed(
        self, sample_source: receipts.ReceiptSource
    ) -> None:
        """A digest mismatch is reported in its own field and does not become the verdict.

        Replaying an old receipt against a newer capture is a thing somebody will legitimately
        want to do, and answering it with "disagrees" would say the grader changed its mind when
        what changed was the input.
        """
        receipt = self._receipt(sample_source)
        receipt["source"] = {"name": "elsewhere.json", "sha256": "f" * 64, "walked_at": None}

        result = receipts.verify(
            receipt, list(sample_source.records.values()), source=sample_source.identity
        )

        assert result.same_source is False
        assert result.exit_code == receipts.AGREES


class TestTheVerbsAndTheirExitCodes:
    """The exit codes are the interface; a caller scripts against them."""

    def _written(self, tmp_path: Path) -> Path:
        out = tmp_path / "receipt.json"
        assert cli.main(["receipt", "100654", "--source", str(SAMPLE), "--out", str(out)]) == 0
        return out

    def test_a_receipt_round_trips_through_the_two_verbs(self, tmp_path: Path) -> None:
        written = self._written(tmp_path)

        assert cli.main(["verify-receipt", str(written), "--source", str(SAMPLE)]) == 0

    def test_asking_for_an_institution_the_source_does_not_hold_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "receipt.json"

        code = cli.main(["receipt", "404404", "--source", str(SAMPLE), "--out", str(out)])

        assert code == receipts.NOT_IN_SOURCE
        assert not out.exists()
        assert "not in" in capsys.readouterr().err

    def test_a_tampered_file_exits_one_and_names_the_field(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        written = self._written(tmp_path)
        payload = json.loads(written.read_text(encoding="utf-8"))
        payload["fields"][0]["classification"] = Disclosure.MISSING.value
        written.write_text(receipts.dumps(payload), encoding="utf-8")

        code = cli.main(["verify-receipt", str(written), "--source", str(SAMPLE)])

        assert code == receipts.DISAGREES
        assert payload["fields"][0]["key"] in capsys.readouterr().out

    def test_a_receipt_for_a_missing_institution_exits_two(self, tmp_path: Path) -> None:
        written = self._written(tmp_path)
        payload = json.loads(written.read_text(encoding="utf-8"))
        payload["unit_id"] = "999999999"
        written.write_text(receipts.dumps(payload), encoding="utf-8")

        assert (
            cli.main(["verify-receipt", str(written), "--source", str(SAMPLE)])
            == receipts.NOT_IN_SOURCE
        )

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param([], id="not an object"),
            pytest.param({"kind": "something-else"}, id="wrong kind"),
            pytest.param(
                {"kind": receipts.RECEIPT_KIND, "schema_version": 99}, id="later schema version"
            ),
            pytest.param(
                {"kind": receipts.RECEIPT_KIND, "schema_version": 1, "fields": []}, id="no unit id"
            ),
            pytest.param(
                {"kind": receipts.RECEIPT_KIND, "schema_version": 1, "unit_id": "1"},
                id="no field list",
            ),
        ],
    )
    def test_a_file_that_is_not_a_receipt_exits_three_rather_than_agreeing(
        self, tmp_path: Path, payload: Any
    ) -> None:
        """A verifier that returns 'agrees' for a file it could not read is a check that cannot
        fail, which is the defect this project keeps finding in other people's work."""
        written = tmp_path / "not-a-receipt.json"
        written.write_text(json.dumps(payload), encoding="utf-8")

        assert (
            cli.main(["verify-receipt", str(written), "--source", str(SAMPLE)])
            == receipts.UNREADABLE
        )

    def test_unparseable_json_exits_three(self, tmp_path: Path) -> None:
        written = tmp_path / "broken.json"
        written.write_text("{not json", encoding="utf-8")

        assert (
            cli.main(["verify-receipt", str(written), "--source", str(SAMPLE)])
            == receipts.UNREADABLE
        )

    def test_a_source_that_is_not_a_scorecard_file_exits_three(self, tmp_path: Path) -> None:
        written = self._written(tmp_path)
        bad = tmp_path / "source.json"
        bad.write_text('{"kind": "something-else"}', encoding="utf-8")

        assert (
            cli.main(["verify-receipt", str(written), "--source", str(bad)]) == receipts.UNREADABLE
        )
        assert cli.main(["receipt", "100654", "--source", str(bad)]) == receipts.UNREADABLE

    def test_an_empty_source_is_refused_rather_than_issuing_no_receipts_quietly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text("[]", encoding="utf-8")

        assert cli.main(["receipt", "100654", "--source", str(empty)]) == receipts.UNREADABLE
        assert "no records" in capsys.readouterr().err

    def test_the_json_form_carries_the_same_verdict_as_the_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        written = self._written(tmp_path)
        capsys.readouterr()

        code = cli.main(["verify-receipt", str(written), "--source", str(SAMPLE), "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert code == receipts.AGREES
        assert payload["agreed"] is True and payload["same_source"] is True

    def test_without_out_the_receipt_goes_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        capsys.readouterr()

        assert cli.main(["receipt", "100654", "--source", str(SAMPLE)]) == 0
        assert json.loads(capsys.readouterr().out)["kind"] == receipts.RECEIPT_KIND


class TestTheSameCaptureProducesTheSameBytes:
    """Determinism, measured across processes rather than inside one.

    Two renders in one interpreter share their objects and their iteration order, so a lost
    ``sorted()`` is invisible to them. These runs are separate interpreters with different hash
    seeds, over a record with six fields: one field would have no order to get wrong.
    """

    def _run(self, tmp_path: Path, seed: str) -> str:
        out = tmp_path / f"receipt-{seed}.json"
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "disclosed.cli",
                "receipt",
                "100654",
                "--source",
                str(SAMPLE),
                "--out",
                str(out),
            ],
            check=True,
            cwd=ROOT,
            env=environment,
            capture_output=True,
        )
        return out.read_text(encoding="utf-8")

    def test_three_interpreters_with_three_hash_seeds_write_identical_receipts(
        self, tmp_path: Path
    ) -> None:
        rendered = {self._run(tmp_path, seed) for seed in ("0", "1", "12345")}

        assert len(rendered) == 1
        only = rendered.pop()
        assert json.loads(only)["unit_id"] == "100654"
        assert len(json.loads(only)["fields"]) == len(FIELDS)


class TestEveryPageLinksAReceiptThatExists:
    """The published contract: a page that offers a receipt has one beside it."""

    def test_every_institution_page_links_a_receipt_and_the_file_is_there(
        self, built: Path
    ) -> None:
        pages = sorted((built / "institution").rglob("index.html"))
        assert len(pages) > 100, "too few institution pages for this to have proved anything"
        for page in pages:
            assert 'href="receipt.json"' in page.read_text(encoding="utf-8"), page
            assert (page.parent / "receipt.json").is_file(), page

    def test_each_receipt_verifies_against_the_committed_source(self, built: Path) -> None:
        """The sampled verification `make verify` carries. Every receipt, in fact: the whole set
        replays in under a second, so there is no reason to check a sample of them."""
        source = receipts.load_source(SAMPLE, _records(SAMPLE), None)
        records = list(source.records.values())
        checked = 0
        for path in sorted((built / "institution").rglob("receipt.json")):
            receipt = receipts.read_receipt(json.loads(path.read_text(encoding="utf-8")))
            result = receipts.verify(receipt, records, source=source.identity)
            assert result.exit_code == receipts.AGREES, (path, result.as_dict())
            checked += 1
        assert checked > 100

    def test_a_build_without_the_flag_mentions_no_receipt_at_all(self, tmp_path: Path) -> None:
        """Absence over a broken promise: a page linking receipt.json where none was written
        would publish a 404 as evidence."""
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        site.build(report, tmp_path, origin="https://example.test", generated="2026-09-07")

        assert not list(tmp_path.rglob("receipt.json"))
        for page in sorted((tmp_path / "institution").rglob("index.html")):
            assert "receipt.json" not in page.read_text(encoding="utf-8"), page


class TestTheBuildRefusesAReceiptThatArguesWithItsPage:
    """Two files, two runs, one page: nothing but this check stops them disagreeing."""

    def test_a_report_and_a_source_that_classify_differently_stop_the_build(
        self, tmp_path: Path
    ) -> None:
        report = {
            "scope": {
                "kind": "sample",
                "source": "College Scorecard",
                "institutions": 1,
                "states": 1,
                "universe": 6300,
                "note": "one record",
            },
            "institutions": 1,
            "ungradeable": 0,
            "overall": {
                "label": "all institutions",
                "graded": 1,
                "ungradeable": 0,
                "mean_score": 1.0,
                "worst_fields": [],
            },
            "by_state": [],
            "implausible": [],
            "grades": [
                {
                    "unit_id": "100654",
                    "name": "Alabama A & M University",
                    "state": "AL",
                    "score": 1.0,
                    "letter": "A",
                    # The capture says this is reported. The report says it is not, which is what
                    # a report graded from other bytes looks like.
                    "fields": {"Admission rate": "missing"},
                }
            ],
        }
        source = receipts.load_source(SAMPLE, _records(SAMPLE), None)

        with pytest.raises(site.ReceiptMismatch, match="Admission rate"):
            site.build(
                report,
                tmp_path,
                origin="https://example.test",
                generated="2026-09-07",
                receipts=source,
            )

    def test_the_command_reports_the_refusal_rather_than_raising(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report_path = tmp_path / "report.json"
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        report["grades"][0]["fields"]["Admission rate"] = Disclosure.MISSING.value
        report_path.write_text(json.dumps(report), encoding="utf-8")

        code = cli.main(
            [
                "site",
                "--report",
                str(report_path),
                "--receipts-from",
                str(SAMPLE),
                "--out",
                str(tmp_path / "site"),
                "--generated",
                "2026-09-07",
            ]
        )

        assert code == 1
        assert "refusing to build" in capsys.readouterr().err

    def test_a_receipts_source_that_cannot_be_read_stops_the_build(self, tmp_path: Path) -> None:
        assert (
            cli.main(
                [
                    "site",
                    "--report",
                    str(REPORT),
                    "--receipts-from",
                    str(tmp_path / "absent.json"),
                    "--out",
                    str(tmp_path / "site"),
                    "--generated",
                    "2026-09-07",
                ]
            )
            == 1
        )


class TestTheSourceIdentity:
    """What a receipt says about the bytes it came from."""

    def test_the_digest_is_over_the_file_a_reader_can_hash_themselves(self) -> None:
        identity = receipts.identify_source(SAMPLE, None)

        assert identity.name == "sample.json"
        assert identity.walked_at is None
        assert len(identity.sha256) == 64

    def test_a_capture_carries_its_walk_date_and_a_bare_array_does_not(
        self, census_source: receipts.ReceiptSource, sample_source: receipts.ReceiptSource
    ) -> None:
        """The absent date stays absent. Filling it in from the clock would date the federal
        record to the day somebody happened to render a page."""
        assert census_source.identity.walked_at
        assert sample_source.identity.walked_at is None

    def test_a_blank_walk_date_is_read_as_absent_rather_than_as_a_date(self) -> None:
        identity = receipts.identify_source(SAMPLE, {"walked_at": "   "})

        assert identity.walked_at is None

    def test_the_path_is_not_published_only_the_name(self) -> None:
        """Where a capture sits on the machine that rendered the site is not part of the claim."""
        identity = receipts.identify_source(SAMPLE, None)

        assert "/" not in identity.name

    def test_a_record_with_no_id_is_dropped_rather_than_keyed_on_nothing(self) -> None:
        indexed = receipts.records_by_unit_id([{"id": None}, {"school.name": "x"}, {"id": 7}])

        assert set(indexed) == {"7"}

    def test_a_source_holds_no_receipt_for_a_record_it_does_not_have(
        self, sample_source: receipts.ReceiptSource
    ) -> None:
        assert sample_source.receipt("999999999") is None
