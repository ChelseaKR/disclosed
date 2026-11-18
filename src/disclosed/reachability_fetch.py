"""The one place this project touches a page it did not publish, and every toll it pays.

Everything else in this repository is reproducible offline from committed federal captures by
somebody with no key and no network. This module is the exception, and it is deliberately the
only one: a link check is reproducible only as "this is what happened on that date", which is a
weaker kind of claim than anything else here makes. ``docs/adr/0011`` records the decision to
make that claim anyway and the conditions attached to it.

Politeness is the design, not an afterthought, and every rule is enforced in one method so that
nothing else can forget one. In order, :meth:`Session.get` pays: the run's request budget, then
``robots.txt`` for the host, then the per-host interval. Nothing else in this package opens a
socket.

``robots.txt`` follows RFC 9309's asymmetry. A ``4xx`` means the host publishes no crawling
policy and fetching may proceed. A ``5xx`` means a policy exists and cannot be read right now, so
the host is treated as fully disallowed and the address is recorded as skipped. A transport
failure on the robots probe is evidence about the host and not about its policy, so the address is
attempted and its own failure is what gets recorded.

**More than one attempt, always, before anything that could name an institution.** A ``404`` seen
once from one vantage point is not a finding about a college, and this is the module that refuses
to let it become one: an address whose first attempt did not return a document is requested again
after a pause, and the capture records how many attempts it took. A verdict of ``GONE`` therefore
rests on the server saying the same thing twice.

The body read is bounded. A disclosure that needs more than 64 KiB to say it exists can say it in
the first 64 KiB, the digest is over exactly those bytes and says so, and nothing here follows a
link, reads a sitemap, or requests anything the federal record did not itself declare.

No cookies, no beacons, no analytics, no JavaScript: a bare ``urllib`` opener with a declared user
agent, and the opener is injectable so the test suite exercises every branch without a network.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from .disclosure import Disclosure
from .fields import Field
from .reachability import (
    SKIP_BUDGET,
    SKIP_NO_URL,
    SKIP_ROBOTS,
    SKIP_ROBOTS_UNREADABLE,
    Observation,
)

__all__ = [
    "AGENT_TOKEN",
    "ATTEMPTS",
    "MAX_BODY_BYTES",
    "PER_HOST_INTERVAL",
    "USER_AGENT",
    "Attempt",
    "Budget",
    "Session",
    "Target",
    "capture",
    "looks_like_a_soft_error",
    "normalize_url",
    "targets_from_records",
    "urllib_fetch",
]

AGENT_TOKEN: Final[str] = "disclosed-reachability"  # noqa: S105 - robots product token
"""The product token ``robots.txt`` rules are matched against.

