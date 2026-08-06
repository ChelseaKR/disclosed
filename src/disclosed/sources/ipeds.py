"""IPEDS directory-file adapter.

IPEDS publishes its institutional directory as a public zip archive per collection year, with no
key, no quota, and no terms to accept. That makes it the natural second source: it carries public
disclosures the College Scorecard does not, and because both are keyed on the same federal unit id
it lets the same institution be checked against two arms of the same department.

Like the Scorecard adapter this one does no interpretation. IPEDS marks absence with negative
integer codes that mean three different things (-1 not reported, -2 not applicable, -3 not
available) and with blank text columns, and every one of those is passed through untouched for
:mod:`disclosed.disclosure` to rule on. Translating them here would put a second place in the
codebase where an absence could quietly become a number, and these particular absences are
unusually easy to get wrong: read naively, ``-2`` is a perfectly good float.

Columns are emitted under an ``ipeds.`` prefix so that a record from this source can never be
confused with a Scorecard record, plus a small set of identity aliases (``id``, ``school.name``,
``school.state``, ``school.ownership``) under the names the rest of the codebase already uses.
Those aliases are a rename and nothing more: a blank name stays blank rather than becoming a
placeholder, and a suppressed control code stays suppressed rather than becoming a sector.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

__all__ = ["BASE_URL", "DEFAULT_YEAR", "IpedsError", "directory_url", "load_directory"]

BASE_URL: Final[str] = "https://nces.ed.gov/ipeds/datacenter/data"

# The most recent directory collection confirmed to download and parse. Pinned rather than
# computed from today's date: IPEDS publishes a year's file months after the year ends, so
# deriving it from the clock would make the project start failing on 1 January every year.
DEFAULT_YEAR: Final[int] = 2023

_TIMEOUT: Final[float] = 90.0

# Directory columns kept. The full file has 74 of them and most are administrative; carrying only
# what is graded or used for applicability keeps a cached record small enough to commit.
_COLUMNS: Final[tuple[str, ...]] = (
    "UNITID",
    "INSTNM",
    "STABBR",
    "CONTROL",
    "ICLEVEL",
    "SECTOR",
    "INSTCAT",
    "UGOFFER",
    "CYACTIVE",
    "PSET4FLG",
    "WEBADDR",
    "NPRICURL",
    "FAIDURL",
    "ADMINURL",
    "DISAURL",
)

# IPEDS ships the identity fields under different names than the Scorecard. Aliasing them lets one
# grader, one peer-group function and one site renderer serve both sources. CONTROL and
# school.ownership genuinely share a vocabulary (1 public, 2 private nonprofit, 3 private
# for-profit), which is what makes the cross-source comparison in disclosed.crosswalk meaningful.
_IDENTITY_ALIASES: Final[tuple[tuple[str, str], ...]] = (
    ("UNITID", "id"),
    ("INSTNM", "school.name"),
    ("STABBR", "school.state"),
    ("CONTROL", "school.ownership"),
)


class IpedsError(RuntimeError):
    """The directory file could not be read. Raised rather than returning partial data.

    Same reasoning as :class:`disclosed.sources.college_scorecard.ScorecardError`: a truncated
    directory would understate disclosure across every institution that never arrived, which is
    indistinguishable on the page from a real collapse in reporting.
    """


def directory_url(year: int = DEFAULT_YEAR) -> str:
    """URL of the institutional directory archive for a collection year."""
    return f"{BASE_URL}/HD{year}.zip"


def _control_as_int(raw: str) -> Any:
    """Return CONTROL as an int so it compares against the Scorecard's ownership codes.

    Only genuine sector codes are converted. The negative sentinels are left as the strings IPEDS
    sent, so that ``-3`` reaches the classifier as a suppression marker instead of arriving in
    :mod:`disclosed.crosswalk` as an integer sector that no institution has.
    """
    text = raw.strip()
    return int(text) if text.isdigit() else text


def parse_directory(archive: bytes) -> list[dict[str, Any]]:
    """Parse a downloaded HD archive into records.

    Args:
        archive: Raw bytes of the zip file.

    Raises:
        IpedsError: If the archive cannot be opened, holds no CSV, or lacks the expected columns.
            A directory missing UNITID cannot be joined to anything and is a failure, not a source
            of zero institutions.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            names = [n for n in bundle.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise IpedsError("IPEDS archive contains no CSV")
            # Revised files ship alongside the original as hd2023_rv.csv. Sorting puts the plain
            # name first; taking it keeps a rerun reproducible rather than silently switching
            # vintages when NCES adds a revision.
            body = bundle.read(sorted(names)[0])
    except zipfile.BadZipFile as exc:
        raise IpedsError(f"IPEDS archive is not a readable zip: {exc}") from exc

    # IPEDS ships a UTF-8 BOM and a few Latin-1 institution names in the same file. Decoding
    # strictly would fail the whole run over one accented character in one college's name.
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "UNITID" not in reader.fieldnames:
        raise IpedsError("IPEDS directory has no UNITID column; cannot join to anything")

    records: list[dict[str, Any]] = []
    for row in reader:
        record: dict[str, Any] = {
            f"ipeds.{column}": (row.get(column) or "").strip() for column in _COLUMNS
        }
        for column, alias in _IDENTITY_ALIASES:
            value = (row.get(column) or "").strip()
            record[alias] = _control_as_int(value) if column == "CONTROL" else value
        records.append(record)
    if not records:
        raise IpedsError("IPEDS directory parsed to zero institutions")
    return records


def load_directory(
    *, year: int = DEFAULT_YEAR, cache: Path | None = None
) -> list[dict[str, Any]]:
    """Fetch and parse the institutional directory, using a cached archive when available.

    Args:
        year: Collection year. See :data:`DEFAULT_YEAR` for why this is not derived from today.
        cache: Path to hold the downloaded archive. Read from when it exists and written to
            otherwise, so repeated local runs and CI do not re-download 1.1 MB from NCES for no
            reason and a run stays reproducible from an archive on disk.

    Raises:
        IpedsError: On any transport or parse failure.
    """
    if cache is not None and cache.exists():
        return parse_directory(cache.read_bytes())

    url = directory_url(year)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310
            archive: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IpedsError(f"IPEDS directory {url} unreadable: {exc}") from exc

    records = parse_directory(archive)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(archive)
    return records


def iter_institutions(
    *, year: int = DEFAULT_YEAR, cache: Path | None = None, limit: int | None = None
) -> Iterator[dict[str, Any]]:
    """Yield directory records, matching the shape of the Scorecard adapter's iterator."""
    for index, record in enumerate(load_directory(year=year, cache=cache)):
        if limit is not None and index >= limit:
            return
        yield record
