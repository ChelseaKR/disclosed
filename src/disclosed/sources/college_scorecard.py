"""College Scorecard adapter.

The API is public and paginated. ``DEMO_KEY`` works for small volumes and is what the test suite
and a first local run use; a free key from api.data.gov raises the rate limit and is read from
``DATA_GOV_API_KEY`` when present.

This adapter deliberately does no interpretation. It returns raw values exactly as the API sent
them, nulls included, and lets :mod:`disclosed.disclosure` decide what they mean. Coercing here
would put a second place in the codebase where null could quietly become zero.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any, Final

from ..fields import SCORECARD_API_FIELDS

__all__ = ["BASE_URL", "RateLimited", "ScorecardError", "fetch_page", "iter_institutions"]

BASE_URL: Final[str] = "https://api.data.gov/ed/collegescorecard/v1/schools"
_PER_PAGE: Final[int] = 100
_TIMEOUT: Final[float] = 45.0
_MAX_ATTEMPTS: Final[int] = 4
_BACKOFF_BASE: Final[float] = 2.0


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


def _api_key() -> str:
    return os.environ.get("DATA_GOV_API_KEY", "DEMO_KEY")


def _using_demo_key() -> bool:
    return _api_key() == "DEMO_KEY"


def _sleep(seconds: float) -> None:
    # Indirected so tests can neutralize the backoff without patching the stdlib globally.
    time.sleep(seconds)


def fetch_page(
    page: int, *, per_page: int = _PER_PAGE, attempts: int = _MAX_ATTEMPTS
) -> dict[str, Any]:
    """Fetch one page of institutions, retrying transient rate limits.

    Retries are bounded and only cover conditions that plausibly clear on their own: HTTP 429 and
    5xx. A 4xx that is not 429 is a request we got wrong and will keep getting wrong, so it fails
    immediately rather than burning the caller's time proving it.

    Raises:
        RateLimited: If 429 persists across every attempt.
        ScorecardError: On any other transport or decode failure.
    """
    query = urllib.parse.urlencode(
        {
            "api_key": _api_key(),
            "per_page": per_page,
            "page": page,
            "fields": ",".join(SCORECARD_API_FIELDS),
        }
    )
    url = f"{BASE_URL}?{query}"

    last_status: int | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            if exc.code != 429 and exc.code < 500:
                raise ScorecardError(
                    f"College Scorecard page {page} rejected the request: HTTP {exc.code}"
                ) from exc
            if attempt == attempts - 1:
                break
            _sleep(_BACKOFF_BASE * (2**attempt))
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ScorecardError(f"College Scorecard page {page} unreadable: {exc}") from exc

        if not isinstance(payload, dict):
            raise ScorecardError(f"College Scorecard page {page} returned a non-object payload")
        return payload

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


def iter_institutions(*, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield institution records, paging until the API is exhausted or ``limit`` is reached.

    Args:
        limit: Stop after this many records. ``None`` walks the full ~6,300 institutions.

    Raises:
        ScorecardError: ``limit`` is ``None`` and a page comes back with missing or empty
            ``results`` before the API's own ``metadata.total`` says every institution has
            arrived. A well-formed HTTP 200 that carries nothing is not evidence a national walk
            is finished; the caller asked to be paged to exhaustion and a page that cannot confirm
            that happened is the same defect as a page that never arrived at all. This cannot fire
            when ``limit`` is given: a caller who asked to stop early gets exactly the records that
            arrived before wherever the walk stopped, same as always.
    """
    seen = 0
    page = 0
    while True:
        payload = fetch_page(page)
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            metadata = payload.get("metadata")
            total = metadata.get("total") if isinstance(metadata, dict) else None
            confirmed_exhausted = isinstance(total, int) and seen >= total
            if limit is None and not confirmed_exhausted:
                total_desc = f"the API's stated total of {total}" if isinstance(total, int) else (
                    "an unknown total; this page carried no usable metadata either"
                )
                raise ScorecardError(
                    f"College Scorecard page {page} returned no usable results after {seen} "
                    f"institutions, short of {total_desc}. A full walk that cannot confirm it "
                    "reached the end is reported as a failure, not as a national count."
                )
            return
        for record in results:
            if isinstance(record, dict):
                yield record
                seen += 1
                if limit is not None and seen >= limit:
                    return
        metadata = payload.get("metadata")
        total = metadata.get("total") if isinstance(metadata, dict) else None
        if isinstance(total, int) and seen >= total:
            return
        page += 1
