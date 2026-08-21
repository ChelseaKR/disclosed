"""College Scorecard adapter.

The API is public and paginated. ``DEMO_KEY`` works for small volumes and is what the test suite
and a first local run use; a free key from api.data.gov raises the rate limit and is read from
``DATA_GOV_API_KEY`` when present.

This adapter deliberately does no interpretation. It returns raw values exactly as the API sent
them, nulls included, and lets :mod:`disclosed.disclosure` decide what they mean. Coercing here
would put a second place in the codebase where null could quietly become zero.

A walk records its own provenance. Every page fetched is written down with the URL it came from
(the key redacted), when it was fetched, the HTTP status, the byte count, a SHA-256 of the body,
how many attempts it took, and the rate-limit headers the API returned. The record travels with
the records in a :class:`Capture`, which :func:`write_capture` serializes as an envelope the CLI
can replay without a key. The point is the same one the committed IPEDS archives make: a national
figure that only its author can regenerate is an assertion, and a capture whose provenance names
every call is evidence. A walk against api.data.gov is also a walk against someone else's budget,
so pages are fetched with a pause between them, ``Retry-After`` is honoured when the API sends
one, and a page cache lets a rerun proceed without touching the network at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ..fields import SCORECARD_API_FIELDS

__all__ = [
    "BASE_URL",
    "CAPTURE_KIND",
    "Capture",
    "PageRecord",
    "RateLimited",
    "ScorecardError",
    "fetch_page",
    "is_exhaustive",
    "iter_institutions",
    "read_capture",
    "summarize_capture",
    "walk",
    "write_capture",
]

BASE_URL: Final[str] = "https://api.data.gov/ed/collegescorecard/v1/schools"
_PER_PAGE: Final[int] = 100
_TIMEOUT: Final[float] = 45.0
_MAX_ATTEMPTS: Final[int] = 4
_BACKOFF_BASE: Final[float] = 2.0

# Seconds between consecutive network fetches. A signed-up key allows 1,000 requests an hour and
# a full walk is about 63, so the budget is never close; the pause is courtesy to a shared
# endpoint, not a necessity, and it is skipped for pages served from the cache.
_PAUSE: Final[float] = 0.5

# The longest the adapter will wait on the API's own say-so. A Retry-After of an hour is not a
# retry, it is a refusal, and it should surface as one rather than as a job that hangs.
_MAX_RETRY_AFTER: Final[float] = 120.0

CAPTURE_KIND: Final[str] = "college-scorecard-capture"
"""The ``kind`` a capture envelope carries, so a reader can tell it from a bare record list."""

_REDACTED: Final[str] = "REDACTED"


class ScorecardError(RuntimeError):
    """The API could not be read. Raised rather than returning partial data.

    A truncated fetch would understate disclosure across every institution that never arrived,
    which would look identical to a real reporting collapse. Failing loudly is the only safe
    behaviour for a project whose subject is missing data.
    """


class RateLimited(ScorecardError):
    """The API refused the request for rate reasons and retries did not clear it.

    Separated from the general error because the remedy is specific and worth saying out loud:
    ``DEMO_KEY`` allows roughly 30 requests an hour per address, which is about three pages. A free
    key from api.data.gov raises that to 1,000 an hour, which covers the full ~6,300 institutions
    comfortably.
    """


@dataclass(frozen=True, slots=True)
class PageRecord:
    """Where one page came from, and what arrived. One of these per page, cache hits included."""

    page: int
    url: str
    """The request URL with ``api_key`` replaced by ``REDACTED``. Never the real key."""

    fetched_at: str
    """UTC, second precision. For a cache hit, the time the cached copy was originally fetched."""

    status: int
    bytes: int
    sha256: str
    attempts: int
    from_cache: bool
    ratelimit_limit: int | None
    ratelimit_remaining: int | None
    """``None`` when the API sent no such header, never ``0``: an unreported limit is not an
    exhausted one."""


@dataclass(frozen=True, slots=True)
class Capture:
    """The records a walk returned and the provenance of every page that carried them."""

    records: list[dict[str, Any]]
    pages: list[PageRecord]
    total_stated: int | None
    """The last ``metadata.total`` the API sent, or ``None`` if no page carried one."""

    exhausted: bool
    """Whether the walk confirmed it reached the API's own total. False for any limited walk."""

    limit: int | None
    walked_at: str
    finished_at: str
    demo_key: bool

    @property
    def calls(self) -> int:
        """Network requests made, excluding pages served from the cache and excluding retries."""
        return sum(1 for p in self.pages if not p.from_cache)

    def provenance(self) -> dict[str, Any]:
        return {
            "base_url": BASE_URL,
            "fields": list(SCORECARD_API_FIELDS),
            "per_page": _PER_PAGE,
            "walked_at": self.walked_at,
            "finished_at": self.finished_at,
            "demo_key": self.demo_key,
            "limit": self.limit,
            "total_stated": self.total_stated,
            "records": len(self.records),
            "exhausted": self.exhausted,
            "calls": self.calls,
            "pages": [asdict(p) for p in self.pages],
        }


