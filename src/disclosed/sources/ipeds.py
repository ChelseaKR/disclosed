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

Two files are read, not one. The directory (``HD``) says who exists and what they published; the
institutional characteristics file (``IC``) says what kind of institution they are, and that is
what decides which disclosures they owe. The athletics disclosure was left ungraded in an earlier
pass for exactly this reason: 4,469 of 6,163 directory rows have no athletics address and almost
all of them are colleges with no athletics programme, so grading the column against the directory
alone would have manufactured four thousand violations. The characteristics file carries the
institution's own answer about whether it belongs to a national athletic association, which turns
an ungradeable column into a denominator of 1,998.
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

__all__ = [
    "BASE_URL",
    "DEFAULT_YEAR",
    "IpedsError",
    "characteristics_url",
    "directory_url",
    "load_directory",
    "load_institutions",
    "merge_characteristics",
]

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
    "ATHURL",
)

# Characteristics columns kept. ``ATHASSOC`` is the institution's own answer to "are you a member
# of a national athletic association", and it is the entire reason this file is fetched: it is the
# applicability rule for the athletics disclosure. The four ``SPORT`` columns are carried beside it
# so a reader can check that answer against the teams the institution reports fielding, and they
# are deliberately not part of the rule. Every institution reporting a team also reports an
# association, so ``ATHASSOC`` alone already covers all 1,334 of them and adding the sports columns
# to the condition would narrow the denominator without excusing anybody.
_CHARACTERISTICS_COLUMNS: Final[tuple[str, ...]] = (
    "UNITID",
    "ATHASSOC",
    "SPORT1",
    "SPORT2",
    "SPORT3",
    "SPORT4",
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


def characteristics_url(year: int = DEFAULT_YEAR) -> str:
    """URL of the institutional characteristics archive for a collection year."""
    return f"{BASE_URL}/IC{year}.zip"


def _control_as_int(raw: str) -> Any:
    """Return CONTROL as an int so it compares against the Scorecard's ownership codes.

    Only genuine sector codes are converted. The negative sentinels are left as the strings IPEDS
    sent, so that ``-3`` reaches the classifier as a suppression marker instead of arriving in
    :mod:`disclosed.crosswalk` as an integer sector that no institution has.
    """
    text = raw.strip()
    return int(text) if text.isdigit() else text


def _rows(archive: bytes, *, what: str) -> Iterator[dict[str, str]]:
    """Yield the rows of the single CSV inside an IPEDS archive.

    Args:
        archive: Raw bytes of the zip file.
        what: Name of the file for error messages, e.g. ``"directory"``.

    Raises:
        IpedsError: If the archive cannot be opened, holds no CSV, or has no UNITID column. A
            file that cannot be joined on unit id is a failure, not a source of zero institutions.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            names = [n for n in bundle.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise IpedsError(f"IPEDS {what} archive contains no CSV")
            # Revised files ship alongside the original as hd2023_rv.csv. Sorting puts the plain
            # name first; taking it keeps a rerun reproducible rather than silently switching
            # vintages when NCES adds a revision.
            body = bundle.read(sorted(names)[0])
    except zipfile.BadZipFile as exc:
        raise IpedsError(f"IPEDS {what} archive is not a readable zip: {exc}") from exc

    # IPEDS ships a UTF-8 BOM and a few Latin-1 institution names in the same file. Decoding
    # strictly would fail the whole run over one accented character in one college's name.
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "UNITID" not in reader.fieldnames:
        raise IpedsError(f"IPEDS {what} has no UNITID column; cannot join to anything")
    yield from reader


def parse_directory(archive: bytes) -> list[dict[str, Any]]:
    """Parse a downloaded HD archive into records.

    Args:
        archive: Raw bytes of the zip file.

    Raises:
        IpedsError: If the archive cannot be opened, holds no CSV, or lacks the expected columns.
    """
    records: list[dict[str, Any]] = []
    for row in _rows(archive, what="directory"):
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


def load_directory(*, year: int = DEFAULT_YEAR, cache: Path | None = None) -> list[dict[str, Any]]:
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
        # Waived for the reason stated at the same call in the Scorecard adapter: the scheme and
        # host come from BASE_URL and the only variable part is an integer collection year, so no
        # input can turn this into a `file://` read. Stated at the line rather than silenced by a
        # severity floor over the whole scan.
        # nosemgrep: dynamic-urllib-use-detected
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310
            archive: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IpedsError(f"IPEDS directory {url} unreadable: {exc}") from exc

    records = parse_directory(archive)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(archive)
    return records


def parse_characteristics(archive: bytes) -> dict[str, dict[str, Any]]:
    """Parse a downloaded IC archive, keyed by unit id.

    Returned as a mapping rather than a list because this file is never graded on its own. It
    exists to answer one question about an institution already in the directory, so the only
    useful shape is one you can look a unit id up in.

    Rows with a blank unit id are dropped rather than kept under ``""``. Two of them would collide
    on that key and one would silently answer for the other, which is how a finding once came to
    be published carrying a different institution's peer group.

    Raises:
        IpedsError: If the archive cannot be opened, holds no CSV, or has no UNITID column.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for row in _rows(archive, what="characteristics"):
        unit_id = (row.get("UNITID") or "").strip()
        if not unit_id:
            continue
        by_id.setdefault(
            unit_id,
            {
                f"ipeds.{column}": (row.get(column) or "").strip()
                for column in _CHARACTERISTICS_COLUMNS
            },
        )
    if not by_id:
        raise IpedsError("IPEDS characteristics file parsed to zero institutions")
    return by_id


def load_characteristics(
    *, year: int = DEFAULT_YEAR, cache: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Fetch and parse the institutional characteristics file, keyed by unit id."""
    if cache is not None and cache.exists():
        return parse_characteristics(cache.read_bytes())

    url = characteristics_url(year)
    try:
        # Same waiver, same reason: fixed scheme and host, one integer year.
        # nosemgrep: dynamic-urllib-use-detected
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310
            archive: bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IpedsError(f"IPEDS characteristics {url} unreadable: {exc}") from exc

    records = parse_characteristics(archive)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(archive)
    return records


def merge_characteristics(
    directory: list[dict[str, Any]], characteristics: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Copy the characteristics columns onto the directory records that have a counterpart.

    A directory row with no characteristics row is left exactly as it was, with the columns
    absent rather than blank. That distinction is the whole point. A blank ``ATHASSOC`` would read
    as "this institution says it has no athletics"; an absent one reads as "nothing was asked and
    nothing is known", and the applicability rules require a positive answer before they place any
    institution in a denominator. Filling the gap with a default would be this project inventing a
    fact out of an absence, one level above the null-versus-zero bug it exists to catch.

    In the 2023 collection 114 of 6,163 directory rows have no characteristics row. Eleven of them
    are operating institutions and none of those participate in Title IV, so nothing that follows
    from this join is currently load-bearing. It is written this way for the year when that stops
    being true.
    """
    merged: list[dict[str, Any]] = []
    for record in directory:
        unit_id = str(record.get("id", "")).strip()
        extra = characteristics.get(unit_id)
        merged.append({**record, **extra} if extra else dict(record))
    return merged


def load_institutions(
    *,
    year: int = DEFAULT_YEAR,
    cache: Path | None = None,
    characteristics_cache: Path | None = None,
) -> list[dict[str, Any]]:
    """Load the directory joined to the characteristics file: the record this project grades.

    Both files are required. If the characteristics file cannot be read the whole load fails,
    rather than returning directory records that would grade perfectly well and quietly drop the
    athletics disclosure out of every denominator in the country. A field that silently stops
    being graded is indistinguishable on the page from a field that everybody suddenly reports.

    Raises:
        IpedsError: If either file is unreadable.
    """
    directory = load_directory(year=year, cache=cache)
    characteristics = load_characteristics(year=year, cache=characteristics_cache)
    return merge_characteristics(directory, characteristics)


def iter_institutions(
    *, year: int = DEFAULT_YEAR, cache: Path | None = None, limit: int | None = None
) -> Iterator[dict[str, Any]]:
    """Yield directory records, matching the shape of the Scorecard adapter's iterator."""
    for index, record in enumerate(load_directory(year=year, cache=cache)):
        if limit is not None and index >= limit:
            return
        yield record