Its own token rather than a browser string, so that any host that wants to exclude this check can
do it by name in one line, and so that a host reading its logs can tell what came knocking.
"""

USER_AGENT: Final[str] = (
    "Mozilla/5.0 (compatible; disclosed-reachability/1; "
    "+https://chelseakr.github.io/disclosed/methodology/) "
    "checks one published address per institution, one request per host at a time"
)

DEFAULT_TIMEOUT: Final[float] = 15.0

MAX_BODY_BYTES: Final[int] = 65_536
"""Bytes read per response. The digest is over these and the capture records the count, so a
reader can see that a digest is a digest of a prefix rather than of a document."""

PER_HOST_INTERVAL: Final[float] = 1.5
"""Seconds between two requests to the same host, at minimum. One request per host at a time."""

ATTEMPTS: Final[int] = 2
"""Attempts before a non-retrievable result is recorded against a named institution."""

RETRY_PAUSE: Final[float] = 5.0
"""Seconds before a second attempt on an address that did not return a document."""

# Transport failures. Every one of these classifies as UNCHECKED, never GONE: none of them is
# the server saying anything at all.
KIND_TIMEOUT: Final[str] = "timeout"
KIND_DNS: Final[str] = "dns"
KIND_TLS: Final[str] = "tls"
KIND_CONNECTION: Final[str] = "connection"
KIND_PROTOCOL: Final[str] = "protocol"
KIND_INVALID: Final[str] = "invalid-url"

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")

# Titles that announce a page as an error while the status line says 200. Matched against the
# <title> only, and only ever to move a verdict from READABLE to INDETERMINATE -- never to GONE,
# and never against an institution. A conservative list on purpose: a heuristic that can
# manufacture a finding is a heuristic that will.
_SOFT_ERROR_TITLES: Final[tuple[str, ...]] = (
    "404",
    "page not found",
    "file not found",
    "not found",
    "page cannot be found",
    "page no longer exists",
    "page unavailable",
    "page not available",
    "error 403",
    "access denied",
    "site maintenance",
    "under maintenance",
    "service unavailable",
)

_TITLE_RE: Final[re.Pattern[str]] = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.S)


def looks_like_a_soft_error(body: str) -> bool:
    """Whether a ``2xx`` body announces itself, in its own title, as an error page.

    Title only. A phrase in prose proves nothing -- a financial aid page may well contain the
    words "not found" -- and widening this would let the heuristic invent findings. The whole
    consequence of a true here is ``INDETERMINATE``, which is not a mark against anybody; the
    case it exists for is the soft 404, where a server answers ``200`` with an error page and a
    naive check publishes it as a working disclosure.
    """
    match = _TITLE_RE.search(body)
    if match is None:
        return False
    title = " ".join(match.group(1).split()).casefold()
    return any(phrase in title for phrase in _SOFT_ERROR_TITLES)


def normalize_url(raw: str) -> str | None:
    """The fetchable form of an address as the federal record wrote it, or ``None``.

    IPEDS carries addresses as institutions typed them: some with a scheme, most without, a few
    with surrounding whitespace. A bare host is given ``https://`` because that is what a browser
    would do. Anything that is not http or https after that -- a ``mailto:``, a typo, an empty
    cell -- returns ``None`` and is recorded as skipped rather than requested, because requesting
    a scheme this module cannot reason about is how an opener ends up reading a local file.

    The path and query are percent-encoded, which is not cosmetic. Several institutions report an
    address containing literal spaces -- a vendor calculator keyed on the college's name is the
    common case -- and ``urllib`` refuses to put a space on a request line. Sending it raw would
    record a protocol error and produce ``UNCHECKED`` for an address a browser opens without
    complaint: this project's own client failing, published as something about the institution.
    Characters that are already escaped are left alone rather than escaped twice.
    """
    text = raw.strip()
    if not text:
        return None
    if "://" in text:
        if text.split("://", 1)[0].lower() not in _ALLOWED_SCHEMES:
            return None
    else:
        declared = _SCHEME_RE.match(text)
        if declared is not None and "." not in declared.group(1):
            # A ``mailto:``, a ``tel:``, a ``javascript:``. Prefixing one of those with https://
            # would turn the scheme into a hostname and send a request somewhere nobody named.
            # A dot in the prefix means it is a host with a port, which is an address.
            return None
        text = f"https://{text}"
    split = urllib.parse.urlsplit(text)
    if split.scheme not in _ALLOWED_SCHEMES or not split.netloc:
        return None
    return urllib.parse.urlunsplit(
        (
            split.scheme,
            split.netloc,
            urllib.parse.quote(split.path, safe="/%:@!$&'()*+,;=~-._"),
            urllib.parse.quote(split.query, safe="/%:@!$&'()*+,;=?~-._"),
            "",
        )
    )


def host_of(url: str) -> str:
    """The host a politeness interval and a ``robots.txt`` lookup are keyed on."""
    return urllib.parse.urlsplit(url).netloc.lower()


@dataclass(frozen=True, slots=True)
class Attempt:
    """What one HTTP request produced, success or not."""

    requested_url: str
    status: int | None
    final_url: str
    body: str
    body_bytes: int
    error_kind: str
    error: str

    @property
    def answered(self) -> bool:
        """Whether the server said anything at all, with any status."""
        return self.status is not None


class _Opener(Protocol):
    """The one method of urllib's opener this module uses, made injectable for the tests."""

    def open(self, fullurl: Any, timeout: float) -> Any: ...  # pragma: no cover - protocol


def _error_kind(exc: BaseException) -> str:
    """Map a transport exception to the reason worth recording."""
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        reason = exc.reason
        return _error_kind(reason) if isinstance(reason, BaseException) else KIND_CONNECTION
    if isinstance(exc, TimeoutError):
        return KIND_TIMEOUT
    if isinstance(exc, socket.gaierror):
        return KIND_DNS
    if isinstance(exc, ssl.SSLError):
        return KIND_TLS
    if isinstance(exc, http.client.HTTPException):
        return KIND_PROTOCOL
    if isinstance(exc, ValueError):
        return KIND_INVALID
    return KIND_CONNECTION


def _read(response: Any, limit: int) -> tuple[str, int]:
    """Decode up to ``limit`` bytes of a response as (text, bytes actually read)."""
    headers = getattr(response, "headers", None)
    get_charset = getattr(headers, "get_content_charset", None)
    charset = str(get_charset() or "utf-8") if callable(get_charset) else "utf-8"
    try:
        raw = bytes(response.read(limit))
    except (OSError, ValueError):
        raw = b""
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return text, len(raw)


