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
from .fields import FIELDS, IPEDS_FIELDS, Field, field_by_label
from .peers import MIN_PEERS
from .scope import Scope, scope_from_payload

__all__ = ["Page", "build", "slug"]

DEFAULT_ORIGIN: Final[str] = "https://chelseakr.github.io/disclosed"

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
    row: dict[str, Any], findings: list[dict[str, Any]], *, path: str
) -> Page:
    """One institution: its grade, every field's disclosure state, and any implausible values."""
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
                f"<p class=\"peers\">Peer check, "
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
                f"<p class=\"why\">{html.escape(str(finding.get('rationale', '')))}</p></li>"
            )
        findings_html = (
            "<h2>Values that do not look like measurements</h2>"
            "<p>These were published, so they are not gaps. They fall outside the credible range "
            "for their field, which is a judgement this project made and states in full so it can "
            "be argued with.</p>"
            f"<ul class=\"findings\">{''.join(items)}</ul>"
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
"""
    return Page(
        path=path,
        title=f"{name}: disclosure grade",
        description=(
            f"What {name} publishes and what it does not, graded on disclosure rather than on "
            f"performance. Disclosure score {_pct(score)}."
        ),
        body=body,
    )


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
        f"<li>{_rationale_link(str(label), str(label), depth=2)}: "
        f"{int(count)} institutions</li>"
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
    def bound(value: float | None, *, upper: bool) -> str:
        if value is None:
            return "no upper bound" if upper else "no lower bound"
        return html.escape(f"{value:,.4g}")

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
                f"Credible range: {bound(field.credible_min, upper=False)} to "
                f"{bound(field.credible_max, upper=True)}. An exact zero is {zero}."
            )
        return (
            f'<section id="{field.anchor}">'
            f"<h3>{html.escape(field.label)}</h3>"
            f"<p><code>{html.escape(field.key)}</code></p>"
            f"<p>{html.escape(field.rationale)}</p>"
            f'<p class="bounds">{terms} Weight {field.weight:g}.</p>'
            f"</section>"
        )

    # Every field this project knows about is documented here, not only the ones in the report
    # being rendered. Findings link to these anchors by label, and a link into a rationale that
    # is not on the page is worse than no link, because it looks answered.
    scorecard_sections = "".join(render(f) for f in FIELDS)
    ipeds_sections = "".join(render(f) for f in IPEDS_FIELDS)
    classifications = "".join(
        f"<tr><th scope=\"row\"><span class=\"tag tag-{d.value.replace('_', '-')}\">"
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
<p>A, 95% and above. B, 85%. C, 70%. D, 50%. F below that. Fixed rather than curved, because
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
<p>A change is called systemic when that rate moves by at least 2 percentage points. The threshold
is a judgement call, stated here so a reader can disagree with it, and set low because a
coordinated stop-reporting event is newsworthy well before it touches a majority of institutions.
Three real collection years say it is roughly right: every year-on-year movement in these six
disclosures sits under one point except the athletics disclosure, which rose 1.75 points in a year
and 2.26 across two. At 2% the bar flags that and nothing else. At 1% it would report ordinary
annual churn as policy; at 5% it would have found nothing in three years of federal data, which is
not a measurement but a way of never having to say anything.</p>
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
            f"{'' if row.get('unit_id') else ' <span class=\"tag\">no unit id published</span>'}"
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


def home_page(report: dict[str, Any], *, has_national: bool = False) -> Page:
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
        f'{html.escape(str(s.get("label", "")))}</a> '
        f'({int(s.get("graded", 0))}, {html.escape(_pct(s.get("mean_score")))})</li>'
        for s in sorted(by_state, key=lambda s: str(s.get("label", "")))
    )
    artifacts = "".join(
        f"<li><a href=\"{html.escape(_institution_path(f) or '')}/\">"
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
        coverage = (
            f'<p class="caveat"><strong>What this run covers.</strong> '
            f"{html.escape(scope.sentence)} {html.escape(scope.note)} A project about undisclosed "
            f"information should not be coy about the limits of its own sample.{national_pointer}"
            "</p>"
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
        title="disclosed: what US colleges do not tell you",
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
@media (prefers-color-scheme: dark) {
  body { background: #131313; color: #e9e9e9; }
  a { color: #79b8ff; }
  .lede { color: #cfcfcf; }
  th, td, section[id] { border-color: #333; }
  .skip { background: #131313; color: #79b8ff; }
  :focus-visible { outline-color: #79b8ff; }
  caption { color: #bbb; }
  .why, .bounds, .caveat, footer { color: #bbb; }
  .caveat { border-color: #333; }
  .tag-reported { color: #6fbf73; } .tag-implausible { color: #ff8a80; }
  .tag-missing { color: #ffab7a; } .tag-suppressed { color: #bbb; }
  .tag-not-applicable { color: #bbb; }
}
"""


def _shell(page: Page, *, canonical: str, generated: str) -> str:
    root = "../" * page.path.count("/") + ("../" if page.path else "")
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
<meta property="og:url" content="{html.escape(canonical)}">
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
</footer>
</body>
</html>
"""


def build(
    report: dict[str, Any],
    out_dir: Path,
    *,
    origin: str = DEFAULT_ORIGIN,
    generated: str,
    national: dict[str, Any] | None = None,
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

    pages: list[Page] = [home_page(report, has_national=national is not None), methodology_page()]
    if national is not None:
        pages.append(national_page(national))

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
        pages.append(institution_page(row, findings_by_id.get(unit_id, []), path=path))

    for page in pages:
        target = out_dir / page.path if page.path else out_dir
        target.mkdir(parents=True, exist_ok=True)
        canonical = f"{origin}/{page.path + '/' if page.path else ''}"
        (target / "index.html").write_text(
            _shell(page, canonical=canonical, generated=generated), encoding="utf-8"
        )

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
