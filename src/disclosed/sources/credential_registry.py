"""Credential Registry (CTDL) adapter, written to measure a join and not yet to grade anything.

The registry is public and unauthenticated. ``GET /ce-registry/search?resource_type=organization``
answers HTTP 200 with the count in an ``x-total`` header and one JSON envelope per organization,
each carrying a decoded CTDL graph. No key, no quota, no crawl directives (``/robots.txt`` 404s).

What this adapter does **not** do is grade. ``docs/ROADMAP.md`` states the prerequisite the
project set itself before any Credential Registry adapter is designed: measure whether registry
organizations can be joined to the two federal corpora at all. A grader built on a join nobody
measured would publish per-institution findings for whichever institutions happened to match, and
the institutions that did not match would be indistinguishable on the page from institutions that
disclosed nothing. That is the project's own defect class, so the join comes first and the
grading contract is untouched by this module.

So the walk keeps only what a join needs, and the reduction is deliberately narrow:

- ``ceterms:ipedsID`` and ``ceterms:opeID``, which are first-class CTDL properties and the only
  federal identifiers the registry publishes in a typed field. Everything else an organization
  puts in ``ceterms:identifier`` is a free-text pair (``"Provider Unique ID"``, ``"NCES LEAID"``,
  ``"IPEDS NCES Data Year"``); the last of those is a *year*, not a unit id, and reading it as one
  is exactly how a join rate gets overstated.
- ``ceterms:agentType``, because the population that could join is postsecondary institutions and
  not the training providers that are most of the registry.
- the host of ``ceterms:subjectWebpage``, because IPEDS publishes an institution web address for
  every institution and this project already grades it, which makes a host a second, weaker
  candidate key that can be measured rather than assumed.

Provenance travels with the walk, the same way it does for the College Scorecard: every page is
written down with the URL it came from, when it was fetched, the status, the byte count, a
SHA-256 of the body, and the registry's own ``x-total``. Exhaustion is proven from that header
rather than from the row count, because a big result is not a complete one.

A full walk is around 340 requests over several minutes, which is long enough that a single
dropped TLS handshake would otherwise throw away every page that had already arrived. Two things
answer that, and neither of them relaxes the refusal to report a partial walk. Transport failures
are retried on the same bounded backoff as a 429, and then fail. And pages are written to an
optional cache directory as they arrive, so a rerun resumes from disk rather than from the
network: the cached body carries the time it was originally fetched and the total the registry
stated then, so a page served from the cache records when it really came from and is marked as
cached rather than being backdated to the rerun.

Two things the registry does that this module refuses to paper over. Pagination is by offset over
a set that publishers change while a walk is running, so the same organization can arrive twice
and another can be stepped over; duplicates are counted and reported rather than silently
collapsed into a smaller-looking total. And an unmatched filter value is answered HTTP 200 with
``x-total: 0`` rather than with an error, which is the failure mode the README records this
project falling for once already: a zero total is treated here as a walk that proved nothing, not
as a registry that holds nothing.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

__all__ = [
    "BASE_URL",
    "CAPTURE_KIND",
    "RESOURCE_TYPE",
    "Capture",
    "Organization",
    "PageRecord",
    "RegistryError",
    "read_capture",
    "reduce_record",
    "walk",
    "write_capture",
]

BASE_URL: Final[str] = "https://credentialengineregistry.org/ce-registry/search"
RESOURCE_TYPE: Final[str] = "organization"
CAPTURE_KIND: Final[str] = "credential-registry-capture"

_PER_PAGE: Final[int] = 100
_TIMEOUT: Final[float] = 60.0
_MAX_ATTEMPTS: Final[int] = 4
_BACKOFF_BASE: Final[float] = 2.0
_MAX_RETRY_AFTER: Final[float] = 120.0

# Seconds between consecutive fetches. A full walk is roughly 340 pages against a registry run by
# a non-profit with no published quota, so the pause is courtesy rather than accounting.
_PAUSE: Final[float] = 0.4

# The CTDL type for a postsecondary educational institution. This is the only agent type whose
# members could plausibly appear in the IPEDS directory, and it is what makes the join rate a
# rate of something rather than a rate over 33,000 training providers.
POSTSECONDARY: Final[str] = "orgType:Postsecondary"


class RegistryError(RuntimeError):
    """The registry could not be read, or was read incompletely.

    Raised rather than returning partial data, for the reason
    :class:`disclosed.sources.college_scorecard.ScorecardError` gives: a truncated walk understates
    the registry across every organization that never arrived, and a join rate computed over a
    truncated walk is a number with a denominator nobody can see.
    """


@dataclass(frozen=True, slots=True)
class PageRecord:
    """Where one page came from and what arrived. One of these per page of the walk."""

    page: int
    url: str
    fetched_at: str
    """UTC, second precision."""

    status: int
    bytes: int
    sha256: str
    attempts: int
    records: int
    from_cache: bool
    total_stated: int | None
    """The registry's ``x-total`` for this request, or ``None`` when it sent no usable header.

    ``None`` and never ``0``: an absent header is a count the registry did not state, and a zero
    is a count it stated. Collapsing them here would put the registry's own ambiguity into this
    project's data, which is the thing this project grades other people for.
    """


@dataclass(frozen=True, slots=True)
class Organization:
    """One registry organization, reduced to what a join to the federal corpora needs.

    Every field is ``str | None`` or an empty tuple rather than a placeholder. An organization
    that publishes no IPEDS id has ``ipeds_id=None``, which is a different fact from an
    organization that publishes an empty one, and neither is a zero.
    """

    ctid: str
    name: str | None
    ipeds_id: str | None
    ope_id: str | None
    org_types: tuple[str, ...]
    state: str | None
    homepage_host: str | None

    properties: tuple[str, ...] = ()
    """The CTDL property names present on this organization's node, sorted, ``@`` keys dropped.

    Names only, never values. The question this answers is what the registry publishes about an
    organization, which is a question about presence: a property is either on the node or it is
    not, and that is the same shape of fact this project already grades institutions on.
    """

    identifier_type_names: tuple[str, ...] = ()
    """The distinct type names inside ``ceterms:identifier``, sorted, as the publisher wrote them.

    Free text, and read as free text. ``docs/adr/0007`` refuses to read anything here as a federal
    identifier, and nothing here is read as one. It is captured because the most common value in
    the registry is ``IPEDS NCES Data Year``, which is a year, and how often that appears is the
    difference between an organization describing itself and a record loaded from a directory.
    """

    @property
    def is_postsecondary(self) -> bool:
        return POSTSECONDARY in self.org_types

    def as_dict(self) -> dict[str, Any]:
        """The seven fields the join needs, and deliberately not the two above.

        ``properties`` and ``identifier_type_names`` are left out of the committed capture on
        purpose: written per organization they add about 8.5 MB to a 7.9 MB file, and the
        questions they answer are questions about the population rather than about any one
        organization. ``disclosed registry-properties`` captures them aggregated instead, at
        245 KiB, and ``docs/adr/0009`` says why. An omission with a reason and a test on it is
        not the silent kind.
        """
        return {
            "ctid": self.ctid,
            "name": self.name,
            "ipeds_id": self.ipeds_id,
            "ope_id": self.ope_id,
            "org_types": list(self.org_types),
            "state": self.state,
            "homepage_host": self.homepage_host,
        }


@dataclass(frozen=True, slots=True)
class Capture:
    """The organizations a walk returned and the provenance of every page that carried them."""

    organizations: list[Organization]
    pages: list[PageRecord]
    total_stated: int | None
    exhausted: bool
    limit: int | None
    walked_at: str
    finished_at: str
    duplicates: int
    """Envelopes that repeated a ``ctid`` already seen. Offset pagination over a set that
    publishers are editing can serve the same organization twice; the count is published rather
    than hidden, because a walk that returned 33,809 envelopes holding 33,800 organizations has
    not covered the population the header claims."""

    unreduced: int
    """Envelopes that carried no readable CTDL organization node. Counted, never dropped in
    silence: an envelope this adapter could not read is a gap in the measurement, not a zero."""

    def provenance(self) -> dict[str, Any]:
        return {
            "base_url": BASE_URL,
            "resource_type": RESOURCE_TYPE,
            "per_page": _PER_PAGE,
            "walked_at": self.walked_at,
            "finished_at": self.finished_at,
            "limit": self.limit,
            "total_stated": self.total_stated,
            "organizations": len(self.organizations),
            "duplicates": self.duplicates,
            "unreduced": self.unreduced,
            "exhausted": self.exhausted,
            "pages_walked": len(self.pages),
            "calls": sum(1 for p in self.pages if not p.from_cache),
            "pages": [asdict(p) for p in self.pages],
        }


def _sleep(seconds: float) -> None:
    # Indirected so tests can neutralize the backoff without patching the stdlib globally.
    time.sleep(seconds)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _url(page: int, per_page: int) -> str:
    query = urllib.parse.urlencode(
        {"resource_type": RESOURCE_TYPE, "per_page": per_page, "page": page}
    )
    return f"{BASE_URL}?{query}"


def _int_header(headers: Any, name: str) -> int | None:
    value = headers.get(name) if headers is not None else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _retry_delay(headers: Any, attempt: int) -> float:
    """How long to wait before trying a page again; the registry's own ``Retry-After`` wins."""
    retry_after = headers.get("Retry-After") if headers is not None else None
    if isinstance(retry_after, str) and retry_after.strip().isdigit():
        return min(float(retry_after), _MAX_RETRY_AFTER)
    return _BACKOFF_BASE * float(2 ** (attempt - 1))