def urllib_fetch(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = USER_AGENT,
    max_bytes: int = MAX_BODY_BYTES,
    opener: _Opener | None = None,
) -> Attempt:
    """Request one URL and report whatever happened as a value.

    Never raises. A ``404`` and a DNS failure are both things worth recording, and a capture that
    died on either would have lost its cohort. The distinction the classifier needs -- the server
    answered, versus the server never spoke -- is carried by the status being present or ``None``.
    """
    agent = opener if opener is not None else urllib.request.build_opener()
    request = urllib.request.Request(  # noqa: S310 - scheme checked in normalize_url
        url, headers={"User-Agent": user_agent, "Accept": "text/html,*/*"}
    )
    try:
        response: Any = agent.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body, read = _read(exc, max_bytes)
        return Attempt(url, exc.code, str(exc.geturl() or url), body, read, "", "")
    except Exception as exc:
        return Attempt(url, None, "", "", 0, _error_kind(exc), str(exc)[:200])
    with response:
        body, read = _read(response, max_bytes)
        status = int(getattr(response, "status", 0) or 200)
        final = str(response.geturl() or url)
    return Attempt(url, status, final, body, read, "", "")


@dataclass
class Budget:
    """A hard ceiling on how much of the web one run may touch.

    Checked before ``robots.txt``, not after, so a run can never exceed its stated budget by the
    probes it makes to find out whether it is allowed to fetch. The number is written into the
    capture, because a measurement that stopped early because it ran out of budget and a
    measurement that stopped because it finished are not the same measurement.
    """

    requests: int
    spent: int = 0

    def take(self) -> bool:
        """Spend one request, or report that there is nothing left to spend."""
        if self.spent >= self.requests:
            return False
        self.spent += 1
        return True


class Session:
    """One polite crawl: a budget, a robots cache, and a per-host clock."""

    def __init__(
        self,
        *,
        budget: Budget,
        fetch: Callable[..., Attempt] = urllib_fetch,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        per_host_interval: float = PER_HOST_INTERVAL,
        user_agent: str = USER_AGENT,
        agent_token: str = AGENT_TOKEN,
    ) -> None:
        self._budget = budget
        self._fetch = fetch
        self._sleep = sleep
        self._clock = clock
        self._interval = per_host_interval
        self._user_agent = user_agent
        self._agent_token = agent_token
        self._robots: dict[str, urllib.robotparser.RobotFileParser | str] = {}
        self._last_seen: dict[str, float] = {}

    @property
    def budget(self) -> Budget:
        return self._budget

    def pause_before_retry(self) -> None:
        """Wait before asking a host again for something it has already declined to serve.

        Longer than the per-host interval on purpose. A retry is this project asking a second
        time after a failure, and the host owes it nothing; the pause is what keeps a retry
        policy from turning one bad minute on a server into two requests a second.
        """
        self._sleep(RETRY_PAUSE)

    def _wait_for(self, host: str) -> None:
        last = self._last_seen.get(host)
        now = self._clock()
        if last is not None and now - last < self._interval:
            self._sleep(self._interval - (now - last))
        self._last_seen[host] = self._clock()

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | str:
        """The host's parsed policy, or the skip reason that stands in for one."""
        host = host_of(url)
        cached = self._robots.get(host)
        if cached is not None:
            return cached
        split = urllib.parse.urlsplit(url)
        robots_url = urllib.parse.urlunsplit((split.scheme, split.netloc, "/robots.txt", "", ""))
        if not self._budget.take():
            return SKIP_BUDGET
        self._wait_for(host)
        attempt = self._fetch(robots_url, user_agent=self._user_agent)
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        if attempt.status is None:
            # The probe itself failed to connect. That is evidence about the host, not about a
            # policy, so no policy is recorded and the page fetch is allowed to fail on its own
            # terms. Not cached, for the same reason.
            parser.parse([])
            return parser
        if attempt.status >= 500:
            self._robots[host] = SKIP_ROBOTS_UNREADABLE
            return SKIP_ROBOTS_UNREADABLE
        parser.parse(attempt.body.splitlines() if attempt.status < 400 else [])
        self._robots[host] = parser
        return parser

    def get(self, url: str) -> tuple[Attempt | None, str]:
        """Fetch one URL politely, or say why it was not fetched.

        Returns ``(attempt, skip)``. Exactly one of the two is meaningful: a skip reason means no
        request was made, and the caller must record it rather than treating it as a failure of
        the address.
        """
        policy = self._robots_for(url)
        if isinstance(policy, str):
            return None, policy
        if not policy.can_fetch(self._agent_token, url):
            return None, SKIP_ROBOTS
        if not self._budget.take():
            return None, SKIP_BUDGET
        self._wait_for(host_of(url))
        return self._fetch(url, user_agent=self._user_agent), ""


