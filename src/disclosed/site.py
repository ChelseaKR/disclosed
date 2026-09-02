"""Static site generation: one page per institution, one per state, and one for the methodology.

Deterministic and dependency-free, the same discipline as the grader. No templating library, no
network at build time, and no clock: the ``generated`` stamp is passed in for the same reason
:class:`disclosed.drift.Snapshot` takes ``taken`` from the caller, so that rebuilding the same
report twice produces byte-identical output and a diff means something changed.

The site is built from the published report rather than from a fresh grading pass. That is a
deliberate constraint, not a convenience: it makes the site incapable of claiming anything the
published dataset does not contain, so a reader who downloads the JSON can check every sentence
here against it.

The rendering rules restate the project's own discipline, because a page is where the null-versus
-zero error finally becomes visible to a member of the public:

* An institution with no grade renders as "not gradeable", never as F and never as 0%.
* An institution the source did not name renders as "unnamed" and gets no page of its own, because
  there is no stable URL to give it. It is still counted, so a reader can see it exists.
* Every field on every institution page links to the rationale for that field, so the sentence "we
  marked you down for this" always arrives attached to the reasoning it rests on.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .disclosure import Disclosure
from .drift import SYSTEMIC_THRESHOLD
from .fields import FIELDS, IPEDS_FIELDS, Field, field_by_label
from .grading import BANDS, BELOW_EVERY_BAND
from .peers import MIN_PEERS
from .scope import Scope, scope_from_payload

__all__ = ["Page", "build", "slug"]

DEFAULT_ORIGIN: Final[str] = "https://chelseakr.github.io/disclosed"

#: Where a reader goes to check any of this. Every page carries it, because a site that grades
#: other people's disclosure and does not say where its own rules are readable is asking for a
#: trust it has not earned.
SOURCE_URL: Final[str] = "https://github.com/ChelseaKR/disclosed"

#: The share card. A link preview strips a page to its title, one sentence and this image, so the
#: card is written into the output beside the pages rather than named as a URL somewhere else: an
#: ``og:image`` is fetched once, by a crawler, and a 404 there is reported to nobody.
_OG_CARD_SOURCE: Final[Path] = Path(__file__).resolve().parent / "assets" / "og-card.png"
OG_CARD_NAME: Final[str] = "og-card.png"
OG_CARD_WIDTH: Final[int] = 1200
OG_CARD_HEIGHT: Final[int] = 630
OG_CARD_ALT: Final[str] = (
    "disclosed: what US colleges do not tell you. Grades US higher-education institutions on "
    "what they disclose, not on how they perform."
)

# What each classification means to a reader, and whether the institution is answerable for it.
# Written for a person who has just been told their college scored badly and wants to know why.
_DISCLOSURE_COPY: Final[dict[Disclosure, tuple[str, str]]] = {
    Disclosure.REPORTED: (
        "Reported",
        "A credible value was published.",
    ),
    Disclosure.IMPLAUSIBLE: (
        "Implausible",
        "A value was published but falls outside the credible range for this field. Counted as a "
        "disclosure failure: a gap is visible to a reader, a wrong number is not.",
    ),
    Disclosure.SUPPRESSED: (
        "Suppressed",
        "Withheld deliberately, usually to protect a small cohort. Not held against the "
        "institution and removed from the denominator entirely.",
    ),
    Disclosure.NOT_APPLICABLE: (
        "Not applicable",
        "The question does not apply to this institution. Removed from the denominator entirely.",
    ),
    Disclosure.MISSING: (
        "Not reported",
        "No value and no stated reason. This is the one that counts against a publisher.",
    ),
}

_LETTER_COPY: Final[dict[str, str]] = {
    "A": "published nearly everything it was in a position to publish",
    "B": "published most of what it was in a position to publish",
    "C": "left a reader with real gaps",
    "D": "left more unpublished than published",
    "F": "published almost nothing a reader could use",
}


@dataclass(frozen=True, slots=True)
class Page:
    """One rendered page. ``path`` is a site-relative directory; the file is always index.html."""

    path: str
    title: str
    description: str
    body: str


def slug(text: str) -> str:
    """Reduce a value to something safe to use as a directory name.

    Everything outside ``[A-Za-z0-9._-]`` is replaced, and leading dots are stripped. Unit ids
    arrive from a federal source and are numeric in practice, but this writes to the filesystem
    from third-party data, and a value like ``../../etc`` must not be able to decide where a file
    lands. Returns ``""`` for anything that reduces to nothing, which callers treat as "no page".
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return cleaned


def _pct(value: float | None) -> str:
    """Format a score, or say plainly that there is not one.

    An ungradeable institution has no score. It must never be rendered as 0%, which is the exact
    confusion this whole project exists to prevent, and rendering it as an em dash would be almost
    as bad because a reader would read the dash as "zero" anyway.
    """
    return "not gradeable" if value is None else f"{value:.0%}"


def _name_of(row: dict[str, Any]) -> str:
    """Display name for an institution the source may not have named.

    Never ``str(row["name"])``: that prints the word "None" as a name. An absent name is stated as
    absent, and the unit id is offered instead so the row is still traceable to a record.
    """
    name = row.get("name")
    if isinstance(name, str) and name.strip():
        return name
    unit_id = row.get("unit_id")
    if isinstance(unit_id, str) and unit_id.strip():
        return f"Unnamed institution (unit id {unit_id})"
    return "Unnamed institution (no unit id published)"


def _grade_badge(letter: str | None) -> str:
    """A grade, with its meaning available to a reader who cannot see the badge.

    The ungradeable badge used to carry its explanation in a ``title`` attribute, which is not
    reliably announced by screen readers and is invisible to anyone navigating by keyboard. A
    person using a screen reader would have heard "n a" and nothing else, which is the audible
    version of rendering an absence as a bare number: technically present, unreadable as meaning.
    """
    if letter is None:
        return (
            '<span class="grade grade-none">n/a'
            '<span class="visually-hidden">: not gradeable, no field applied</span></span>'
        )
    return (
        f'<span class="grade grade-{letter.lower()}">{html.escape(letter)}'
        f'<span class="visually-hidden"> grade</span></span>'
    )


def _rationale_link(label: str, text: str, *, depth: int) -> str:
    """Link a field label to its rationale, degrading to plain text if the field is unknown."""
    field = field_by_label(label)
    if field is None:
        return html.escape(text)
    up = "../" * depth
    return f'<a href="{up}methodology/#{field.anchor}">{html.escape(text)}</a>'


def _institution_path(row: dict[str, Any]) -> str | None:
    """Site path for an institution, or ``None`` if it cannot be given a stable URL.

    An institution with no unit id gets no page. Inventing one from its name or its position in
    the file would produce a URL that silently points at a different school the next time the
    corpus changes, and a citable page that quietly changes subject is worse than no page.
    """
    unit_id = row.get("unit_id")
    if not isinstance(unit_id, str):
        return None
    safe = slug(unit_id)
    return f"institution/{safe}" if safe else None


