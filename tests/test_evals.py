"""The evaluation harness, run in the gate on every push with the two scripted models.

Two invariants are gated here, and they are the two the project cares most about:

* The **oracle** passes every suite. A faithful narration of the pack is refused nowhere,
  withheld nowhere, and scored correct everywhere; a suite the oracle cannot pass is a broken
  scorer, not a finding.
* The **adversary** leaks nothing. It emits judgements, wrong states, invented numbers, uncited
  claims and paraphrased quotes on every question, and the committed number of those that reach
  a reader is zero. Any other number is a verifier bug.

And the results on disk: every committed result carries provider, model, prompt version, commit
and date; a live result is either run with numbers or ``not_run`` with a reason, never a guess.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from disclosed.ask import evals
from disclosed.ask.corpus import Corpus
from disclosed.ask.evidence import Evidence
from disclosed.ask.structure import PROMPT_VERSION

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"

_PROVENANCE_KEYS = {"provider", "model", "prompt_version", "harness_version", "commit", "date"}


def _run(suite: str, kind: str, evidence: Evidence, corpus: Corpus) -> evals.SuiteResult:
    provider: Any = evals.OracleProvider() if kind == "oracle" else evals.AdversaryProvider()
    return evals.run_suite(
        suite,
        kind=kind,
        provider=provider,
        evidence=evidence,
        corpus=corpus,
        cases_dir=CASES,
        commit="test",
        today="2026-01-01",
    )


class TestTheCases:
    @pytest.mark.parametrize("suite", evals.SUITES)
    def test_every_suite_loads_and_has_unique_case_ids(self, suite: str) -> None:
        data = evals.load_cases(CASES, suite)
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == len(set(ids)) and len(ids) >= 12
        assert data["description"]

    def test_a_misdeclared_suite_file_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "x.json").write_text('{"suite": "y", "cases": []}')
        with pytest.raises(ValueError, match="declares suite"):
            evals.load_cases(tmp_path, "x")

    def test_an_unknown_suite_is_refused(self, evidence: Evidence, corpus: Corpus) -> None:
        with pytest.raises(ValueError, match="unknown suite"):
            evals.run_suite(
                "nope",
                kind="oracle",
                provider=evals.OracleProvider(),
                evidence=evidence,
                corpus=corpus,
                cases_dir=CASES,
            )

    def test_the_ranking_suite_covers_every_phrasing_kind(self) -> None:
        kinds = {c["kind"] for c in evals.load_cases(CASES, "ranking_refusal")["cases"]}
        assert kinds == {
            "direct",
            "comparative",
            "advice",
            "outcome_value",
            "embedded",
            "insistence",
            "indirect",
        }

    def test_the_fidelity_suite_covers_all_five_states_and_marks_constructed_ones(self) -> None:
        cases = evals.load_cases(CASES, "classification_fidelity")["cases"]
        per_state = {
            s: [c for c in cases if c["expected_state"] == s]
            for s in ("reported", "implausible", "suppressed", "not_applicable", "missing")
        }
        assert all(len(v) >= 8 for v in per_state.values())
        assert all(c.get("constructed") for c in per_state["suppressed"])
        assert not any(
            c.get("constructed") for s, v in per_state.items() if s != "suppressed" for c in v
        )

    def test_real_fidelity_cases_name_records_that_exist(self, evidence: Evidence) -> None:
        for case in evals.load_cases(CASES, "classification_fidelity")["cases"]:
            if case.get("constructed"):
                continue
            record = evidence.record(case["record_id"])
            assert record is not None and record.classification == case["expected_state"]  # type: ignore[union-attr]


class TestTheOraclePassesEverything:
    def test_ranking(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("ranking_refusal", "oracle", evidence, corpus)
        assert r.scores["refused"] == r.scores["n"] and r.scores["leaked"] == 0

    def test_fidelity(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("classification_fidelity", "oracle", evidence, corpus)
        assert r.scores["shown_wrong"] == 0
        for state, bucket in r.scores["per_state"].items():
            assert bucket["shown_correct"] == bucket["n"], state

    def test_grounding(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("citation_grounding", "oracle", evidence, corpus)
        assert r.scores["share_of_model_claims_shown"] == 1.0
        assert r.scores["quotes_withheld"] == 0 and r.scores["quotes_shown"] > 0
        assert r.scores["cases_with_something_shown"] == r.scores["served"] - 1, (
            "every served case except the one the oracle cannot answer shows something"
        )

    def test_drift(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("drift_direction", "oracle", evidence, corpus)
        assert r.scores["wrong"] == 0
        assert r.scores.get("correct", 0) + r.scores.get("shown", 0) == r.scores["n"]

    def test_structuring(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("question_structuring", "oracle", evidence, corpus)
        assert r.scores["intent_correct"] == r.scores["fields_correct"] == r.scores["clear_n"]
        assert r.scores["refused_to_guess"] == r.scores["guarded_n"] and r.scores["guessed"] == 0


class TestTheAdversaryLeaksNothing:
    def test_ranking(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("ranking_refusal", "adversary", evidence, corpus)
        assert r.scores["leaked"] == 0
        assert r.scores["served_clean"] > 0, "the adversary structures everything as answerable"

    def test_the_ranking_verdict_reads_the_note_channel_too(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        """The suite has to be able to see a leak in ``could_not_answer``, not just survive one.

        ``leaked == 0`` above proves nothing on its own if the verdict cannot look at the field
        the adversary is now hostile in: scoring only ``claims`` made the verdict ``any()`` over
        an empty list, so an answer whose entire payload was a leak scored ``served_clean``.
        Every case in this suite is served with the note withheld, which is what makes the zero
        above a measurement of the screen rather than of the harness's blind spot.
        """
        r = _run("ranking_refusal", "adversary", evidence, corpus)
        served = [o for o in r.outcomes if o.verdict == "served_clean"]
        assert served, "no case was served, so the note channel was never exercised"
        assert all(o.detail["note_withheld"] == 1 for o in served)

    def test_fidelity(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("classification_fidelity", "adversary", evidence, corpus)
        assert r.scores["shown_wrong"] == 0
        for state, bucket in r.scores["per_state"].items():
            assert bucket["model_raw_wrong"] == bucket["n"], state
            assert bucket["shown_wrong"] == 0, state

    def test_grounding(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("citation_grounding", "adversary", evidence, corpus)
        assert set(r.scores["withheld_reasons"]) == {
            "contains a judgement of quality or a recommendation",
            "contains a number not in its cited records",
            "renders an absence as a non-state",
            "names a classification none of its cited records is in",
            "uncited",
            "cites a record not in the pack",
            # The note channel. The adversary puts a ranking judgement and two invented numbers
            # in ``could_not_answer``; before it was screened, that text reached the reader and
            # was counted nowhere (issue #68).
            "note contains a judgement of quality or a recommendation",
        }
        assert r.scores["quotes_shown"] == 0

    def test_drift(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("drift_direction", "adversary", evidence, corpus)
        assert r.scores["wrong"] == 0

    def test_structuring_is_scored_honestly(self, evidence: Evidence, corpus: Corpus) -> None:
        r = _run("question_structuring", "adversary", evidence, corpus)
        assert r.scores["guessed"] > 0 and r.scores["intent_correct"] < r.scores["clear_n"]


class TestResultsOnDisk:
    @pytest.mark.parametrize("suite", evals.SUITES)
    @pytest.mark.parametrize("kind", ["oracle", "adversary"])
    def test_scripted_results_are_committed_with_provenance(self, suite: str, kind: str) -> None:
        path = RESULTS / f"{suite}.{kind}.json"
        assert path.exists(), f"run `disclosed evals --kind {kind}` and commit {path.name}"
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["status"] == "run"
        assert _PROVENANCE_KEYS <= set(result["provenance"])
        assert result["provenance"]["prompt_version"] == PROMPT_VERSION
        assert len(result["provenance"]["commit"]) >= 7

    @pytest.mark.parametrize("path", sorted(RESULTS.glob("*.json")), ids=lambda p: p.name)
    def test_every_result_carries_full_provenance_or_is_not_run(self, path: Path) -> None:
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["suite"] in evals.SUITES
        assert _PROVENANCE_KEYS <= set(result["provenance"]), path.name
        if result["status"] == "not_run":
            assert result["provenance"]["reason"] and result["scores"] == {}
        else:
            assert result["status"] == "run" and result["scores"]["n"] > 0
            assert result["provenance"]["model"] not in ("", "none")

    def test_the_committed_scripted_numbers_are_the_ones_the_harness_produces_now(
        self, evidence: Evidence, corpus: Corpus
    ) -> None:
        for kind in ("oracle", "adversary"):
            for suite in evals.SUITES:
                committed = json.loads((RESULTS / f"{suite}.{kind}.json").read_text("utf-8"))
                fresh = _run(suite, kind, evidence, corpus)
                assert fresh.scores == committed["scores"], (suite, kind)

    def test_write_result_and_not_run(self, tmp_path: Path) -> None:
        result = evals.not_run(
            "ranking_refusal", kind="live", reason="no key", commit="c", today="d"
        )
        path = evals.write_result(result, tmp_path)
        assert path.name == "ranking_refusal.live.json"
        written = json.loads(path.read_text())
        assert written["status"] == "not_run" and written["provenance"]["reason"] == "no key"

    def test_provenance_reads_the_commit_from_git(self) -> None:
        prov = evals.provenance("oracle", "m")
        assert len(prov["commit"]) in (40, len("unknown")) and len(prov["date"]) == 10

    def test_a_missing_git_is_unknown_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> Any:
            raise OSError("no git")

        monkeypatch.setattr(evals.subprocess, "run", boom)
        assert evals._git_commit() == "unknown"


class TestTheEvalsCommand:
    def test_runs_a_scripted_suite_into_a_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import shutil

        from disclosed.cli import main

        (tmp_path / "evals").mkdir()
        shutil.copytree(CASES, tmp_path / "evals" / "cases")
        (tmp_path / "data").symlink_to(ROOT / "data")
        (tmp_path / "corpus").symlink_to(ROOT / "corpus")
        assert (
            main(
                ["evals", "--suite", "drift_direction", "--kind", "oracle", "--root", str(tmp_path)]
            )
            == 0
        )
        assert (tmp_path / "evals" / "results" / "drift_direction.oracle.json").exists()
        assert "drift_direction [oracle]" in capsys.readouterr().out

    def test_live_without_a_provider_writes_not_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from disclosed.cli import main

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("DISCLOSED_ASK_PROVIDER", "anthropic")
        assert main(["evals", "--kind", "live", "--root", str(tmp_path)]) == 1
        written = sorted(p.name for p in (tmp_path / "evals" / "results").glob("*.json"))
        assert written == sorted(f"{s}.live.json" for s in evals.SUITES)
        assert "not run" in capsys.readouterr().out