@dataclass(frozen=True, slots=True)
class Target:
    """One published address, and the institution the record attaches it to."""

    unit_id: str
    name: str
    state: str
    field_key: str
    raw_url: str


def _observation(target: Target, url: str, attempt: Attempt, attempts: int) -> Observation:
    body = attempt.body
    return Observation(
        unit_id=target.unit_id,
        name=target.name,
        state=target.state,
        field_key=target.field_key,
        requested_url=url,
        attempts=attempts,
        status=attempt.status,
        final_url=attempt.final_url,
        error_kind=attempt.error_kind,
        skipped="",
        digest=hashlib.sha256(body.encode("utf-8", "replace")).hexdigest() if body else "",
        body_bytes=attempt.body_bytes,
        soft_error=bool(attempt.status and 200 <= attempt.status < 300)
        and looks_like_a_soft_error(body),
    )


def _skipped(target: Target, url: str, reason: str) -> Observation:
    return Observation(
        unit_id=target.unit_id,
        name=target.name,
        state=target.state,
        field_key=target.field_key,
        requested_url=url,
        attempts=0,
        status=None,
        final_url="",
        error_kind="",
        skipped=reason,
        digest="",
        body_bytes=0,
        soft_error=False,
    )


def _retrievable(attempt: Attempt) -> bool:
    """Whether this attempt already settles the question in the institution's favour."""
    return attempt.status is not None and 200 <= attempt.status < 300


def observe(session: Session, target: Target, *, attempts: int = ATTEMPTS) -> Observation:
    """Visit one published address, more than once if the first visit did not find a document.

    The retry is the rule that makes a named finding publishable. Anything other than a document
    -- a ``404``, a timeout, a refusal -- is tried again after a pause before it is written down,
    so that nothing in the committed capture rests on a single failed attempt against a named
    institution. A first attempt that already found a document is not repeated: there is nothing
    to confirm and a second request would be a request the host did not need to serve.
    """
    url = normalize_url(target.raw_url)
    if url is None:
        return _skipped(target, target.raw_url, SKIP_NO_URL)
    last: Attempt | None = None
    made = 0
    for index in range(max(1, attempts)):
        attempt, skip = session.get(url)
        if attempt is None:
            if made == 0:
                return _skipped(target, url, skip)
            break
        made += 1
        last = attempt
        if _retrievable(attempt):
            break
        if index + 1 < attempts:
            session.pause_before_retry()
    if last is None:  # pragma: no cover - unreachable while attempts >= 1
        return _skipped(target, url, SKIP_BUDGET)
    return _observation(target, url, last, made)


def capture(
    targets: Iterable[Target],
    *,
    session: Session,
    on_progress: Callable[[int, Observation], None] | None = None,
) -> list[Observation]:
    """Visit every target in order, and return what was seen.

    Serial by construction. There is no worker pool here and there is not meant to be one: the
    politeness rule this project committed to is one request per host at a time, and the simplest
    way to hold it is to make one request at a time.
    """
    seen: list[Observation] = []
    for index, target in enumerate(targets, start=1):
        observation = observe(session, target)
        seen.append(observation)
        if on_progress is not None:
            on_progress(index, observation)
    return seen


def targets_from_records(
    records: Iterable[Mapping[str, Any]], *, fields: Iterable[Field]
) -> list[Target]:
    """Every address the grader counted as published, for the fields named.

    The cohort is defined by the grader and not by this module, and that is what keeps the two
    numbers comparable. ``published`` in the report is the count of ``REPORTED`` cells for a
    field; a target is built for exactly those cells, so ``verified / published`` is a share of
    the same set rather than two counts of two different things.

    It also settles the cases this module has no business ruling on. A blank cell, an IPEDS
    negative sentinel, a cell belonging to an institution the duty does not reach -- the grader
    has already classified every one of them, and manufacturing a fetch for any of them would put
    a reachability verdict beside an institution that never claimed to have a page.
    """
    wanted = tuple(fields)
    out: list[Target] = []
    for record in records:
        for field in wanted:
            if field.classify(record) is not Disclosure.REPORTED:
                continue
            out.append(
                Target(
                    unit_id=str(record.get("id") or "").strip(),
                    name=str(record.get("school.name") or "").strip(),
                    state=str(record.get("school.state") or "").strip(),
                    field_key=field.key,
                    raw_url=str(record.get(field.key) or "").strip(),
                )
            )
    return out