def institution_page(
    row: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    path: str,
    ask_endpoint: str | None = None,
) -> Page:
    """One institution: its grade, every field's disclosure state, and any implausible values.

    With ``ask_endpoint`` the page also carries the opt-in question form and the one inline
    script behind it (see :func:`_ask_widget`); without it the page is exactly what it was.
    """
    name = _name_of(row)
    letter = row.get("letter")
    score = row.get("score")
    state = row.get("state")
    summary = _LETTER_COPY.get(letter or "", "could not be graded on any field")

    rows = []
    for label in sorted(row.get("fields", {})):
        raw_state = row["fields"][label]
        try:
            disclosure = Disclosure(raw_state)
        except ValueError:
            # A report written by a newer version than this renderer. Say so rather than guessing.
            rows.append(
                f'<tr><th scope="row">{_rationale_link(label, label, depth=2)}</th>'
                f'<td colspan="2">unrecognized classification '
                f"{html.escape(str(raw_state))}</td></tr>"
            )
            continue
        title, meaning = _DISCLOSURE_COPY[disclosure]
        rows.append(
            f'<tr><th scope="row">{_rationale_link(label, label, depth=2)}</th>'
            f'<td><span class="tag tag-{disclosure.value.replace("_", "-")}">'
            f"{html.escape(title)}</span></td>"
            f"<td>{html.escape(meaning)}</td></tr>"
        )

    findings_html = ""
    if findings:
        items = []
        for finding in findings:
            peers = finding.get("peers")
            verdict = (
                f'<p class="peers">Peer check, '
                f"{html.escape(str(peers.get('group', 'unknown group')))}: "
                f"{html.escape(str(peers.get('verdict', '')))}</p>"
                if isinstance(peers, dict)
                else '<p class="peers">No peer group was available for this institution, so no '
                "comparison is claimed.</p>"
            )
            label = str(finding.get("field", ""))
            items.append(
                f"<li><strong>{_rationale_link(label, label, depth=2)}</strong> published as "
                f"<code>{html.escape(json.dumps(finding.get('value')))}</code>."
                f"{verdict}"
                f'<p class="why">{html.escape(str(finding.get("rationale", "")))}</p></li>'
            )
        findings_html = (
            "<h2>Values that do not look like measurements</h2>"
            "<p>These were published, so they are not gaps. They fall outside the credible range "
            "for their field, which is a judgement this project made and states in full so it can "
            "be argued with.</p>"
            f'<ul class="findings">{"".join(items)}</ul>'
        )

    state_link = (
        f'<a href="../../state/{html.escape(slug(state))}/">{html.escape(state)}</a>'
        if isinstance(state, str) and slug(state)
        else "state not published"
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="../../">All institutions</a> / {state_link}</nav>
<h1>{html.escape(name)} {_grade_badge(letter)}</h1>
<p class="lede">On the fields this project checks, this institution {html.escape(summary)}.</p>
<dl class="facts">
  <dt>Disclosure score</dt><dd>{html.escape(_pct(score))}</dd>
  <dt>State</dt><dd>{state_link}</dd>
  <dt>IPEDS unit id</dt>
  <dd>{html.escape(str(row.get("unit_id")) if row.get("unit_id") else "not published")}</dd>
</dl>
<h2>What was disclosed</h2>
<table>
<caption>Every field this project checks, and how this institution disclosed it.</caption>
<thead><tr><th scope="col">Field</th><th scope="col">Status</th>
<th scope="col">What that means</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
{findings_html}
<p class="caveat">This is a measure of disclosure, not of quality. An institution that reports
everything and performs badly scores higher here than a good school that reports nothing, and
that is intended. If you believe a field is marked wrongly, the
<a href="../../methodology/">methodology</a> states every rule and the reasoning behind it.</p>
{_ask_widget(str(row.get("unit_id")), ask_endpoint) if ask_endpoint else ""}
"""
    # The name alone does not identify an institution, and this project of all
    # projects should not pretend otherwise. Two institutions in the committed
    # report are both called "Glendale Community College": unit 104708 in
    # Arizona, graded B, and unit 115001 in California, graded D. Titled by
    # name alone they were the same string, so a result list showed one page
    # twice and a reader had no way to tell which grade belonged to which
    # school. That is this project's own subject matter -- two different facts
    # rendered identically -- appearing in its own <head>.
    #
    # The state is already on the page, in the breadcrumb and in the facts
    # list, so naming it here adds nothing that was not published; it only
    # stops the head saying less than the body. Where the report publishes no
    # state, the qualifier is left off rather than filled in: an absence is not
    # a value here either.
    qualified = f"{name} ({state})" if state else name
    return Page(
        path=path,
        title=f"{qualified}: disclosure grade",
        description=(
            f"What {qualified} publishes and what it does not, graded on disclosure rather "
            f"than on performance. Disclosure score {_pct(score)}."
        ),
        body=body,
    )


# The one script the site can carry, and only when it is built with an endpoint. It is inline
# (no ``src``, so no second file is fetched), it registers a submit handler and does nothing
# else at load, and its single network call sits inside that handler: nothing leaves the page
# until the reader presses Ask. Everything it renders is built from DOM nodes with textContent,
# never from markup, so a reply cannot inject anything into the page. A failed or rate-limited
# request leaves the page exactly as it was, with one sentence saying so.
_ASK_SCRIPT: Final[str] = """(function () {
  var form = document.querySelector("form.ask-form");
  if (!form) { return; }
  var out = document.getElementById("ask-answer");
  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    if (cls) { node.className = cls; }
    return node;
  }
  function list(items, render) {
    var ul = el("ul");
    items.forEach(function (item) { ul.appendChild(render(item)); });
    return ul;
  }
  function show(answer) {
    out.replaceChildren();
    out.appendChild(el("p", answer.label, "ask-label"));
    if (answer.error) {
      out.appendChild(el("p", answer.error, "ask-error"));
      return;
    }
    if (answer.refusal) {
      out.appendChild(el("p", answer.refusal.message));
      if (answer.refusal.known && answer.refusal.known.length) {
        out.appendChild(el("p", "What is known instead:"));
        out.appendChild(list(answer.refusal.known, function (k) { return el("li", k); }));
      }
      return;
    }
    if (answer.claims.length) {
      out.appendChild(list(answer.claims, function (c) {
        var li = el("li", c.text);
        li.appendChild(el("span", " [" + c.cites.join(", ") + "]", "ask-cite"));
        return li;
      }));
    }
    if (answer.quotes.length) {
      out.appendChild(el("p", "From the federal source, verbatim:"));
      out.appendChild(list(answer.quotes, function (q) {
        var li = el("li");
        li.appendChild(el("q", q.quote));
        var src = q.source || {};
        li.appendChild(el("span", " (" + (src.publisher || "") + ", " + (src.locator || "") +
          ", retrieved " + (src.retrieved || "") + ")", "ask-cite"));
        if (q.note) { li.appendChild(el("p", q.note, "ask-note")); }
        return li;
      }));
    }
    if (answer.could_not_answer) { out.appendChild(el("p", answer.could_not_answer)); }
    var w = answer.withheld || { claims: 0, quotes: 0 };
    out.appendChild(el("p", "Withheld by the verifier: " + w.claims + " statement(s), " +
      w.quotes + " quote(s).", "ask-withheld"));
    if (!answer.claims.length && !answer.quotes.length && !answer.could_not_answer) {
      out.appendChild(el("p",
        "Nothing could be verified against the records, so nothing is shown."));
    }
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var question = form.elements.question.value.trim();
    if (!question) { return; }
    out.replaceChildren(el("p", "Asking\u2026"));
    fetch(form.dataset.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, institution: form.dataset.unitId })
    }).then(function (response) { return response.json(); }).then(show).catch(function () {
      out.replaceChildren(el("p",
        "The question service is unavailable or at its limit. This page is unchanged."));
    });
  });
})();"""


def _ask_widget(unit_id: str, endpoint: str) -> str:
    """The opt-in question form for one institution, and the script behind it.

    Rendered only when the site is built with ``--ask-endpoint``. The section says what it is
    before the reader types anything: optional, nothing sent until Ask is pressed, answers
    AI-generated and unofficial, about disclosure and never about quality.
    """
    return f"""