def _fetch_bytes(url: str, *, page: int, attempts: int) -> tuple[bytes, int, Any, int]:
    """Fetch one URL, retrying 429 and 5xx. Returns body, status, headers, attempts used."""
    last_status: int | None = None
    last_error = "no response"
    for attempt in range(1, attempts + 1):
        try:
            # Same waiver, and the same reason, as the College Scorecard adapter's fetch: the
            # scheme and host are fixed in BASE_URL and everything after the `?` is urlencoded
            # into the query string, so nothing outside this module chooses what is fetched.
            # nosemgrep: dynamic-urllib-use-detected
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310
                body: bytes = response.read()
                status = int(getattr(response, "status", 200) or 200)
                return body, status, response.headers, attempt
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            if exc.code != 429 and exc.code < 500:
                raise RegistryError(
                    f"Credential Registry page {page} rejected the request: HTTP {exc.code}"
                ) from exc
            if attempt == attempts:
                break
            _sleep(_retry_delay(exc.headers, attempt))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Retried rather than fatal, unlike the Scorecard adapter's equivalent, because a walk
            # here is 340 requests over several minutes against one host and a dropped handshake
            # in the middle of it is a transient fact about the connection, not about the
            # registry. Bounded the same way a 429 is: it retries, and then it fails.
            last_error = str(exc)
            if attempt == attempts:
                break
            _sleep(_retry_delay(None, attempt))
    reason = f"HTTP {last_status}" if last_status is not None else last_error
    raise RegistryError(
        f"Credential Registry page {page} still failing after {attempts} attempts: {reason}"
    )


