"""The classifier as something other projects can use, and the refusals that make it safe.

Three things are held here.

The published surface: the names ``disclosed`` exports, the doc that documents them, and the
changelog line that has to exist before the doc's surface revision may move. Any one of the three
can be changed alone, and if it is, this file fails.

The rule file: what it may say, and every reading of it this build declines to guess at. Each
refusal is tested against the permissive alternative it exists to avoid, because "raises an
error" is not the interesting half -- what matters is that the plausible wrong file does not
quietly produce a plausible wrong output.

The CSV verb, with a negative control in the form ``tests/test_i18n.py`` established: the
sabotage is asserted to have landed in the file before the file is asserted to have changed
because of it. A control that silently fails to apply reads as a pass.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import disclosed
from disclosed import rules
from disclosed.cli import main
from disclosed.disclosure import CLASSIFICATIONS, Disclosure
from disclosed.fields import ALL_FIELDS, APPLICABILITY_PREDICATES, Field, predicate_name

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "CLASSIFIER.md"
_CHANGELOG = _ROOT / "CHANGELOG.md"
_SCHEMA_FILE = _ROOT / "schema" / "classification.v1.schema.json"

# One row per state the classifier can reach, written the way a federal source actually writes it.
# The header carries a column with no rules (`name`) so that the pass-through is exercised, and an
# `ipeds.INSTCAT`/`ipeds.CYACTIVE` pair so an applicability predicate has something to read.
_CSV = (
    "unit_id,name,adm_rate,npc_url,ipeds.INSTCAT,ipeds.CYACTIVE\n"
    "1,Reported College,0.55,https://example.edu/npc,1,1\n"
    "2,Suppressed College,PrivacySuppressed,https://example.edu/npc,1,1\n"
    "3,Missing College,-1,https://example.edu/npc,1,1\n"
    "4,Not Asked College,-2,https://example.edu/npc,1,1\n"
    "5,Withheld College,-3,https://example.edu/npc,1,1\n"
    "6,Zero College,0,https://example.edu/npc,1,1\n"
    "7,Blank College,,https://example.edu/npc,1,1\n"
    "8,System Office,0.55,https://example.edu/npc,-2,1\n"
)

_RULES: dict[str, Any] = {
    "version": 1,
    "rules": [
        {
            "column": "adm_rate",
            "label": "Admission rate",
            "credible_min": 0.0,
            "credible_max": 1.0,
            "zero_is_credible": False,
            "sentinels": {"-1": "missing", "-2": "not_applicable", "-3": "suppressed"},
            "applies_when": "is_an_institution",
        },
        {"column": "npc_url", "label": "Net price calculator", "text_is_a_value": True},
    ],
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A directory holding the fixture CSV and rule file, plus a byte copy of each.

    The copies are what the negative control restores from. Restoring by rewriting the constant
    would prove only that the constant is still the constant.
    """
    (tmp_path / "input.csv").write_text(_CSV, encoding="utf-8")
    (tmp_path / "input.csv.orig").write_text(_CSV, encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps(_RULES), encoding="utf-8")
    return tmp_path


def _classify(workspace: Path) -> list[dict[str, str]]:
    text = (workspace / "input.csv").read_text(encoding="utf-8")
    parsed = rules.load_rules(workspace / "rules.json")
    out = rules.classify_table(text, parsed)
    header, *lines = out.strip().split("\n")
    keys = header.split(",")
    return [dict(zip(keys, line.split(","), strict=True)) for line in lines]