def _api_key() -> str:
    return os.environ.get("DATA_GOV_API_KEY", "DEMO_KEY")


def _using_demo_key() -> bool:
    return _api_key() == "DEMO_KEY"


def _sleep(seconds: float) -> None:
    # Indirected so tests can neutralize the backoff without patching the stdlib globally.
    time.sleep(seconds)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _url(page: int, per_page: int) -> str:
    query = urllib.parse.urlencode(
        {
            "api_key": _api_key(),
            "per_page": per_page,
            "page": page,
            "fields": ",".join(SCORECARD_API_FIELDS),
        }
    )
    return f"{BASE_URL}?{query}"


def _redact(url: str) -> str:
    """The URL as it may be written down: everything except the key.

    Done by parsing the query rather than by string replacement, so a key that happened to be a
    substring of a field name, or a future second credential parameter, could not slip through.
    """
    parts = urllib.parse.urlsplit(url)
    query = [
        (name, _REDACTED if name == "api_key" else value)
        for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _int_header(headers: Any, name: str) -> int | None:
    value = headers.get(name) if headers is not None else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _retry_delay(headers: Any, attempt: int) -> float:
    """How long to wait before trying a page again.

    The API's own ``Retry-After`` wins when it sends one in seconds and it is not absurd;
    otherwise exponential backoff from ``_BACKOFF_BASE``. Honouring the header is what
    "respecting the rate limit" means in practice, rather than guessing at it.
    """
    retry_after = headers.get("Retry-After") if headers is not None else None
    if isinstance(retry_after, str) and retry_after.strip().isdigit():
        return min(float(retry_after), _MAX_RETRY_AFTER)
    return _BACKOFF_BASE * float(2 ** (attempt - 1))


def _fetch_bytes(url: str, *, page: int, attempts: int) -> tuple[bytes, int, Any, int]:
    """Fetch one URL, retrying transient rate limits. Returns body, status, headers, attempts.

    Retries are bounded and only cover conditions that plausibly clear on their own: HTTP 429 and
    5xx. A 4xx that is not 429 is a request we got wrong and will keep getting wrong, so it fails
    immediately rather than burning the caller's time proving it.
    """
    last_status: int | None = None
    for attempt in range(1, attempts + 1):
        try:
            # Two scanners flag this call for the same reason -- urllib honours `file://`, so a
            # caller-controlled URL could read a local path -- and one fact answers both: the
            # scheme and host are fixed in BASE_URL, and everything after the `?` is urlencoded
            # into the query string, so nothing reachable from outside this module can change
            # what is fetched or how. Waived here, at the line, rather than by raising the
            # severity floor of the whole scan: a waiver a reviewer can see is a decision, and a
            # threshold that hides every finding the scan has is not.
            # nosemgrep: dynamic-urllib-use-detected
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310
                body: bytes = response.read()
                status = int(getattr(response, "status", 200) or 200)
                return body, status, response.headers, attempt
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            if exc.code != 429 and exc.code < 500:
                raise ScorecardError(
                    f"College Scorecard page {page} rejected the request: HTTP {exc.code}"
                ) from exc
            if attempt == attempts:
                break
            _sleep(_retry_delay(exc.headers, attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ScorecardError(f"College Scorecard page {page} unreadable: {exc}") from exc

    hint = (
        " Set DATA_GOV_API_KEY to a free key from api.data.gov; DEMO_KEY allows only about three "
        "pages an hour."
        if _using_demo_key()
        else ""
    )
    raise RateLimited(
        f"College Scorecard page {page} still returning HTTP {last_status} after {attempts} "
        f"attempts.{hint}"
    )


def _cache_paths(cache_dir: Path, page: int) -> tuple[Path, Path]:
    return cache_dir / f"page-{page:04d}.json", cache_dir / f"page-{page:04d}.meta.json"


def _from_cache(cache_dir: Path | None, page: int) -> tuple[bytes, dict[str, Any]] | None:
    """A page's cached body and the provenance recorded when it was fetched, or ``None``.

    Both files have to be there. A body without its provenance would be a page that cannot say
    when it was fetched, and writing a guess into the record would be the wrong kind of filling
    in for a project about blanks.
    """
    if cache_dir is None:
        return None
    body_path, meta_path = _cache_paths(cache_dir, page)
    if not (body_path.is_file() and meta_path.is_file()):
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        return None
    return body_path.read_bytes(), meta


def _to_cache(cache_dir: Path | None, page: int, body: bytes, meta: dict[str, Any]) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    body_path, meta_path = _cache_paths(cache_dir, page)
    body_path.write_bytes(body)
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def _read_page(
    page: int, *, per_page: int, attempts: int, cache_dir: Path | None
) -> tuple[dict[str, Any], PageRecord]:
    """One page as a parsed payload, with the record of where it came from."""
    url = _url(page, per_page)
    cached = _from_cache(cache_dir, page)
    if cached is not None:
        body, meta = cached
    else:
        body, status, headers, used = _fetch_bytes(url, page=page, attempts=attempts)
        meta = {
            "fetched_at": _now(),
            "status": status,
            "attempts": used,
            "ratelimit_limit": _int_header(headers, "X-RateLimit-Limit"),
            "ratelimit_remaining": _int_header(headers, "X-RateLimit-Remaining"),
        }
        _to_cache(cache_dir, page, body, meta)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ScorecardError(f"College Scorecard page {page} unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScorecardError(f"College Scorecard page {page} returned a non-object payload")

    record = PageRecord(
        page=page,
        url=_redact(url),
        fetched_at=str(meta.get("fetched_at", "")),
        status=int(meta.get("status", 0)),
        bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        attempts=int(meta.get("attempts", 0)),
        from_cache=cached is not None,
        ratelimit_limit=meta.get("ratelimit_limit"),
        ratelimit_remaining=meta.get("ratelimit_remaining"),
    )
    return payload, record


def fetch_page(
    page: int, *, per_page: int = _PER_PAGE, attempts: int = _MAX_ATTEMPTS
) -> dict[str, Any]:
    """Fetch one page of institutions, retrying transient rate limits.

    Raises:
        RateLimited: If 429 persists across every attempt.
        ScorecardError: On any other transport or decode failure.
    """
    payload, _ = _read_page(page, per_page=per_page, attempts=attempts, cache_dir=None)
    return payload


def _stated_total(payload: dict[str, Any]) -> int | None:
    metadata = payload.get("metadata")
    total = metadata.get("total") if isinstance(metadata, dict) else None
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _take(records: list[dict[str, Any]], results: list[Any], limit: int | None) -> None:
    for item in results:
        if isinstance(item, dict):
            records.append(item)
            if limit is not None and len(records) >= limit:
                return


def _short_walk(page: int, seen: int, total: int | None) -> ScorecardError:
    total_desc = (
        f"the API's stated total of {total}"
        if total is not None
        else "an unknown total; this page carried no usable metadata either"
    )
    return ScorecardError(
        f"College Scorecard page {page} returned no usable results after {seen} institutions, "
        f"short of {total_desc}. A full walk that cannot confirm it reached the end is reported "
        "as a failure, not as a national count."
    )


def walk(
    *,
    limit: int | None = None,
    cache_dir: Path | None = None,
    pause: float = _PAUSE,
    attempts: int = _MAX_ATTEMPTS,
) -> Capture:
    """Page the API to exhaustion, or to ``limit``, recording the provenance of every page.

    Args:
        limit: Stop after this many records. ``None`` walks the full ~6,300 institutions.
        cache_dir: Directory of per-page bodies and their provenance. A page found there is
            served from disk and recorded as such; a page fetched is written there. ``None``
            disables the cache.
        pause: Seconds to wait between consecutive network fetches.
        attempts: Retries per page for 429 and 5xx.

    Raises:
        ScorecardError: ``limit`` is ``None`` and a page comes back with missing or empty
            ``results`` before the API's own ``metadata.total`` says every institution has
            arrived. A well-formed HTTP 200 that carries nothing is not evidence a national walk
            is finished; the caller asked to be paged to exhaustion and a page that cannot confirm
            that happened is the same defect as a page that never arrived at all. This cannot fire
            when ``limit`` is given: a caller who asked to stop early gets exactly the records that
            arrived before wherever the walk stopped, same as always.
    """
    walked_at = _now()
    records: list[dict[str, Any]] = []
    pages: list[PageRecord] = []
    last_total: int | None = None
    exhausted = False
    page = 0
    while True:
        if pages and not pages[-1].from_cache and pause > 0:
            _sleep(pause)
        payload, record = _read_page(
            page, per_page=_PER_PAGE, attempts=attempts, cache_dir=cache_dir
        )
        pages.append(record)
        total = _stated_total(payload)
        last_total = total if total is not None else last_total
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            exhausted = total is not None and len(records) >= total
            if limit is None and not exhausted:
                raise _short_walk(page, len(records), total)
            break
        _take(records, results, limit)
        if total is not None and len(records) >= total:
            exhausted = True
            break
        if limit is not None and len(records) >= limit:
            break
        page += 1
    return Capture(
        records=records,
        pages=pages,
        total_stated=last_total,
        exhausted=exhausted,
        limit=limit,
        walked_at=walked_at,
        finished_at=_now(),
        demo_key=_using_demo_key(),
    )


def iter_institutions(*, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield institution records, paging until the API is exhausted or ``limit`` is reached.

    The streaming face of :func:`walk`, kept for callers that want records and not provenance.
    It raises exactly where :func:`walk` raises.
    """
    yield from walk(limit=limit).records


def _record_lines(records: list[dict[str, Any]]) -> str:
    # One record per line, compact, keys sorted: a diff between two captures then names the
    # institutions that moved rather than reflowing six thousand lines of indentation.
    return ",\n".join(
        "    " + json.dumps(record, sort_keys=True, separators=(",", ":")) for record in records
    )


def write_capture(capture: Capture, path: Path) -> None:
    """Write a capture as an envelope the CLI can replay: ``kind``, ``provenance``, ``records``.

    The provenance is indented so a reader can see it; the records are one per line so the file
    stays greppable and a diff between two captures is a list of institutions. A key is never
    written: every URL in the envelope has already been through :func:`_redact`.
    """
    head = json.dumps(
        {"kind": CAPTURE_KIND, "provenance": capture.provenance()}, indent=2, sort_keys=True
    )
    assert head.endswith("\n}")  # noqa: S101 -- the splice below depends on json's layout
    text = f'{head[:-2]},\n  "records": [\n{_record_lines(capture.records)}\n  ]\n}}\n'
    path.write_text(text, encoding="utf-8")


def read_capture(raw: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Records and provenance from a parsed source file.

    Accepts both shapes the project has written: a bare JSON array of records, which is what
    ``data/sample.json`` is and which carries no provenance, and the envelope
    :func:`write_capture` produces. Anything else is refused rather than read as zero
    institutions.

    Raises:
        ScorecardError: The value is neither shape.
    """
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)], None
    if isinstance(raw, dict) and raw.get("kind") == CAPTURE_KIND:
        provenance = raw.get("provenance")
        records = raw.get("records")
        if isinstance(provenance, dict) and isinstance(records, list):
            return [r for r in records if isinstance(r, dict)], provenance
    raise ScorecardError(
        "not a JSON array of records and not a capture envelope written by `disclosed fetch`"
    )


def is_exhaustive(provenance: dict[str, Any], record_count: int) -> bool:
    """Whether a replayed capture may be graded as the whole population.

    Every condition has to hold: the walk said it was exhausted, the API stated a total, and
    that total equals both the count the provenance recorded and the count of records actually
    present in the file. A capture that was truncated after it was written, or one whose
    provenance was edited, fails the last comparison and is graded as a sample. The ``limit``
    the caller passed is deliberately not consulted: a limit above the population changes
    nothing about what arrived, and the evidence is the three counts agreeing.
    """
    total = provenance.get("total_stated")
    return (
        provenance.get("exhausted") is True
        and isinstance(total, int)
        and total == record_count == provenance.get("records")
    )


def summarize_capture(capture: Capture, written: Path) -> dict[str, Any]:
    """The provenance small enough to commit beside a daily snapshot.

    The per-page URL is the same template every time and the rate-limit headers only matter at
    their lowest point, so the summary keeps one URL, the worst remaining budget, and per page
    only what identifies the bytes: status, size, digest. The capture file's own digest is here
    so a reader holding the file can check it is the one this summary describes.
    """
    body = written.read_bytes()
    remaining = [p.ratelimit_remaining for p in capture.pages if p.ratelimit_remaining is not None]
    limits = [p.ratelimit_limit for p in capture.pages if p.ratelimit_limit is not None]
    return {
        "walked_at": capture.walked_at,
        "finished_at": capture.finished_at,
        "url_template": _redact(_url(0, _PER_PAGE)).replace("page=0", "page=N"),
        "calls": capture.calls,
        "total_stated": capture.total_stated,
        "records": len(capture.records),
        "exhausted": capture.exhausted,
        "demo_key": capture.demo_key,
        "capture_bytes": len(body),
        "capture_sha256": hashlib.sha256(body).hexdigest(),
        "ratelimit_limit": limits[0] if limits else None,
        "ratelimit_remaining_min": min(remaining) if remaining else None,
        "pages": [
            {"page": p.page, "status": p.status, "bytes": p.bytes, "sha256": p.sha256}
            for p in capture.pages
        ],
    }
