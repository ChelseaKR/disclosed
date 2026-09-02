"""Fail the build when a Lighthouse report breaks the timing budget the project publishes.

``lighthouse-budget.json`` carries three timing lines. Nothing enforced them, and this project's
own metrics ledger said so: ``Gate: NONE``. ``docs/adr/0008`` moved the resource sizes into
``make verify``, where a static checker can hold them from the built bytes, and left these three
where they were, because a layout shift and a blocking time are facts about a browser's main
thread and a paint time is a fact about a machine and a simulated network as much as about a
document. It also wrote down the precondition for gating them: measure what this job's own runner
reports, rather than calibrating a gate on somebody's laptop.

That measurement is in ``docs/adr/0010``. It came back within a millisecond of the laptop's, on
both pages, because Lighthouse's default throttling is simulated rather than applied, so this
script exists.

Three failures, and they are three different failures:

1. A metric over its budget. The thing the budget is for.
2. A metric the report does not carry. Lighthouse only collects timings when the performance
   category is requested, so a report audited for accessibility alone has no LCP in it at all.
   Treating that as a pass would mean the gate silently stopped applying the day somebody
   trimmed a ``--only-categories`` flag.
3. A report that is not there. The same failure mode the accessibility scorer in this workflow
   already refuses by name: a pass over a partial set is not a pass.

Usage: check_lighthouse_timings.py <budget.json> <report.json> [<report.json> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def timing_budgets(budget_path: Path) -> dict[str, float]:
    """The ``timings`` lines of the one path entry, as metric -> budget.

    Refuses a budget file with more than one path entry for the same reason
    ``tests/test_accessibility.py`` does: every page this job audits would otherwise be held to
    whichever entry happened to be first, which is a claim the budget file does not make.
    """
    entries = json.loads(budget_path.read_text(encoding="utf-8"))
    if len(entries) != 1:
        raise ValueError(
            f"{budget_path} carries {len(entries)} path entries; this gate applies one entry's "
            "timings to every audited page and cannot choose between two"
        )
    if entries[0].get("path") != "/*":
        raise ValueError(
            f"{budget_path} budgets the path {entries[0].get('path')!r} rather than every page"
        )
    return {line["metric"]: float(line["budget"]) for line in entries[0].get("timings", [])}


def _value(report: dict[str, Any], metric: str) -> float | None:
    """A metric's measured value, or ``None`` when the report does not carry the audit.

    ``None`` and never zero. A zero is what a perfect cumulative layout shift looks like, and
    reading an absent audit as one would turn a report that measured nothing into the best
    possible result.
    """
    audit = report.get("audits", {}).get(metric)
    if not isinstance(audit, dict):
        return None
    value = audit.get("numericValue")
    return float(value) if isinstance(value, int | float) else None


def check(budgets: dict[str, float], path: Path) -> list[str]:
    """Every way one report breaks the budget, in words. Empty means it is inside it."""
    if not path.is_file():
        return [f"{path} was never written; lighthouse did not audit that page"]
    report = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for metric, budget in sorted(budgets.items()):
        value = _value(report, metric)
        if value is None:
            problems.append(
                f"{path.name}: {metric} is not in the report. Lighthouse collects timings only "
                "when the performance category is requested, so this page is not being measured "
                "against the budget at all."
            )
        elif value > budget:
            problems.append(f"{path.name}: {metric} is {value:g} against a budget of {budget:g}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    budget_path = Path(argv[1])
    reports = [Path(arg) for arg in argv[2:]]
    try:
        budgets = timing_budgets(budget_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not budgets:
        print(
            f"error: {budget_path} carries no timing lines, so this gate would pass over every "
            "report without measuring anything. A gate that cannot fail is worse than no gate.",
            file=sys.stderr,
        )
        return 2

    problems: list[str] = []
    for report in reports:
        problems += check(budgets, report)
    if problems:
        for problem in problems:
            print(f"::error title=Timing budget::{problem}", file=sys.stderr)
        return 1
    metrics = ", ".join(sorted(budgets))
    print(f"{len(reports)} report(s) within the timing budget ({metrics})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