<section class="ask" aria-labelledby="ask-heading">
<h2 id="ask-heading">Ask about this institution's disclosure</h2>
<p>Optional. Nothing is sent anywhere until you press Ask. Answers are AI-generated and
unofficial: they describe what this institution disclosed to federal sources and why an absence
might be there, never how it performs, and every statement shown was checked against this
project's own records. Questions about quality, rankings or whether to attend are refused.</p>
<form class="ask-form" data-endpoint="{html.escape(endpoint)}"
      data-unit-id="{html.escape(unit_id)}">
<label for="ask-question">Your question</label>
<input id="ask-question" name="question" type="text" maxlength="600" required
       placeholder="What does this college not report?">
<button type="submit">Ask</button>
</form>
<div id="ask-answer" class="ask-answer" aria-live="polite"></div>
</section>
<script>{_ASK_SCRIPT}</script>
"""


def state_page(summary: dict[str, Any], rows: list[dict[str, Any]]) -> Page:
    """One state: how its institutions disclose, and which fields go unreported most often."""
    code = str(summary.get("label", ""))
    graded = summary.get("graded", 0)
    ungradeable = summary.get("ungradeable", 0)
    mean = summary.get("mean_score")

    listed = []
    for row in sorted(rows, key=lambda r: (_name_of(r).casefold(), str(r.get("unit_id")))):
        path = _institution_path(row)
        name = html.escape(_name_of(row))
        linked = f'<a href="../../{path}/">{name}</a>' if path else name
        listed.append(
            f'<tr><th scope="row">{linked}</th><td>{_grade_badge(row.get("letter"))}</td>'
            f"<td>{html.escape(_pct(row.get('score')))}</td></tr>"
        )

    worst = "".join(
        f"<li>{_rationale_link(str(label), str(label), depth=2)}: {int(count)} institutions</li>"
        for label, count in summary.get("worst_fields", [])
    )
    ungradeable_note = (
        f"<p>{ungradeable} of these could not be graded at all, because every field this project "
        "checks was either suppressed or inapplicable. They are counted here but excluded from "
        "the mean, so a state where most records are unreadable cannot pass as a state that "
        "scored well.</p>"
        if ungradeable
        else ""
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="../../">All institutions</a></nav>
<h1>{html.escape(code)}: what institutions disclose</h1>
<p class="lede">{graded} graded institutions, mean disclosure {html.escape(_pct(mean))}.</p>
{ungradeable_note}
<h2>Least-reported fields in {html.escape(code)}</h2>
<ul class="worst">{worst}</ul>
<h2>Institutions</h2>
<table>
<caption>Every institution graded in this state, with its disclosure score. An institution with
no gradeable field shows as not gradeable rather than as a zero.</caption>
<thead><tr><th scope="col">Institution</th><th scope="col">Grade</th>
<th scope="col">Disclosure</th></tr></thead>
<tbody>{"".join(listed)}</tbody>
</table>
<p class="caveat">Grades measure disclosure, not quality. See the
<a href="../../methodology/">methodology</a>.</p>
"""
    return Page(
        path=f"state/{slug(code)}",
        title=f"{code}: college disclosure grades",
        description=(
            f"{graded} institutions in {code} graded on what they report to the College "
            f"Scorecard. Mean disclosure {_pct(mean)}."
        ),
        body=body,
    )


