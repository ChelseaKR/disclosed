"""The gate that holds a Lighthouse report to the timing budget this project publishes.

``.github/scripts/check_lighthouse_timings.py`` runs in CI, where nothing else in this suite can
watch it, so it is exercised here the way ``check_site_origin.py`` is: as the real script, over
real report shapes, broken one way at a time.

The reason it is worth this much attention is that it replaces a row in the metrics ledger that
read ``Gate: NONE``. A gate that replaces an honestly declared absence has to be a gate: it has
to go red on a page over budget, and it has to go red on a page it was never given the numbers
for, because the second is how a gate quietly stops applying.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / ".github" / "scripts" / "check_lighthouse_timings.py"
_BUDGET_FILE = _ROOT / "lighthouse-budget.json"


def _load() -> ModuleType:
    assert _SCRIPT.is_file(), (
        f"{_SCRIPT} is gone. It is the only thing holding a rendered page to the three timing "
        "lines of lighthouse-budget.json, which were enforced by nothing until docs/adr/0010."
    )
    spec = importlib.util.spec_from_file_location("check_lighthouse_timings", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def _report(**metrics: float) -> dict[str, Any]:
    """A Lighthouse report carrying exactly the audits named, in Lighthouse's own shape."""
    return {
        "lighthouseVersion": "12.8.2",
        "audits": {name: {"numericValue": value} for name, value in metrics.items()},
    }


