"""The committed snapshot series, assembled into one history per source.

:mod:`disclosed.drift` compares two runs. This reads the whole committed series off disk and
groups it so that a page can show what a field's reporting rate did across every run there is,
which is the measurement this project argues is its most distinctive and the one that until now
existed only in a job summary and a terminal.

Three rules, and all three are :mod:`disclosed.drift`'s rules rather than new ones.

**A series is keyed on the source, never on the directory.** ``data/snapshots/scorecard`` and
``data/snapshots/ipeds`` happen to hold one source each, but the directory is a filing
convention and the source is a fact the snapshot carries. Grouping on the path would let a
misfiled run join a series it is not part of, and the resulting table would read as a
population that never existed.

**An unstated source is its own series and matches nothing.** ``Snapshot.source`` is empty for
runs written before scope existed. Folding those into the nearest named series would be a guess
presented as a fact, so they are grouped together, under wording that says the source was not
recorded, and they are never compared against a named run -- which is also what
:func:`drift.compare` already does with an empty source.

**Two runs cannot both be the same run.** A source whose series carries the same ``taken`` twice
cannot be ordered, and the ordering is the whole point of a history. It is refused rather than
resolved by an arbitrary tiebreak, because either file might be the real one and this module has
no way to tell.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .drift import FieldDrift, Snapshot, compare

__all__ = ["HistoryError", "SnapshotSeries", "load", "series_from_snapshots"]


class HistoryError(ValueError):
    """A committed series that cannot be read as a history.

    Raised rather than worked around, for the reason every other refusal in this project is: a
    history rendered from a series this module had to guess about would look exactly like one it
    did not.
    """


@dataclass(frozen=True, slots=True)
class SnapshotSeries:
    """Every committed run of one source, oldest first."""

    source: str
    """The publisher every run in this series named, or ``""`` when none of them did."""

    snapshots: tuple[Snapshot, ...]

    @property
    def stated(self) -> bool:
        """Whether the runs in this series say who published them."""
        return bool(self.source)

    @property
    def labels(self) -> tuple[str, ...]:
        """Every field graded in any run of this series, in a fixed order.

        The union rather than the intersection. A field that appears part-way through the series
        is a change in this project's graded set, and dropping it from the table would hide that
        the series is not uniform; carrying it, with the runs that never graded it saying so,
        shows it. :func:`drift.compare` still refuses to *compare* such a field, for the same
        reason it always has.
        """
        seen: set[str] = set()
        for snap in self.snapshots:
            seen.update(snap.reported)
        return tuple(sorted(seen))

    def graded(self, label: str, snapshot: Snapshot) -> bool:
        """Whether a run graded a field at all.

        Distinct from :meth:`drift.Snapshot.rate` returning ``None``: a field the run never
        graded and a field the run graded and could not measure are two different absences, and a
        table that printed the same cell for both would have collapsed them.
        """
        return label in snapshot.reported

    def movement(self) -> tuple[FieldDrift, ...]:
        """What moved between the first run and the last, as :func:`drift.compare` states it.

        The span rather than the most recent step, because the most recent step is one day of a
        seventeen-day series and calling it "the history" would be the smallest available claim
        wearing the largest available heading. Empty for a series of fewer than two runs: one run
        is not a comparison, and there is no honest way to render it as one.
        """
        if len(self.snapshots) < 2:
            return ()
        return compare(self.snapshots[0], self.snapshots[-1])


def series_from_snapshots(snapshots: Iterable[Snapshot]) -> tuple[SnapshotSeries, ...]:
    """Group loaded snapshots into one series per source, named sources first.

    Named before unnamed, and alphabetical within that, so the page order is a property of the
    data and not of the order the filesystem handed them over.

    Raises:
        HistoryError: If one source carries two runs with the same ``taken``.
    """
    by_source: dict[str, list[Snapshot]] = {}
    for snap in snapshots:
        by_source.setdefault(snap.source, []).append(snap)

    out: list[SnapshotSeries] = []
    for source in sorted(by_source, key=lambda s: (s == "", s)):
        runs = sorted(by_source[source], key=lambda s: s.taken)
        taken = [s.taken for s in runs]
        duplicated = sorted({t for t in taken if taken.count(t) > 1})
        if duplicated:
            named = source or "an unstated source"
            raise HistoryError(
                f"{named} has more than one snapshot taken {', '.join(duplicated)}; "
                "two runs cannot both be the same run, and there is no way to tell from here "
                "which of them is the one the series should carry"
            )
        out.append(SnapshotSeries(source=source, snapshots=tuple(runs)))
    return tuple(out)


def _read(path: Path) -> Snapshot:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise HistoryError(f"{path} is not a snapshot object")
    known = {f for f in Snapshot.__slots__}
    unknown = sorted(set(raw) - known)
    if unknown:
        # A key this build does not know is a snapshot written by a version that recorded
        # something more, and quietly dropping it would mean rendering a partial reading of a
        # file as a whole one. Refused, the way the catalog parser refuses a line it cannot read.
        raise HistoryError(f"{path} carries keys this build does not know: {', '.join(unknown)}")
    try:
        return Snapshot(**raw)
    except TypeError as exc:  # pragma: no cover - argument shape, restated for the caller
        raise HistoryError(f"{path} is not a snapshot: {exc}") from None


def load(root: Path) -> tuple[SnapshotSeries, ...]:
    """Read every committed snapshot under ``root`` and group it into series.

    The glob is exactly ``<root>/*/*.json`` -- one directory of runs per collection, and no
    deeper. The daily workflow writes a provenance sidecar for every Scorecard run into
    ``scorecard/provenance/``, and a recursive walk would load each of those as though it were a
    snapshot. That is the same shape as the defect this project exists to name: a file that is
    *about* a run read as though it were the run.

    Args:
        root: A directory of per-source directories, such as ``data/snapshots``.

    Returns:
        One series per source, named sources first. Empty when ``root`` holds no snapshots at
        all, which callers render as the absence of a history rather than as a history of
        nothing.

    Raises:
        HistoryError: If a file under the glob is not a snapshot, or if a source carries two runs
            with the same ``taken``.
    """
    return series_from_snapshots(_read(path) for path in sorted(root.glob("*/*.json")))
