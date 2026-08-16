"""The one CI step whose failure mode is silence, checked from the workflow file itself.

Most of this project's gates announce themselves: a test fails, a score is not 100, a fetch
raises rather than returning short. The daily snapshot commit is different. It runs on a
schedule nobody watches, its whole job is a side effect on the repository, and when it does
nothing it says so in a sentence that reads like good news.

It said that sentence ten times. Between 2026-08-06 and 2026-08-15 the workflow graded 6,273
institutions on every run, wrote a snapshot, and then decided there was nothing to commit,
because it asked ``git diff -- data/snapshots/`` whether the tree had changed. ``git diff``
compares the index against the working tree, and a brand-new untracked file is in neither, so
a new snapshot is invisible to it. Every one of those runs was green. Not one snapshot reached
the history, and the drift step reported "First Scorecard snapshot; nothing to compare against
yet" every single day.

The bug is a one-word fix and completely invisible in review, which is exactly why it is worth
a test. This asserts the two properties that make the step honest: it compares something a new
file can fail, and it checks afterwards that the commit actually happened.
"""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "snapshot.yml"
).read_text(encoding="utf-8")


def _commands() -> list[str]:
    """Every git invocation in the workflow, whitespace-normalized, in order."""
    return [" ".join(line.split()) for line in _WORKFLOW.splitlines() if " git " in f" {line} "]


class TestTheDailySnapshotCommitCanFail:
    def test_the_change_check_compares_the_index_and_not_the_working_tree(self) -> None:
        """``git diff`` without ``--cached`` cannot see an untracked file, and every daily
        snapshot is an untracked file. A check that can only return "unchanged" is not a check.
        """
        diffs = [c for c in _commands() if re.search(r"\bgit diff\b", c)]
        assert diffs, (
            "the snapshot workflow no longer compares anything before committing. If the step "
            "was rewritten, bring this test with it rather than deleting it: the failure this "
            "guards against was silent for ten consecutive green runs."
        )
        for command in diffs:
            assert "--cached" in command, (
                f"{command!r} compares the working tree against the index. A new snapshot is "
                "untracked, so it appears in neither and this reports 'unchanged' forever. "
                "Stage first and compare the index against HEAD with --cached."
            )

    def test_the_snapshot_is_staged_before_it_is_compared(self) -> None:
        """Order matters as much as the flag: ``--cached`` against an unstaged new file is just
        as blind. The ``git add`` has to come first."""
        sequence = _commands()
        added = next(i for i, c in enumerate(sequence) if re.search(r"\bgit add\b", c))
        compared = next(i for i, c in enumerate(sequence) if re.search(r"\bgit diff\b", c))
        assert added < compared, (
            "the workflow compares data/snapshots/ before staging it, so a new snapshot file is "
            "still untracked at the moment of the comparison and the step decides there is "
            "nothing to commit."
        )

    def test_the_step_proves_the_snapshot_reached_the_history(self) -> None:
        """The post-condition. Everything above is about one flag; this is the check that does
        not care how the commit was attempted, only whether the file is in the repository
        afterwards. It is the property the daily series actually needs."""
        assert re.search(r"git ls-files --error-unmatch", _WORKFLOW), (
            "nothing in the snapshot workflow verifies that the snapshot it wrote is tracked. "
            "Without that, any future rewrite of the commit step can go back to reporting "
            "success while committing nothing, which is what it did for ten days."
        )