def _written(tmp_path: Path, name: str, payload: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# What the runner reported on 2026-08-28, recorded in docs/adr/0010. The largest page in the
# site, which is the one this gate is really about.
_RUNNER_CA = {
    "largest-contentful-paint": 1052.3905,
    "cumulative-layout-shift": 0,
    "total-blocking-time": 0,
}


class TestTheBudgetItReads:
    def test_it_reads_the_committed_budget_file(self) -> None:
        """The numbers come out of the file, so widening the file widens the gate visibly."""
        budgets = check.timing_budgets(_BUDGET_FILE)
        assert budgets == {
            "largest-contentful-paint": 1500.0,
            "cumulative-layout-shift": 0.0,
            "total-blocking-time": 200.0,
        }

    def test_a_budget_file_with_two_path_entries_is_refused(self, tmp_path: Path) -> None:
        """Two entries mean some pages are budgeted differently, and this gate audits one set."""
        path = _written(tmp_path, "budget.json", [{"path": "/*"}, {"path": "/state/*"}])
        with pytest.raises(ValueError, match="2 path entries"):
            check.timing_budgets(path)

    def test_a_budget_file_that_does_not_budget_every_page_is_refused(self, tmp_path: Path) -> None:
        path = _written(tmp_path, "budget.json", [{"path": "/state/*", "timings": []}])
        with pytest.raises(ValueError, match="rather than every page"):
            check.timing_budgets(path)


class TestOneReport:
    def test_the_runners_own_numbers_pass(self, tmp_path: Path) -> None:
        """The measurement ADR 0010 gated on, run through the gate it produced."""
        report = _written(tmp_path, "CA.json", _report(**_RUNNER_CA))
        assert check.check(check.timing_budgets(_BUDGET_FILE), report) == []

    def test_a_metric_over_budget_is_reported_with_both_numbers(self, tmp_path: Path) -> None:
        report = _written(
            tmp_path, "CA.json", _report(**{**_RUNNER_CA, "largest-contentful-paint": 1500.1})
        )
        problems = check.check(check.timing_budgets(_BUDGET_FILE), report)
        assert problems == ["CA.json: largest-contentful-paint is 1500.1 against a budget of 1500"]

    def test_a_metric_exactly_at_its_budget_is_inside_it(self, tmp_path: Path) -> None:
        report = _written(
            tmp_path, "CA.json", _report(**{**_RUNNER_CA, "largest-contentful-paint": 1500.0})
        )
        assert check.check(check.timing_budgets(_BUDGET_FILE), report) == []

    def test_a_layout_shift_of_any_size_breaks_a_budget_of_zero(self, tmp_path: Path) -> None:
        report = _written(
            tmp_path, "CA.json", _report(**{**_RUNNER_CA, "cumulative-layout-shift": 0.01})
        )
        problems = check.check(check.timing_budgets(_BUDGET_FILE), report)
        assert problems == ["CA.json: cumulative-layout-shift is 0.01 against a budget of 0"]

    def test_the_blocking_time_the_second_run_reported_is_inside_the_budget(
        self, tmp_path: Path
    ) -> None:
        """Run 33139828844 reported 34 ms of total blocking time on the home page, against the
        0 ms this budget line was first set to from a single run that reported 0.

        The page ships no script at all -- ``resourceCounts.script`` is budgeted at 0 and held
        there statically -- so 34 ms is a shared runner's main thread, not this site's code. A
        budget set to a single observation of a noisy metric is the failure ADR 0008 wrote the
        rule about, and this is the case that proves the line is no longer set that way.
        """
        report = _written(
            tmp_path, "home.json", _report(**{**_RUNNER_CA, "total-blocking-time": 34})
        )
        assert check.check(check.timing_budgets(_BUDGET_FILE), report) == []

    def test_a_blocking_time_past_lighthouses_good_threshold_fails(self, tmp_path: Path) -> None:
        """200 ms is where Lighthouse itself stops scoring total blocking time as good. A
        document with no script that blocks the main thread for longer than that is a
        regression in the document, not runner noise."""
        report = _written(
            tmp_path, "CA.json", _report(**{**_RUNNER_CA, "total-blocking-time": 200.5})
        )
        problems = check.check(check.timing_budgets(_BUDGET_FILE), report)
        assert problems == ["CA.json: total-blocking-time is 200.5 against a budget of 200"]

    def test_a_report_without_the_audit_is_a_failure_and_not_a_pass(self, tmp_path: Path) -> None:
        """The way this gate would otherwise stop applying without anybody noticing.

        Lighthouse collects timings only when the performance category is requested. Drop
        ``performance`` from a page's ``--only-categories`` and the report still exists, still
        scores accessibility 1, and carries no LCP at all.
        """
        report = _written(tmp_path, "methodology.json", _report())
        problems = check.check(check.timing_budgets(_BUDGET_FILE), report)
        assert len(problems) == 3
        assert all("is not in the report" in problem for problem in problems)

    def test_a_missing_audit_is_never_read_as_a_zero(self, tmp_path: Path) -> None:
        """Zero is what a perfect layout shift looks like. An absent measurement is not that."""
        assert check._value({"audits": {"cumulative-layout-shift": {}}}, "x") is None
        assert check._value({"audits": {"x": {"numericValue": None}}}, "x") is None
        assert check._value({}, "x") is None

    def test_a_report_that_was_never_written_is_a_failure(self, tmp_path: Path) -> None:
        problems = check.check(check.timing_budgets(_BUDGET_FILE), tmp_path / "gone.json")
        assert problems == [
            f"{tmp_path / 'gone.json'} was never written; lighthouse did not audit that page"
        ]


class TestTheCommandItself:
    def test_it_exits_zero_over_reports_inside_the_budget(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        home = _written(
            tmp_path, "home.json", _report(**{**_RUNNER_CA, "largest-contentful-paint": 751.7})
        )
        state = _written(tmp_path, "CA.json", _report(**_RUNNER_CA))
        assert check.main(["prog", str(_BUDGET_FILE), str(home), str(state)]) == 0
        assert "2 report(s) within the timing budget" in capsys.readouterr().out

    def test_it_exits_one_and_annotates_every_page_over_budget(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        over = _written(
            tmp_path, "CA.json", _report(**{**_RUNNER_CA, "largest-contentful-paint": 4000})
        )
        assert check.main(["prog", str(_BUDGET_FILE), str(over)]) == 1
        assert "::error title=Timing budget::" in capsys.readouterr().err

    def test_a_budget_with_no_timing_lines_is_refused_rather_than_passed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The exact shape of the defect this gate replaces: a check with nothing to check.

        Delete the timing lines and every report is trivially inside the budget, which is the
        state ``lighthouse-budget.json`` was in for months while three documents called it a
        gate.
        """
        budget = _written(tmp_path, "budget.json", [{"path": "/*", "timings": []}])
        report = _written(tmp_path, "CA.json", _report(**_RUNNER_CA))
        assert check.main(["prog", str(budget), str(report)]) == 2
        assert "cannot fail is worse than no gate" in capsys.readouterr().err

    def test_too_few_arguments_prints_the_usage_and_exits_two(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert check.main(["prog", str(_BUDGET_FILE)]) == 2
        assert "Usage:" in capsys.readouterr().err

    def test_an_unreadable_budget_file_exits_two_rather_than_passing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        broken = tmp_path / "budget.json"
        broken.write_text("{not json", encoding="utf-8")
        report = _written(tmp_path, "CA.json", _report(**_RUNNER_CA))
        assert check.main(["prog", str(broken), str(report)]) == 2
        assert "error:" in capsys.readouterr().err