def _english(value: Any) -> str | None:
    """A CTDL language map, or a bare string, as one string. ``None`` when there is nothing.

    CTDL writes most human-readable values as ``{"en-US": "..."}``. A map with no English entry
    falls back to whatever single language it does carry, because the alternative is discarding a
    name the registry published and recording the organization as unnamed.
    """
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("en-US", "en"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _identifier(value: Any) -> str | None:
    """``ceterms:ipedsID`` / ``ceterms:opeID`` as a string, or ``None``.

    The registry publishes both as strings, but a publisher that sends an integer is sending the
    same identifier and it is read as one. A value that is neither is not guessed at: it becomes
    ``None`` and the organization counts as publishing no identifier, which is true.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _org_types(node: dict[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    raw = node.get("ceterms:agentType")
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            target = item.get("ceterms:targetNode")
            if isinstance(target, str) and target not in out:
                out.append(target)
    return tuple(out)


def _properties(node: dict[str, Any]) -> tuple[str, ...]:
    """The CTDL property names on a node, sorted, without the JSON-LD keywords.

    ``@id`` and ``@type`` are dropped because they are on every node by construction and would
    say nothing about what a publisher chose to publish. Everything else is kept as written,
    including a property this adapter does not understand: a name nobody here recognises is
    still something the registry published, and dropping it would make the census a census of
    what this code knows about.
    """
    return tuple(sorted(key for key in node if isinstance(key, str) and not key.startswith("@")))


def _identifier_type_names(node: dict[str, Any]) -> tuple[str, ...]:
    """Distinct ``ceterms:identifierTypeName`` values inside ``ceterms:identifier``, sorted."""
    names: set[str] = set()
    raw = node.get("ceterms:identifier")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _english(item.get("ceterms:identifierTypeName"))
        if name:
            names.add(name)
    return tuple(sorted(names))


def _state(node: dict[str, Any]) -> str | None:
    raw = node.get("ceterms:address")
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            region = _english(item.get("ceterms:addressRegion"))
            if region:
                return region
    return None


def host_of(url: Any) -> str | None:
    """The comparable host of a web address, or ``None`` when there is not one.

    Lowercased, ``www.`` stripped, port and credentials discarded. This is the normalization the
    homepage join is measured under, and it is stated here rather than inline because the join
    rate it produces is only as defensible as the rule that produced it. A bare host with no
    scheme is accepted: publishers write ``example.edu`` as often as they write a URL.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    text = url.strip()
    if "//" not in text:
        text = f"//{text}"
    try:
        parsed = urllib.parse.urlsplit(text, scheme="https")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _organization_node(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """The CTDL node an envelope is about, or ``None``.

    An envelope's ``@graph`` holds the resource first and any referenced blank nodes after it. The
    node is matched on its ``ceterms:ctid`` against the envelope's own ``envelope_ceterms_ctid``
    rather than taken positionally, so an envelope whose graph is ordered differently cannot have
    a referenced sub-organization read as the organization the envelope is about.
    """
    resource = envelope.get("decoded_resource")
    graph = resource.get("@graph") if isinstance(resource, dict) else None
    if not isinstance(graph, list):
        return None
    nodes = [node for node in graph if isinstance(node, dict)]
    wanted = envelope.get("envelope_ceterms_ctid")
    if isinstance(wanted, str) and wanted:
        for node in nodes:
            if node.get("ceterms:ctid") == wanted:
                return node
    for node in nodes:
        if isinstance(node.get("ceterms:ctid"), str):
            return node
    return None


def reduce_record(envelope: Any) -> Organization | None:
    """One search-result envelope reduced to the projection the join needs, or ``None``.

    ``None`` for an envelope with no readable organization node or no ctid to key it on. The
    caller counts those; they are never turned into an organization with a placeholder id, which
    would put two unidentifiable rows into the same bucket and let one of them be joined.
    """
    if not isinstance(envelope, dict):
        return None
    node = _organization_node(envelope)
    if node is None:
        return None
    ctid = node.get("ceterms:ctid")
    if not isinstance(ctid, str) or not ctid.strip():
        return None
    return Organization(
        ctid=ctid.strip(),
        name=_english(node.get("ceterms:name")),
        ipeds_id=_identifier(node.get("ceterms:ipedsID")),
        ope_id=_identifier(node.get("ceterms:opeID")),
        org_types=_org_types(node),
        state=_state(node),
        homepage_host=host_of(node.get("ceterms:subjectWebpage")),
        properties=_properties(node),
        identifier_type_names=_identifier_type_names(node),
    )


def _cache_paths(cache_dir: Path, page: int) -> tuple[Path, Path]:
    return cache_dir / f"page-{page:04d}.json", cache_dir / f"page-{page:04d}.meta.json"


def _from_cache(cache_dir: Path | None, page: int) -> tuple[bytes, dict[str, Any]] | None:
    """A page's cached body and the provenance recorded when it was fetched, or ``None``.

    Both files have to be there. A body without its provenance would be a page that cannot say
    when it was fetched or what total the registry stated at the time, and writing a guess into
    the record would be the wrong kind of filling in for a project about blanks.
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
) -> tuple[list[Any], PageRecord]:
    """One page as a list of envelopes, with the record of where it came from."""
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
            "total_stated": _int_header(headers, "x-total"),
        }
        _to_cache(cache_dir, page, body, meta)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RegistryError(f"Credential Registry page {page} unreadable: {exc}") from exc
    if not isinstance(payload, list):
        raise RegistryError(
            f"Credential Registry page {page} returned {type(payload).__name__}, not a list of "
            "envelopes"
        )
    total = meta.get("total_stated")
    record = PageRecord(
        page=page,
        url=url,
        fetched_at=str(meta.get("fetched_at", "")),
        status=int(meta.get("status", 0)),
        bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        attempts=int(meta.get("attempts", 0)),
        records=len(payload),
        from_cache=cached is not None,
        total_stated=total if isinstance(total, int) else None,
    )
    return payload, record


@dataclass(slots=True)
class _Counts:
    """What a walk saw, apart from the organizations it kept. Mutable, and folded page by page."""

    returned: int = 0
    duplicates: int = 0
    unreduced: int = 0


def _absorb(
    envelopes: list[Any],
    organizations: list[Organization],
    seen: set[str],
    counts: _Counts,
    limit: int | None,
) -> None:
    """Fold one page of envelopes into the walk, counting what did not become an organization.

    A repeated ctid is counted and dropped rather than appended. Offset pagination over a set the
    registry's publishers are editing can serve the same organization on two pages, and keeping
    both would put one organization into a join denominator twice.
    """
    for envelope in envelopes:
        counts.returned += 1
        organization = reduce_record(envelope)
        if organization is None:
            counts.unreduced += 1
            continue
        if organization.ctid in seen:
            counts.duplicates += 1
            continue
        seen.add(organization.ctid)
        organizations.append(organization)
        if limit is not None and len(organizations) >= limit:
            return


def walk(
    *,
    limit: int | None = None,
    per_page: int = _PER_PAGE,
    cache_dir: Path | None = None,
    pause: float = _PAUSE,
    attempts: int = _MAX_ATTEMPTS,
) -> Capture:
    """Page the registry to exhaustion, or to ``limit``, recording the provenance of every page.

    Args:
        limit: Stop after this many organizations. ``None`` walks the whole resource type.
        per_page: Envelopes per request.
        cache_dir: Directory of per-page bodies and the provenance recorded when they arrived. A
            page found there is served from disk and recorded as such; a page fetched is written
            there. ``None`` disables the cache. A walk this long needs it: the point is that a
            rerun after a dropped connection resumes rather than starting over.
        pause: Seconds between consecutive network fetches. Skipped for a cached page.
        attempts: Retries per page for 429 and 5xx.

    Raises:
        RegistryError: ``limit`` is ``None`` and the walk ran out of pages without reaching the
            registry's own ``x-total``, or no page carried a usable ``x-total`` at all, or the
            stated total was zero. The last of those is the case the README already records this
            project misreading once: the registry answers an unmatched filter with HTTP 200 and
            ``x-total: 0``, so a zero total is evidence about the query and not about the
            registry, and a walk that returns it has measured nothing.
    """
    walked_at = _now()
    organizations: list[Organization] = []
    pages: list[PageRecord] = []
    seen: set[str] = set()
    counts = _Counts()
    last_total: int | None = None
    page = 1
    while True:
        if pages and not pages[-1].from_cache and pause > 0:
            _sleep(pause)
        envelopes, record = _read_page(
            page, per_page=per_page, attempts=attempts, cache_dir=cache_dir
        )
        pages.append(record)
        if record.total_stated is not None:
            last_total = record.total_stated
        if not envelopes:
            break
        _absorb(envelopes, organizations, seen, counts, limit)
        if limit is not None and len(organizations) >= limit:
            break
        page += 1

    exhausted = (
        limit is None
        and last_total is not None
        and last_total > 0
        and counts.returned >= last_total
    )
    if limit is None and not exhausted:
        raise _short_walk(len(pages), counts.returned, last_total)
    return Capture(
        organizations=organizations,
        pages=pages,
        total_stated=last_total,
        exhausted=exhausted,
        limit=limit,
        walked_at=walked_at,
        finished_at=_now(),
        duplicates=counts.duplicates,
        unreduced=counts.unreduced,
    )


def _short_walk(pages: int, returned: int, total: int | None) -> RegistryError:
    if total is None:
        stated = "no page carried a usable x-total header, so there is nothing to have reached"
    elif total == 0:
        stated = (
            "the registry stated a total of 0, which it also answers to an unmatched filter "
            "value; a zero total is evidence about the query and not about the registry"
        )
    else:
        stated = f"the registry's stated total of {total}"
    return RegistryError(
        f"Credential Registry walk ended after {pages} pages and {returned} envelopes, short of "
        f"{stated}. A walk that cannot confirm it reached the end is reported as a failure, not "
        "as a count of the registry."
    )


def _organization_lines(organizations: list[Organization]) -> str:
    # One organization per line, compact, keys sorted: a diff between two captures then names the
    # organizations that moved rather than reflowing thirty thousand lines of indentation.
    return ",\n".join(
        "    " + json.dumps(o.as_dict(), sort_keys=True, separators=(",", ":"))
        for o in organizations
    )


def write_capture(capture: Capture, path: Path) -> None:
    """Write a capture as an envelope: ``kind``, ``provenance``, ``organizations``."""
    head = json.dumps(
        {"kind": CAPTURE_KIND, "provenance": capture.provenance()}, indent=2, sort_keys=True
    )
    assert head.endswith("\n}")  # noqa: S101 -- the splice below depends on json's layout
    body = _organization_lines(capture.organizations)
    text = head[:-2] + ',\n  "organizations": [\n' + body + "\n  ]\n}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_capture(payload: Any) -> tuple[list[Organization], dict[str, Any]]:
    """Read a capture envelope back. Raises :class:`RegistryError` on anything else.

    A bare list of organizations is refused rather than accepted as a capture with unknown
    provenance. The provenance is what says whether the walk was exhaustive, and a join rate read
    off a file that cannot say is a rate over an unknown denominator.
    """
    if not isinstance(payload, dict) or payload.get("kind") != CAPTURE_KIND:
        raise RegistryError(
            f"not a {CAPTURE_KIND}: a capture carries its own provenance, and a walk that cannot "
            "say whether it was exhaustive cannot support a join rate"
        )
    provenance = payload.get("provenance")
    rows = payload.get("organizations")
    if not isinstance(provenance, dict) or not isinstance(rows, list):
        raise RegistryError(f"a {CAPTURE_KIND} needs a provenance object and an organization list")
    organizations = [
        Organization(
            ctid=str(row["ctid"]),
            name=row.get("name"),
            ipeds_id=row.get("ipeds_id"),
            ope_id=row.get("ope_id"),
            org_types=tuple(row.get("org_types") or ()),
            state=row.get("state"),
            homepage_host=row.get("homepage_host"),
        )
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("ctid"), str)
    ]
    if len(organizations) != len(rows):
        raise RegistryError(
            "a capture carries an organization without a ctid; it cannot be keyed, and keying it "
            "on a placeholder would let two unidentifiable rows join as one"
        )
    return organizations, provenance