class TestThePublishedSurface:
    """The six names, the page that documents them, and the changelog line behind it."""

    def test_the_documented_names_are_exactly_the_exported_ones(self) -> None:
        page = _DOC.read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| `([A-Za-z_]+)` \|", page, re.M))

        assert documented == set(disclosed.__all__), (
            "docs/CLASSIFIER.md and disclosed.__all__ disagree about the public API. Whichever "
            "one moved, the other and the changelog have to move with it."
        )

    def test_every_exported_name_exists_and_is_documented_in_code(self) -> None:
        for name in disclosed.__all__:
            obj = getattr(disclosed, name)
            doc = getattr(obj, "__doc__", None)
            assert doc, f"{name} is exported with no docstring; the docstring is the contract"

    def test_the_surface_revision_is_named_in_the_changelog(self) -> None:
        """The coupling. Moving the surface means moving the revision, and that means saying so.

        Without this the doc and ``__all__`` could be edited together in one commit and nobody
        downstream would ever learn that the API they pinned had changed.
        """
        revision = re.search(r"^Surface revision: (\d+)$", _DOC.read_text(encoding="utf-8"), re.M)
        assert revision, "docs/CLASSIFIER.md must carry a 'Surface revision: N' line"

        expected = f"public API surface revision {revision.group(1)}"
        assert expected in _CHANGELOG.read_text(encoding="utf-8"), (
            f"CHANGELOG.md does not mention {expected!r}. The public surface may not move "
            "without a changelog entry naming the revision it moved to."
        )

    def test_classify_and_the_five_states_import_from_the_package_root(self) -> None:
        assert disclosed.classify("") is disclosed.Disclosure.MISSING
        assert disclosed.CLASSIFICATIONS == frozenset(d.value for d in disclosed.Disclosure)


class TestTheCommittedSchema:
    """The schema on disk against the enum in code, in both directions."""

    def test_the_committed_schema_is_what_the_code_generates(self) -> None:
        committed = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))

        assert committed == rules.schema(), (
            "schema/classification.v1.schema.json is stale. Regenerate it with "
            "`disclosed classify --schema`."
        )

    def test_the_schema_enumerates_the_five_states_and_no_others(self) -> None:
        committed = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))

        assert committed["$defs"]["disclosure"]["enum"] == sorted(CLASSIFICATIONS)
        assert len(committed["$defs"]["disclosure"]["enum"]) == 5

    def test_the_schema_names_every_applicability_predicate_this_build_implements(self) -> None:
        committed = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
        allowed = committed["$defs"]["rule"]["properties"]["applies_when"]["enum"]

        assert set(allowed) == {*APPLICABILITY_PREDICATES, None}

    def test_the_cli_prints_the_committed_schema_byte_for_byte(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["classify", "--schema"]) == 0

        assert capsys.readouterr().out == _SCHEMA_FILE.read_text(encoding="utf-8")

    def test_a_sixth_state_is_rejected_by_the_rule_loader(self) -> None:
        """The schema says five; this is the loader agreeing with it where it matters."""
        sixth = {
            "version": 1,
            "rules": [{"column": "adm_rate", "sentinels": {"-9": "probably_reported"}}],
        }

        with pytest.raises(rules.RuleFileError, match="not one of the five classifications"):
            rules.rules_from_payload(sixth)


class TestThisProjectsOwnFieldsRoundTrip:
    """The only honest test that the format expresses the rules it claims to."""

    def test_every_graded_field_survives_a_round_trip(self) -> None:
        payload = rules.rules_to_payload(ALL_FIELDS)
        parsed = rules.rules_from_payload(payload)

        assert len(parsed) == len(ALL_FIELDS)
        for field, rule in zip(ALL_FIELDS, parsed, strict=True):
            assert rule.column == field.key
            assert rule.credible_min == field.credible_min
            assert rule.credible_max == field.credible_max
            assert rule.zero_is_credible == field.zero_is_credible
            assert rule.text_is_a_value == field.text_is_a_value
            assert dict(rule.sentinels) == dict(field.sentinels)
            assert rule.predicate is field.applies_when

    def test_the_cli_prints_them_as_a_rule_file_the_loader_accepts(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["classify", "--rules"]) == 0

        printed = json.loads(capsys.readouterr().out)
        assert len(rules.rules_from_payload(printed)) == len(ALL_FIELDS)

    def test_a_field_whose_predicate_has_no_name_is_refused_rather_than_written_without_one(
        self,
    ) -> None:
        """Dropping ``applies_when`` silently would apply the field to every row it met."""
        unnamed = Field(
            key="x",
            label="X",
            credible_min=None,
            credible_max=None,
            zero_is_credible=True,
            rationale="",
            applies_when=lambda record: True,
        )
        assert predicate_name(unnamed.applies_when) is None

        with pytest.raises(rules.RuleFileError, match="no registered name"):
            rules.rules_to_payload([unnamed])