def methodology_page() -> Page:
    """Every rule, every bound, and the reasoning behind each, at a stable anchor.

    This is the page every finding links to. It exists so that a graded institution arguing with a
    grade is arguing with a stated rule rather than guessing at one, which is the difference
    between a scorecard and an accusation.
    """

    def render(field: Field) -> str:
        if field.text_is_a_value:
            # A URL column has no credible range to state; what it has is a rule about who the
            # field applies to at all, and that is what a reader needs in its place.
            terms = (
                "Graded on whether an address was published, not on what is behind it; no page "
                "is ever fetched."
            )
            if field.applies_when is not None:
                terms += (
                    " Institutions the requirement does not reach leave the denominator entirely "
                    "rather than being marked down."
                )
        else:
            zero = "a credible measurement" if field.zero_is_credible else "treated as an artifact"
            terms = (
                f"Credible range: {_bound(field.credible_min, upper=False)} to "
                f"{_bound(field.credible_max, upper=True)}. An exact zero is {zero}."
            )
        return (
            f'<section id="{field.anchor}">'
            f"<h3>{html.escape(field.label)}</h3>"
            f"<p><code>{html.escape(field.key)}</code></p>"
            f"<p>{html.escape(field.rationale)}</p>"
            f'<p class="bounds">{terms} Weight {field.weight:g}.</p>'
            f"</section>"
        )

    # Printed from the constants that decide these things, never typed beside them. A grade band
    # and a drift threshold are the two numbers on this page that a graded institution is most
    # likely to check a specific decision against, and a page that states one figure while the
    # grader applies another is the failure this whole page exists to make impossible.
    first_threshold, first_letter = BANDS[0]
    bands = " ".join(
        [f"{first_letter}, {first_threshold:.0%} and above."]
        + [f"{letter}, {threshold:.0%}." for threshold, letter in BANDS[1:]]
        + [f"{BELOW_EVERY_BAND} below that."]
    )
    systemic = f"{SYSTEMIC_THRESHOLD * 100:g}"

    # Every field this project knows about is documented here, not only the ones in the report
    # being rendered. Findings link to these anchors by label, and a link into a rationale that
    # is not on the page is worse than no link, because it looks answered.
    scorecard_sections = "".join(render(f) for f in FIELDS)
    ipeds_sections = "".join(render(f) for f in IPEDS_FIELDS)
    classifications = "".join(
        f'<tr><th scope="row"><span class="tag tag-{d.value.replace("_", "-")}">'
        f"{html.escape(_DISCLOSURE_COPY[d][0])}</span></th>"
        f"<td>{html.escape(_DISCLOSURE_COPY[d][1])}</td>"
        f"<td>{'yes' if d.counts_against_publisher else 'no'}</td></tr>"
        for d in Disclosure
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="../">All institutions</a></nav>
<h1>How we grade</h1>
<p class="lede">Every credible range here is a judgement call, so every one carries a written
rationale. A scorecard that cannot be disputed line by line is not a scorecard, it is an
accusation.</p>

<h2>What is being measured</h2>
<p>Not quality. The grade answers one question: of the things this institution was in a position
to tell the public, how much did it actually tell them? A well-funded university with poor
outcomes that reports every field completely scores higher here than a good school that reports
nothing. Outcomes are graded elsewhere, by other people, using the very data this project is
checking the existence of.</p>

<h2>How an absent value is classified</h2>
<p>A field an institution did not report, a field suppressed to protect a small cohort, and a
field whose true value is zero are three different facts. Rendered carelessly they all become
<code>0</code> on a page, and a reader cannot tell a college that admits nobody from a college
that declined to say. Every value is classified before anything else touches it.</p>
<table>
<caption>The five ways a value can be absent or present, and which of them an institution is
answerable for.</caption>
<thead><tr><th scope="col">Classification</th><th scope="col">Meaning</th>
<th scope="col">Counts against the institution?</th></tr></thead>
<tbody>{classifications}</tbody>
</table>
<p>Suppression is a policy decision made for good reasons and is never held against anyone.
Penalising an institution for protecting a twelve-person cohort would push publishers toward
disclosing things they should not, which is the opposite of the point. Suppressed and
not-applicable fields leave the denominator rather than scoring zero, and an institution with an
empty denominator gets <strong>no grade at all</strong>, not a zero.</p>

<h2>Why a published value can still be a failure</h2>
<p>A value being present is not sufficient; it also has to be credible for the field it sits in.
A published zero survives in federal data because zero is a legal number, not because anyone
measured it. A wrong number published as fact is worse than a gap, because the gap is visible to
a reader and the wrong number is not.</p>

<h2>Peer comparison</h2>
<p>A fixed credible range is a blunt instrument and an institution can reasonably object to one.
So every implausible finding carries its peer group: sector, level, and state, with the
institution excluded from its own comparison. A reader who thinks a grade is unfair can see what
comparable institutions published and attack the peer definition, the sample, or the conclusion.
All three are better arguments to be having than one about where a constant was set.</p>
<p>Where the peers turn out to publish the same value, the verdict says so and the finding
argues against itself. That is intended and is not suppressed.</p>
<p>A peer claim is only made where at least {MIN_PEERS} comparable institutions exist
<em>and</em> at least {MIN_PEERS} of them published the field. Both counts, because the second is
what the comparison is made out of: a group of fifty in which six published a number supports a
claim about six, and the finding says so instead of borrowing the confidence of the fifty. Below
either bar the finding is still reported and still links here; it simply arrives without a peer
comparison attached, because an unsupported comparison is worse than none.</p>

<h2>Letter bands</h2>
<p>{bands} Fixed rather than curved, because
grading on a curve would hide a field-wide collapse in reporting: if everyone stopped publishing
graduation rates tomorrow, a curve would report that as normal.</p>

<h2>Drift</h2>
<p>A single snapshot cannot distinguish a field that was never collected from one that was
collected until recently and then stopped. The first is a gap in the data model; the second is a
change in what the public is allowed to know. Only the comparison between runs can tell them
apart, so per-field counts are committed to version control on every run.</p>
<p><strong>Drift is a change in rate, not a change in count.</strong> Every comparison divides by
the institutions the field applied to in that run, and the reason is a mistake this project made
and published to itself. Measured on counts, three IPEDS collection years produced three confident
systemic findings and all three were false: between 2021 and 2023 the directory shrank from 6,289
institutions to 6,163, so 130 fewer published a web address, and that was reported as a systemic
2.1% collapse. The share publishing one had gone <em>up</em>, from 99.93% to 99.95%. Colleges
closed; they did not stop reporting. The one real movement in the period, the athletics disclosure
rising from 57.1% to 59.4%, ranked fourth and was never flagged, because 52 is a small number next
to 130.</p>
<p>A change is called systemic when that rate moves by at least {systemic} percentage points. The
threshold is a judgement call, stated here so a reader can disagree with it, and set low because a
coordinated stop-reporting event is newsworthy well before it touches a majority of institutions.
Three real collection years say it is roughly right: every year-on-year movement in these six
disclosures sits under one point except the athletics disclosure, which rose 1.75 points in a year
and 2.26 across two. At {systemic}% the bar flags that and nothing else. At 1% it would report
ordinary annual churn as policy; at 5% it would have found nothing in three years of federal data,
which is not a measurement but a way of never having to say anything.</p>
<p>Drift is reported in both directions: fields that <em>started</em> being reported are as real a
finding as fields that stopped, and reporting only the losses would make this an argument rather
than a measurement. A field whose rate cannot be computed in either run is reported as unmeasured
and is never called systemic, because an unknown is not a large movement.</p>

<h2>The fields: College Scorecard</h2>
{scorecard_sections}

<h2>The fields: IPEDS</h2>
<p>IPEDS records public disclosures the Scorecard does not carry, and states absence three
different ways, all of them negative integers: -1 not reported, -2 not applicable, -3 not
available. They are not interchangeable, and only the first counts against an institution.</p>
<p>The athletics disclosure below was ungraded until a second IPEDS file arrived, and the reason is
worth stating. It is blank for 4,469 of 6,163 directory rows, and almost every one of those is a
college with no athletics programme, so grading the column against the directory alone would have
produced four thousand confident and entirely fabricated violations. The institutional
characteristics file carries each institution's own answer about whether it competes, and that
answer moves the denominator from 6,163 to 1,998. The finding did not need a better threshold. It
needed to know who the rule applied to.</p>
<p>One candidate is still deliberately not graded. The veterans information page is blank for 2,377
institutions, and the same characteristics file would now supply an applicability rule for it. It
stays ungraded because applicability was never the obstacle here: no universal requirement obliges
an institution to publish a veterans page, so a rule about who it applied to would be a rule about
a duty that does not exist. Knowing who would owe a disclosure is not the same as there being one
to owe.</p>
{ipeds_sections}

<h2>If this is wrong about your institution</h2>
<p>The rules above are the whole of it; there is no model anywhere in the grading path and no
judgement that is not written down on this page. If a field is marked wrongly, the disagreement
will be with a stated bound, a stated peer group, or the underlying federal record, and all three
can be checked.</p>
"""
    return Page(
        path="methodology",
        title="How the disclosure grades are calculated",
        description=(
            "Every credible range, the reasoning behind it, how suppressed and not-applicable "
            "fields leave the denominator, and why an ungradeable institution gets no grade "
            "rather than a zero."
        ),
        body=body,
    )


def _bound(value: float | None, *, upper: bool) -> str:
    """Render one end of a credible range, in a notation a prospective student will read.

    Never ``g``. Past four significant digits it switches to exponent form and the thousands
    separator stops applying, so four of the six Scorecard ceilings were published as ``5e+05``,
    ``4e+05``, ``1.5e+05`` and ``2.5e+05`` on the one page whose entire job is to be argued with by
    people who are not statisticians. The rationale directly above each of them says "$1,000" and
    "aggregate federal borrowing limits" in plain words, and then the generated line underneath
    said ``4e+05``.

    Fixed-point with the trailing zeros trimmed rather than ``,.0f``, so that a bound which is not
    a whole number stays honest. Every bound defined today is integral and ``,.0f`` would be
    correct for all of them; it would also silently round a future credible range of 0.5 to "0",
    which is a rule misstated on the page that states the rules.
    """
    if value is None:
        return "no upper bound" if upper else "no lower bound"
    return html.escape(f"{value:,.4f}".rstrip("0").rstrip("."))


def _share(numerator: int, denominator: int) -> str:
    """A percentage, or words when there is nothing to divide by.

    ``0%`` is a real answer to "what share reported this" and must not also be the answer to
    "there was nobody to ask". A denominator of zero returns the sentence rather than the number.
    """
    if denominator <= 0:
        return "no applicable institutions"
    return f"{numerator / denominator:.0%}"


def national_page(payload: dict[str, Any]) -> Page:
    """The one page whose percentages describe the country rather than a slice of it.

    Kept as its own page rather than merged into the home page, because the two rest on different
    corpora and a reader who lands halfway down a page must never be able to carry a national
    figure back up to a sample one or the other way round. The scope sentence is printed from the
    artifact, not from this template, so a page rendered from a different run says what that run
    covered rather than what this paragraph was written believing.
    """
    scope = scope_from_payload(payload)
    fields: list[dict[str, Any]] = list(payload.get("fields", []))
    gaps: dict[str, Any] = payload.get("gaps", {}) or {}

    rows = "".join(
        f'<tr><th scope="row">'
        f"{_rationale_link(str(f.get('label', '')), str(f.get('label', '')), depth=1)}</th>"
        f"<td>{int(f.get('applicable', 0)):,}</td>"
        f"<td>{int(f.get('missing', 0)):,}</td>"
        f"<td>{html.escape(_share(int(f.get('reported', 0)), int(f.get('applicable', 0))))}</td>"
        f"<td>{html.escape(str(f.get('statute')) or 'no statute')}</td></tr>"
        for f in fields
    )

    sections = []
    for field in fields:
        label = str(field.get("label", ""))
        listed = gaps.get(label)
        if not isinstance(listed, list) or not listed:
            continue
        items = "".join(
            f"<li>{html.escape(str(row.get('name') or 'Unnamed institution'))}"
            f"{html.escape(' (' + str(row.get('state')) + ')') if row.get('state') else ''}"
            f"{'' if row.get('unit_id') else ' <span class="tag">no unit id published</span>'}"
            "</li>"
            for row in listed
            if isinstance(row, dict)
        )
        sections.append(
            f"<h3>{_rationale_link(label, label, depth=1)}: "
            f"{len(listed):,} of {int(field.get('applicable', 0)):,} institutions</h3>"
            f"<p>Required by {html.escape(str(field.get('statute', '')))}. These are the "
            "institutions the requirement reaches for which the federal record carries no "
            "address. Named rather than counted because there is a published rule behind this "
            "one; the fields with no statute behind them are counted above and their institutions "
            "are not listed.</p>"
            f'<ul class="gaps">{items}</ul>'
        )

    # Read off the table rather than written into the sentence. The paragraph below explains the
    # applicable column by naming the narrowest and widest disclosure in it, and those two numbers
    # move every collection year; hardcoded, the prose would go on citing 2023's denominators
    # underneath a table showing another year's, which is the failure this page exists to describe.
    reach = sorted(int(f.get("applicable", 0)) for f in fields)
    spread = (
        f"A disclosure that reaches {reach[0]:,} institutions and a disclosure that reaches "
        f"{reach[-1]:,} produce"
        if len(reach) >= 2 and reach[0] != reach[-1]
        else "Two disclosures that reach different numbers of institutions produce"
    )

    lede = html.escape(scope.sentence) if scope else "This run did not state its coverage."
    ungradeable = int(payload.get("ungradeable", 0))
    ungradeable_note = (
        f"<p>{ungradeable:,} directory rows get no grade at all rather than a zero, because every "
        "field checked was either suppressed or outside the reach of the rule. Most are system "
        "offices and closed institutions. They are counted here and excluded from every mean.</p>"
        if ungradeable
        else ""
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="../">All institutions</a></nav>
<h1>The national picture</h1>
<p class="lede">{lede}</p>
<p>Everything else on this site is graded from a sample of the College Scorecard and says so. This
page is different: IPEDS publishes its directory as a file rather than as a paged API, so grading
it grades every institution there is, and the percentages below describe the country.</p>

<h2>What the country discloses</h2>
<table>
<caption>Per-field national counts. Suppressed and inapplicable institutions are outside the
applicable column, never scored as failures.</caption>
<thead><tr><th scope="col">Disclosure</th><th scope="col">Institutions it reaches</th>
<th scope="col">Record carries none</th><th scope="col">Published</th>
<th scope="col">Requirement</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p>The middle column is the whole argument. {spread} very different-looking failure counts from the
same underlying behaviour, and a table that showed only the failures would rank them wrongly.</p>
{ungradeable_note}

<h2>Named findings</h2>
{"".join(sections) or "<p>No statute-backed disclosure is absent anywhere in this run.</p>"}

<p class="caveat">An absent address means the federal record carries none. It is not proof the
institution has nothing: it may have published the thing and not reported where. Which of those
two is true is not something a blank cell can settle, and this page does not pretend otherwise.
The <a href="../methodology/">methodology</a> states the rule behind every row.</p>
"""
    return Page(
        path="national",
        title="What US colleges disclose, nationally",
        description=(
            "Per-field disclosure counts across every institution in the IPEDS directory, with "
            "the applicability rule behind each and the institutions named where a statute "
            "requires the disclosure."
        ),
        body=body,
    )


def _share_of(count: int, total: int) -> str:
    return f"{count / total:.1%}" if total else "no institutions"


def scorecard_census_page(payload: dict[str, Any]) -> Page:
    """The full College Scorecard walk, beside the 600-institution sample it does not replace.

    #17 was opened over one fact: every published Scorecard figure came from 600 institutions in
    13 states, 51% of them Californian, because the API returns institutions grouped by state and
    the committed capture was the first page and a half. This page is the answer, and it is an
    addition rather than a correction -- the home page's sample figures are unchanged and still
    say what they have always said about the 600 institutions they describe. This page says the
    same six things about every institution the Scorecard publishes, and states the composition
    of both frames side by side so "how skewed was the sample" has a table instead of a sentence.
    """
    scope = scope_from_payload(payload)
    fields: list[dict[str, Any]] = list(payload.get("fields", []))
    comp = payload.get("composition") or {}
    sample_comp = payload.get("sample_composition") or {}
    comp_total = int(comp.get("institutions", 0))
    sample_total = int(sample_comp.get("institutions", 0))

    rows = "".join(
        f'<tr><th scope="row">'
        f"{_rationale_link(str(f.get('label', '')), str(f.get('label', '')), depth=1)}</th>"
        f"<td>{int(f.get('applicable', 0)):,}</td>"
        f"<td>{int(f.get('missing', 0)):,}</td>"
        f"<td>{html.escape(_share(int(f.get('reported', 0)), int(f.get('applicable', 0))))}</td>"
        "</tr>"
        for f in fields
    )

    census_sectors: dict[str, int] = comp.get("sectors", {})
    sample_sectors: dict[str, int] = sample_comp.get("sectors", {})
    sector_labels = sorted(
        set(census_sectors) | set(sample_sectors), key=lambda label: -census_sectors.get(label, 0)
    )
    sector_rows = "".join(
        f'<tr><th scope="row">{html.escape(str(label))}</th>'
        f"<td>{sample_sectors.get(label, 0):,}</td>"
        f"<td>{html.escape(_share_of(sample_sectors.get(label, 0), sample_total))}</td>"
        f"<td>{census_sectors.get(label, 0):,}</td>"
        f"<td>{html.escape(_share_of(census_sectors.get(label, 0), comp_total))}</td></tr>"
        for label in sector_labels
    )

    state_count_sample = len(sample_comp.get("states", {}))
    state_count_census = len(comp.get("states", {}))
    ca_sample = int(sample_comp.get("states", {}).get("CA", 0))
    ca_census = int(comp.get("states", {}).get("CA", 0))
    ca_sample_share = _share_of(ca_sample, sample_total)
    ca_census_share = _share_of(ca_census, comp_total)

    admission = next((f for f in fields if f.get("label") == "Admission rate"), None)
    headline = ""
    if admission is not None and admission.get("applicable"):
        missing = int(admission["missing"])
        applicable = int(admission["applicable"])
        headline = (
            f"<p>In the full census, <strong>{missing:,} of {applicable:,}, or "
            f"{missing / applicable:.1%}, publish no admission rate at all</strong> -- the same "
            "question the home page asks of the 600-institution sample, asked here of every "
            "institution the Scorecard publishes.</p>"
        )

    lede = html.escape(scope.sentence) if scope else "This run did not state its coverage."
    body = f"""
<nav aria-label="Breadcrumb"><a href="../">All institutions</a></nav>
<h1>The College Scorecard census</h1>
<p class="lede">{lede}</p>
<p>The home page's figures are graded from a 600-institution sample and say so. This page grades
every institution the College Scorecard publishes -- the API paged to exhaustion, proven from the
walk's own counts rather than assumed from how large the result looks -- and does not replace the
sample figures, because the sample is a real, separately-interesting slice and silently swapping
one number for another is the failure this project exists to name in other people's data.</p>

{headline}

<h2>Composition: how skewed was the sample</h2>
<p>{ca_sample:,} of the sample's {sample_total:,} institutions ({ca_sample_share}) are
Californian, across {state_count_sample} states. The census, {comp_total:,} institutions across
{state_count_census} states, puts California at {ca_census_share}.</p>
<table>
<caption>Institutions by sector, sample against census.</caption>
<thead><tr><th scope="col">Sector</th><th scope="col">Sample</th><th scope="col">Sample share</th>
<th scope="col">Census</th><th scope="col">Census share</th></tr></thead>
<tbody>{sector_rows}</tbody>
</table>

<h2>What the census discloses</h2>
<table>
<caption>Per-field counts across every institution the Scorecard publishes. Suppressed and
inapplicable institutions are outside the applicable column, never scored as failures.</caption>
<thead><tr><th scope="col">Disclosure</th><th scope="col">Institutions it reaches</th>
<th scope="col">Record carries none</th><th scope="col">Published</th></tr></thead>
<tbody>{rows}</tbody>
</table>

<p class="caveat">A field with no statute behind it is this project's opinion about what a college
ought to publish, not a legal requirement; the <a href="../methodology/">methodology</a> states
the rationale behind every row. Institutions are counted here and not named, the same rule the
sample figures on the home page follow.</p>
"""
    return Page(
        path="census",
        title="The College Scorecard census, in full",
        description=(
            "Every institution the College Scorecard publishes, graded the same way as the "
            "600-institution sample, with both frames' composition stated side by side."
        ),
        body=body,
    )


def home_page(
    report: dict[str, Any], *, has_national: bool = False, has_scorecard_census: bool = False
) -> Page:
    """The landing page: the thesis, what this run found, and where the numbers stop applying."""
    overall = report.get("overall", {})
    total = int(report.get("institutions", 0))
    ungradeable = int(report.get("ungradeable", 0))
    implausible = report.get("implausible", [])
    by_state = report.get("by_state", [])
    mean = overall.get("mean_score")

    worst = "".join(
        f'<tr><th scope="row">{_rationale_link(str(label), str(label), depth=1)}</th>'
        f"<td>{int(count)}</td>"
        f"<td>{int(count) / total:.0%}</td></tr>"
        for label, count in overall.get("worst_fields", [])
        if total
    )
    states = "".join(
        f'<li><a href="state/{html.escape(slug(str(s.get("label", ""))))}/">'
        f"{html.escape(str(s.get('label', '')))}</a> "
        f"({int(s.get('graded', 0))}, {html.escape(_pct(s.get('mean_score')))})</li>"
        for s in sorted(by_state, key=lambda s: str(s.get("label", "")))
    )
    artifacts = "".join(
        f'<li><a href="{html.escape(_institution_path(f) or "")}/">'
        f"{html.escape(_name_of(f))}</a> publishes "
        f"{_rationale_link(str(f.get('field', '')), str(f.get('field', '')), depth=1)} as "
        f"<code>{html.escape(json.dumps(f.get('value')))}</code></li>"
        if _institution_path(f)
        else (
            f"<li>{html.escape(_name_of(f))} publishes "
            f"{_rationale_link(str(f.get('field', '')), str(f.get('field', '')), depth=1)} as "
            f"<code>{html.escape(json.dumps(f.get('value')))}</code></li>"
        )
        for f in implausible
    )
    ungradeable_note = (
        f"<p>{ungradeable} institutions could not be graded at all, because every field checked "
        "was suppressed or inapplicable. They get no grade rather than a zero, and they are "
        "counted separately from the mean so they cannot pass as institutions that scored well."
        "</p>"
        if ungradeable
        else ""
    )
    # Printed from the scope the run recorded, never from a constant in this template. A caveat
    # written into a template stays true only until somebody renders a different report through
    # it, and the sentence this one carries is the one thing on the page a reader must be able to
    # trust without checking anything else.
    scope: Scope | None = scope_from_payload(report)
    if scope is None:
        coverage = (
            '<p class="caveat"><strong>What this run covers.</strong> This report predates the '
            "coverage record and does not say how much of the College Scorecard it holds. Treat "
            "every percentage on this page as describing the institutions listed here and nothing "
            "wider, because nothing wider has been established.</p>"
        )
    else:
        national_pointer = (
            ' The <a href="national/">national page</a> carries the figures that do describe the '
            "country, drawn from a source published as a whole file rather than as a paged API."
            if has_national and not scope.is_national
            else ""
        )
        census_pointer = (
            ' The <a href="census/">Scorecard census page</a> asks the same questions of every '
            "institution the College Scorecard publishes, not this sample, and states both "
            "frames' composition side by side."
            if has_scorecard_census and not scope.is_national
            else ""
        )
        coverage = (
            f'<p class="caveat"><strong>What this run covers.</strong> '
            f"{html.escape(scope.sentence)} {html.escape(scope.note)} A project about undisclosed "
            "information should not be coy about the limits of its own sample."
            f"{national_pointer}{census_pointer}</p>"
        )
    body = f"""
<h1>disclosed</h1>
<p class="lede">Grades US higher-education institutions on what they disclose, not on how they
perform.</p>
<p>Plenty of tools will tell you a college's graduation rate. None will tell you how many
colleges never reported one, or which fields quietly stopped being published this year. That is
what this grades.</p>
<p>The distinction matters because the two failures look identical on a page. A college with a 0%
admission rate and a college that never reported an admission rate both render as a blank or a
zero in most tools, and a reader cannot tell them apart.</p>

<h2>What this run found</h2>
<dl class="facts">
  <dt>Institutions graded</dt><dd>{total}</dd>
  <dt>Mean disclosure</dt><dd>{html.escape(_pct(mean))}</dd>
  <dt>Not gradeable</dt><dd>{ungradeable}</dd>
  <dt>Published values that are not measurements</dt><dd>{len(implausible)}</dd>
</dl>
{ungradeable_note}

<h2>Least-reported fields</h2>
<table>
<caption>Fields most often absent across the institutions in this run. Shares are of this run,
not of the country.</caption>
<thead><tr><th scope="col">Field</th><th scope="col">Institutions not reporting it</th>
<th scope="col">Share</th></tr></thead>
<tbody>{worst}</tbody>
</table>

<h2>Published zeros that are not zeros</h2>
<p>These institutions published a value rather than leaving a gap, and the value is not a
plausible measurement of the thing it claims to measure. Each carries the peer group that
supports or undermines the finding.</p>
<ul class="findings">{artifacts}</ul>

<h2>By state</h2>
<ul class="states">{states}</ul>

<h2>How this works</h2>
<p>Read the <a href="methodology/">methodology</a>: every credible range, the reasoning behind
it, and why a suppressed field is never held against an institution.</p>
{coverage}
"""
    return Page(
        path="",
        # Not "disclosed: what US colleges do not tell you". _shell appends
        # " | disclosed" to every title, so that one rendered as "disclosed:
        # what US colleges do not tell you | disclosed" -- the site's name
        # twice in fifty-four characters, on the one page most likely to be
        # seen in a result list.
        title="What US colleges do not tell you",
        description=(
            "Grades US higher-education institutions on what they disclose rather than on how "
            "they perform. Which fields go unreported, which published values are not "
            "measurements, and what stopped being published."
        ),
        body=body,
    )


_STYLE: Final[str] = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, sans-serif; max-width: 52rem; margin: 0 auto;
       padding: 1.5rem 1rem 4rem; line-height: 1.55; color: #1a1a1a; background: #fff; }
a { color: #0b5cad; }
/* Available to a screen reader and to nothing else. Used where a visual cue carries meaning that
   would otherwise be lost, such as the letter in a grade badge. Clip rather than display:none,
   which removes the text from the accessibility tree along with the screen. */
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden;
                   clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
/* The skip link is off-screen until focused, then lands in the top-left corner. A keyboard user
   should not have to tab through a breadcrumb on 616 pages to reach the content of any of them. */
.skip { position: absolute; left: -9999px; top: 0; background: #fff; color: #0b5cad;
        padding: .6rem 1rem; border: 2px solid currentColor; border-radius: 0 0 4px 0; }
.skip:focus { left: 0; z-index: 10; }
/* An explicit focus ring, because the custom link colours make the browser default hard to see
   in dark mode. Two-colour outline so it stays visible against both backgrounds. */
:focus-visible { outline: 3px solid #0b5cad; outline-offset: 2px; }
caption { text-align: left; font-size: .9rem; color: #555; padding-bottom: .4rem; }
nav[aria-label="Breadcrumb"] { font-size: .9rem; margin-bottom: .5rem; }
h1 { line-height: 1.2; }
h2 { margin-top: 2rem; }
.lede { font-size: 1.1rem; color: #333; }
.grade { display: inline-block; min-width: 1.7em; text-align: center; border-radius: 4px;
         padding: 0 .35em; color: #fff; background: #555; font-weight: 700; }
.grade-a { background: #14691f; } .grade-b { background: #3f7d20; }
.grade-c { background: #8a5a00; } .grade-d { background: #a8421f; }
.grade-f { background: #96110f; } .grade-none { background: #555; font-size: .8em; }
.tag { display: inline-block; border-radius: 3px; padding: .05em .45em; font-size: .85em;
       border: 1px solid currentColor; white-space: nowrap; }
.tag-reported { color: #14691f; } .tag-implausible { color: #96110f; }
.tag-missing { color: #a8421f; } .tag-suppressed { color: #555; }
.tag-not-applicable { color: #555; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #e3e3e3;
         vertical-align: top; }
dl.facts { display: grid; grid-template-columns: auto 1fr; gap: .3rem .9rem; }
dl.facts dt { font-weight: 600; }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
ul.states { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: .3rem 1.2rem; }
ul.findings { padding-left: 1.1rem; }
ul.findings li { margin-bottom: 1rem; }
.peers { font-size: .92rem; margin: .3rem 0; }
.why { font-size: .9rem; color: #555; margin: .3rem 0; }
.bounds { font-size: .9rem; color: #555; }
section[id] { scroll-margin-top: 1rem; border-left: 3px solid #e3e3e3; padding-left: .9rem;
              margin-bottom: 1.2rem; }
.caveat { font-size: .9rem; color: #555; border-top: 1px solid #e3e3e3; padding-top: .8rem;
          margin-top: 2rem; }
footer { margin-top: 3rem; font-size: .9rem; color: #555; }
.ask { margin-top: 2.5rem; border-top: 1px solid #e3e3e3; padding-top: .5rem; }
.ask-form { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
.ask-form label { flex-basis: 100%; font-weight: 600; }
.ask-form input { flex: 1 1 18rem; padding: .5rem; font: inherit; }
.ask-form button { padding: .5rem 1rem; font: inherit; }
.ask-answer { margin-top: 1rem; }
.ask-label, .ask-cite, .ask-note, .ask-withheld { font-size: .9rem; color: #555; }
.ask-error { color: #a8421f; }
@media (prefers-color-scheme: dark) {
  body { background: #131313; color: #e9e9e9; }
  a { color: #79b8ff; }
  .lede { color: #cfcfcf; }
  th, td, section[id] { border-color: #333; }
  .skip { background: #131313; color: #79b8ff; }
  :focus-visible { outline-color: #79b8ff; }
  caption { color: #bbb; }
  .why, .bounds, .caveat, footer { color: #bbb; }
  .ask-label, .ask-cite, .ask-note, .ask-withheld { color: #bbb; }
  .ask-error { color: #ffab7a; }
  .caveat, .ask { border-color: #333; }
  .tag-reported { color: #6fbf73; } .tag-implausible { color: #ff8a80; }
  .tag-missing { color: #ffab7a; } .tag-suppressed { color: #bbb; }
  .tag-not-applicable { color: #bbb; }
}
"""


def _shell(page: Page, *, canonical: str, origin: str, generated: str) -> str:
    """One page, including what a search result and a link preview will say about it.

    The share card repeats this page's own title and description rather than a second set written
    for sharing, which would be an unreviewed description of the project published where nobody
    rereads it. The image is the one part it does not take from the page, because there is only
    one: ``og-card.png``, written into the site root by :func:`build` and named here at an
    absolute address off ``origin``, which is the only kind of address a crawler on another host
    can resolve.
    """

    # Every in-page link is relative, and it has to stay that way. This site is
    # served at a path under an origin five sibling projects also publish
    # under, and https://chelseakr.github.io/ is itself a 404, so an
    # `href="/methodology/"` would not be a shorter way of writing the link: it
    # would resolve against the origin and land on another project or on
    # nothing. `root` is why there is no root-relative href here, and
    # .github/scripts/check_site_origin.py refuses a build that grows one.
    root = "../" * page.path.count("/") + ("../" if page.path else "")
    card = html.escape(f"{origin}/{OG_CARD_NAME}")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page.title)} | disclosed</title>
<meta name="description" content="{html.escape(page.description)}">
<link rel="canonical" href="{html.escape(canonical)}">
<meta property="og:title" content="{html.escape(page.title)}">
<meta property="og:description" content="{html.escape(page.description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="disclosed">
<meta property="og:locale" content="en_US">
<meta property="og:url" content="{html.escape(canonical)}">
<meta property="og:image" content="{card}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="{OG_CARD_WIDTH}">
<meta property="og:image:height" content="{OG_CARD_HEIGHT}">
<meta property="og:image:alt" content="{html.escape(OG_CARD_ALT)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html.escape(page.title)}">
<meta name="twitter:description" content="{html.escape(page.description)}">
<meta name="twitter:image" content="{card}">
<meta name="twitter:image:alt" content="{html.escape(OG_CARD_ALT)}">
<style>{_STYLE}</style>
</head>
<body>
<a class="skip" href="#content">Skip to content</a>
<main id="content">
{page.body}
</main>
<footer>
<p>Generated {html.escape(generated)} from public federal data. This grades disclosure, not
quality, and says so on every page. <a href="{root}methodology/">Methodology</a>.</p>
<p>Every rule behind these grades, the data they are computed from, and the code that applies
them are public: <a href="{html.escape(SOURCE_URL)}">github.com/ChelseaKR/disclosed</a>.</p>
</footer>
</body>
</html>
"""


def _corpus_pages(
    report: dict[str, Any],
    *,
    national: dict[str, Any] | None,
    scorecard_census: dict[str, Any] | None,
) -> list[Page]:
    """The pages that describe a corpus as a whole, rather than one institution or state.

    Split out of :func:`build` so that adding a third corpus-level page is a change to this
    function's short body rather than a rise in ``build``'s own branching, which is otherwise the
    function that walks every institution and writes every file.
    """
    pages: list[Page] = [
        home_page(
            report,
            has_national=national is not None,
            has_scorecard_census=scorecard_census is not None,
        ),
        methodology_page(),
    ]
    if scorecard_census is not None:
        pages.append(scorecard_census_page(scorecard_census))
    if national is not None:
        pages.append(national_page(national))
    return pages


def build(
    report: dict[str, Any],
    out_dir: Path,
    *,
    origin: str = DEFAULT_ORIGIN,
    generated: str,
    national: dict[str, Any] | None = None,
    scorecard_census: dict[str, Any] | None = None,
    ask_endpoint: str | None = None,
) -> list[Page]:
    """Render the whole site from a graded report.

    Args:
        report: A payload as written by ``disclosed grade``.
        out_dir: Directory to write into. Created if absent; existing files are overwritten.
        origin: Absolute base URL, used only for canonical links.
        generated: Run identifier shown in the footer. Supplied by the caller rather than read
            from the clock, so that rebuilding the same report is byte-identical and a diff in the
            output means the data changed.
        national: A payload as written by ``disclosed national``, or ``None``. Without it no
            national page is written and the site makes no national claim anywhere, which is the
            right default: the absence of a national corpus must show up as the absence of
            national figures, not as sample figures with the qualifier quietly dropped.
        scorecard_census: A payload as written by ``disclosed census-report``, or ``None``.
            Without it no census page is written and the site's Scorecard figures describe the
            600-institution sample only, exactly as before #17 -- the same "absence over
            assertion" default as ``national``.
        ask_endpoint: The URL of a running ``disclosed.ask`` service, or ``None``. With it,
            every institution page carries the opt-in question form and one inline script;
            without it the build is byte-for-byte what it was, with no script anywhere.

    Returns:
        Every page written, in the order written. Callers use it to assert page counts without
        walking the filesystem.
    """
    grades: list[dict[str, Any]] = list(report.get("grades", []))
    findings_by_id: dict[str, list[dict[str, Any]]] = {}
    for finding in report.get("implausible", []):
        unit_id = finding.get("unit_id")
        if isinstance(unit_id, str) and unit_id:
            findings_by_id.setdefault(unit_id, []).append(finding)

    pages: list[Page] = _corpus_pages(report, national=national, scorecard_census=scorecard_census)

    by_state: dict[str, list[dict[str, Any]]] = {}
    for row in grades:
        state = row.get("state")
        by_state.setdefault(state if isinstance(state, str) and state else "unknown", []).append(
            row
        )
    for summary in sorted(report.get("by_state", []), key=lambda s: str(s.get("label", ""))):
        code = str(summary.get("label", ""))
        if not slug(code):
            continue
        pages.append(state_page(summary, by_state.get(code, [])))

    for row in sorted(grades, key=lambda r: str(r.get("unit_id"))):
        path = _institution_path(row)
        if path is None:
            # Counted in the state listings, but given no URL. See _institution_path.
            continue
        unit_id = str(row.get("unit_id"))
        pages.append(
            institution_page(
                row, findings_by_id.get(unit_id, []), path=path, ask_endpoint=ask_endpoint
            )
        )

    for page in pages:
        target = out_dir / page.path if page.path else out_dir
        target.mkdir(parents=True, exist_ok=True)
        canonical = f"{origin}/{page.path + '/' if page.path else ''}"
        (target / "index.html").write_text(
            _shell(page, canonical=canonical, origin=origin, generated=generated),
            encoding="utf-8",
        )

    # The share card every page's og:image names. Written here, in the same pass that writes the
    # pages that promise it, so the promise and the file cannot come apart: a link preview is
    # fetched once, by a crawler on another host, and a 404 there is reported to nobody. It is a
    # byte copy rather than a render, so a rebuild of the same report stays byte-identical.
    (out_dir / OG_CARD_NAME).write_bytes(_OG_CARD_SOURCE.read_bytes())

    # robots.txt, written where this site lives rather than where robots.txt is
    # read. Worth being plain about, because the file looks like coverage it
    # does not provide: a crawler asks one URL per origin,
    # https://chelseakr.github.io/robots.txt, and this repository does not own
    # that path -- it is a 404, because there is no user site at that origin at
    # all. So the Sitemap: line below is not discovered by anything, and the
    # Allow: line changes nothing, since a missing robots.txt already means
    # "crawl freely".
    #
    # It is still written, and deliberately not removed. It is correct for
    # anyone who fetches it, it is what a reader looking for the sitemap will
    # try first, and check_site_origin.py holds its origin to the deploy
    # target. What it is not is a way to have the sitemap found: that needs the
    # sitemap submitted directly, which is the owner's action and not a file
    # this build can write.
    (out_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {origin}/sitemap.xml\n", encoding="utf-8"
    )
    entries = "".join(
        f"<url><loc>{html.escape(origin)}/{html.escape(p.path + '/' if p.path else '')}</loc></url>"
        for p in pages
    )
    (out_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>",
        encoding="utf-8",
    )
    return pages