class TestWhatTheRuleLoaderRefuses:
    """Every refusal, against the permissive reading it exists to decline."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"version": 2, "rules": [{"column": "a"}]}, "this build reads version 1"),
            ({"rules": [{"column": "a"}]}, "this build reads version 1"),
            ({"version": 1}, "'rules' must be a non-empty list"),
            ({"version": 1, "rules": []}, "'rules' must be a non-empty list"),
            ({"version": 1, "rules": [{}]}, "every rule needs a non-empty 'column'"),
            ({"version": 1, "rules": [{"column": "  "}]}, "every rule needs a non-empty 'column'"),
            (
                {"version": 1, "rules": [{"column": "a", "credible_maximum": 1}]},
                "unknown key",
            ),
            ({"version": 1, "rules": [{"column": "a"}], "extra": 1}, "unknown top-level key"),
            (
                {"version": 1, "rules": [{"column": "a"}, {"column": "a"}]},
                "appears twice",
            ),
            (
                {"version": 1, "rules": [{"column": "a", "credible_min": 5, "credible_max": 1}]},
                "no value could ever be credible",
            ),
            (
                {"version": 1, "rules": [{"column": "a", "credible_min": "low"}]},
                "must be a number or null",
            ),
            (
                {"version": 1, "rules": [{"column": "a", "zero_is_credible": "yes"}]},
                "must be true or false",
            ),
            (
                {"version": 1, "rules": [{"column": "a", "label": 7}]},
                "'label' must be a string",
            ),
            (
                {"version": 1, "rules": [{"column": "a", "sentinels": []}]},
                "must be an object",
            ),
            (
                {"version": 1, "rules": [{"column": "a", "applies_when": 7}]},
                "must be a name or null",
            ),
            ({"version": 1, "rules": ["a"]}, "must be an object"),
            ("not a document", "must be an object"),
        ],
    )
    def test_it_refuses(self, payload: object, expected: str) -> None:
        with pytest.raises(rules.RuleFileError, match=re.escape(expected)):
            rules.rules_from_payload(payload)

    def test_an_unimplemented_applicability_predicate_is_refused_not_read_as_always(self) -> None:
        """The permissive reading puts rows the rule never reached into the denominator."""
        payload = {
            "version": 1,
            "rules": [{"column": "adm_rate", "applies_when": "is_accredited_in_california"}],
        }

        with pytest.raises(rules.RuleFileError, match="no applicability predicate named"):
            rules.rules_from_payload(payload)

    def test_a_bad_json_file_is_a_refusal_and_not_a_traceback(self, tmp_path: Path) -> None:
        path = tmp_path / "rules.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(rules.RuleFileError, match="is not valid JSON"):
            rules.load_rules(path)

    def test_null_bounds_and_omitted_flags_take_the_documented_defaults(self) -> None:
        (rule,) = rules.rules_from_payload(
            {"version": 1, "rules": [{"column": "a", "credible_min": None}]}
        )

        assert rule.credible_min is None
        assert rule.credible_max is None
        assert rule.zero_is_credible is True
        assert rule.text_is_a_value is False
        assert rule.applies_when is None
        assert rule.predicate is None
        assert rule.state_column == "a_disclosure"


class TestClassifyingATable:
    """The six absences the fixture carries, and the column that is not there at all."""

    def test_each_row_reaches_the_state_its_value_states(self, workspace: Path) -> None:
        rows = _classify(workspace)

        assert [row["adm_rate_disclosure"] for row in rows] == [
            "reported",
            "suppressed",
            "missing",
            "not_applicable",
            "suppressed",
            "implausible",
            "missing",
            "not_applicable",
        ]

    def test_the_five_states_and_only_the_five_appear(self, workspace: Path) -> None:
        rows = _classify(workspace)
        seen = {row["adm_rate_disclosure"] for row in rows}

        assert seen == CLASSIFICATIONS

    def test_a_zero_is_implausible_here_and_never_silently_reported(self, workspace: Path) -> None:
        """The reverse error: a present value that is not a measurement."""
        rows = _classify(workspace)

        assert rows[5]["adm_rate"] == "0"
        assert rows[5]["adm_rate_disclosure"] == "implausible"

    def test_the_applicability_predicate_removes_the_system_office(self, workspace: Path) -> None:
        """Row 8 discloses a perfectly good rate; it is not an institution, so it leaves."""
        rows = _classify(workspace)

        assert rows[7]["adm_rate"] == "0.55"
        assert rows[7]["adm_rate_disclosure"] == "not_applicable"

    def test_a_url_column_is_not_read_as_a_failed_number(self, workspace: Path) -> None:
        rows = _classify(workspace)

        assert {row["npc_url_disclosure"] for row in rows} == {"reported"}

    def test_columns_without_rules_pass_through_and_order_is_preserved(
        self, workspace: Path
    ) -> None:
        parsed = rules.load_rules(workspace / "rules.json")
        out = rules.classify_table(_CSV, parsed)

        assert out.split("\n")[0] == (
            "unit_id,name,adm_rate,adm_rate_disclosure,npc_url,npc_url_disclosure,"
            "ipeds.INSTCAT,ipeds.CYACTIVE"
        )
        assert [line.split(",")[1] for line in out.strip().split("\n")[1:]] == [
            "Reported College",
            "Suppressed College",
            "Missing College",
            "Not Asked College",
            "Withheld College",
            "Zero College",
            "Blank College",
            "System Office",
        ]

    def test_identical_input_produces_identical_output(self, workspace: Path) -> None:
        parsed = rules.load_rules(workspace / "rules.json")

        assert rules.classify_table(_CSV, parsed) == rules.classify_table(_CSV, parsed)

    def test_a_rule_for_a_column_the_file_does_not_have_is_refused(self, workspace: Path) -> None:
        """Absence rendered as a value, in the tool built to prevent it.

        The permissive reading marks all eight rows ``missing`` and writes a file reporting that
        nobody disclosed a completion rate, when the truth is that this file never asked.
        """
        parsed = rules.rules_from_payload({"version": 1, "rules": [{"column": "completion_rate"}]})

        with pytest.raises(rules.RuleFileError, match="has no column named 'completion_rate'"):
            rules.classify_table(_CSV, parsed)

    def test_a_file_with_no_header_is_refused(self) -> None:
        parsed = rules.rules_from_payload({"version": 1, "rules": [{"column": "a"}]})

        with pytest.raises(rules.RuleFileError, match="no header row"):
            rules.classify_table("", parsed)

    def test_classify_rows_refuses_the_absent_column_over_dictionaries_too(self) -> None:
        parsed = rules.rules_from_payload({"version": 1, "rules": [{"column": "missing_col"}]})

        with pytest.raises(rules.RuleFileError, match="has no column named"):
            rules.classify_rows([{"a": 1}], parsed)


class TestTheNegativeControl:
    """Sabotage the input, prove the sabotage landed, then prove the output moved.

    Every assertion in :class:`TestClassifyingATable` is of the form "the state is what the value
    says". So is the result of a classifier that had no effect at all. This class introduces one
    fault, reads the file back to confirm the fault is in it, and only then asserts the change.
    """

    def test_a_blank_never_classifies_as_reported(self, workspace: Path) -> None:
        before = _classify(workspace)
        assert before[0]["adm_rate"] == "0.55"
        assert before[0]["adm_rate_disclosure"] == "reported"

        csv_path = workspace / "input.csv"
        sabotaged = csv_path.read_text(encoding="utf-8").replace(
            "1,Reported College,0.55,", "1,Reported College,,"
        )
        csv_path.write_text(sabotaged, encoding="utf-8")

        # The sabotage, read back off disk. A control that silently fails to apply reads as a
        # pass, and "the state is not reported" is exactly what a no-op would also produce.
        assert "1,Reported College,,https://example.edu/npc,1,1" in csv_path.read_text(
            encoding="utf-8"
        )

        after = _classify(workspace)
        assert after[0]["adm_rate"] == ""
        assert after[0]["adm_rate_disclosure"] != "reported"
        assert after[0]["adm_rate_disclosure"] == "missing"

        csv_path.write_text(
            (workspace / "input.csv.orig").read_text(encoding="utf-8"), encoding="utf-8"
        )
        assert _classify(workspace) == before

    def test_removing_the_sentinel_map_turns_minus_one_into_a_measurement(
        self, workspace: Path
    ) -> None:
        """Why ``sentinels`` is not optional in practice, stated as a measurement.

        Without it, ``-1`` is a number, and a number inside no credible range at all is
        ``reported``. This is the bug the whole module exists to make hard, so it is worth
        showing that it is one edit away rather than asserting that it cannot happen.
        """
        before = _classify(workspace)
        assert before[2]["adm_rate_disclosure"] == "missing"

        rules_path = workspace / "rules.json"
        stripped = json.loads(rules_path.read_text(encoding="utf-8"))
        del stripped["rules"][0]["sentinels"]
        del stripped["rules"][0]["credible_min"]
        rules_path.write_text(json.dumps(stripped), encoding="utf-8")

        # Assert the sabotage landed before believing the result.
        reread = json.loads(rules_path.read_text(encoding="utf-8"))
        assert "sentinels" not in reread["rules"][0]
        assert "credible_min" not in reread["rules"][0]

        after = _classify(workspace)
        assert after[2]["adm_rate"] == "-1"
        assert after[2]["adm_rate_disclosure"] == "reported"

        rules_path.write_text(json.dumps(_RULES), encoding="utf-8")
        assert _classify(workspace) == before


class TestTheCommandLine:
    """Exit codes and the file the verb writes."""

    def test_it_writes_the_classified_file_and_exits_zero(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = workspace / "out.csv"

        code = main(
            [
                "classify-csv",
                str(workspace / "input.csv"),
                "--rules",
                str(workspace / "rules.json"),
                "--out",
                str(out),
            ]
        )

        assert code == 0
        assert "classified 8 rows against 2 rules" in capsys.readouterr().out
        assert out.read_text(encoding="utf-8").startswith(
            "unit_id,name,adm_rate,adm_rate_disclosure,"
        )

    def test_it_writes_to_stdout_by_default(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "classify-csv",
                str(workspace / "input.csv"),
                "--rules",
                str(workspace / "rules.json"),
            ]
        )

        assert code == 0
        assert "adm_rate_disclosure" in capsys.readouterr().out

    def test_a_refused_rule_file_exits_two_and_says_why(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = workspace / "bad.json"
        bad.write_text(json.dumps({"version": 1, "rules": [{"column": "nope"}]}), encoding="utf-8")

        code = main(["classify-csv", str(workspace / "input.csv"), "--rules", str(bad)])

        assert code == 2
        assert "refusing: the input has no column named 'nope'" in capsys.readouterr().err

    def test_a_missing_input_file_exits_two(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "classify-csv",
                str(workspace / "nowhere.csv"),
                "--rules",
                str(workspace / "rules.json"),
            ]
        )

        assert code == 2
        assert "refusing:" in capsys.readouterr().err

    def test_classify_needs_to_be_told_which_of_the_two_to_print(self) -> None:
        with pytest.raises(SystemExit) as raised:
            main(["classify"])

        assert raised.value.code == 2

    def test_the_installed_console_script_answers(self) -> None:
        """`disclosed classify --schema` from a shell, which is how a consumer meets it."""
        result = subprocess.run(
            [sys.executable, "-m", "disclosed.cli", "classify", "--schema"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_ROOT,
        )

        assert json.loads(result.stdout)["$defs"]["disclosure"]["enum"] == sorted(CLASSIFICATIONS)


class TestNothingPublishedMoved:
    """The site and the dataset are not in scope for this change, and must not have moved."""

    def test_the_graded_fields_are_untouched(self) -> None:
        assert len(ALL_FIELDS) == 12
        assert {predicate_name(f.applies_when) for f in ALL_FIELDS if f.applies_when} == set(
            APPLICABILITY_PREDICATES
        )

    def test_every_registered_predicate_is_one_a_field_actually_uses(self) -> None:
        """A registry that grew past its callers would offer rule authors dead names."""
        used = {predicate_name(f.applies_when) for f in ALL_FIELDS if f.applies_when is not None}

        assert set(APPLICABILITY_PREDICATES) == used

    def test_the_classifier_still_answers_the_way_the_five_states_are_documented(self) -> None:
        assert disclosed.classify(-1, sentinels={"-1": Disclosure.MISSING}) is Disclosure.MISSING
        assert disclosed.classify("PrivacySuppressed") is Disclosure.SUPPRESSED
        assert disclosed.classify(0.0, zero_is_credible=False) is Disclosure.IMPLAUSIBLE
        assert disclosed.classify(0.0, zero_is_credible=True) is Disclosure.REPORTED
        assert disclosed.classify("") is Disclosure.MISSING
