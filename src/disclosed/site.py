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
from .messages import SOURCE_LOCALE, Catalog, load
from .peers import MIN_PEERS
from .scope import Scope, scope_from_payload

__all__ = ["Page", "build", "slug"]

#: The catalog every page function falls back to, so that calling one of them without saying
#: which language you want renders the language this project's prose is written and reviewed in.
#: Read once, at import, from a file inside the package: no clock, no network, no locale sniffed
#: from the environment, the same discipline as ``generated`` being passed in rather than read.
ENGLISH: Final[Catalog] = load(SOURCE_LOCALE)

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


#: Every letter a grade can be, read off the bands that decide them rather than typed here. The
#: catalog carries one sentence per letter, and a band added without its sentence fails the
#: catalog-coverage test rather than rendering a page with a blank where the summary goes.
_LETTERS: Final[frozenset[str]] = frozenset(letter for _, letter in BANDS) | {BELOW_EVERY_BAND}


def _classification_copy(disclosure: Disclosure, catalog: Catalog) -> tuple[str, str]:
    """What one classification is called on a page, and what it means to a reader.

    **This function is the only place the five classification tokens are ever turned into words,
    and it is a rendering step.** ``Disclosure.MISSING`` travels through the grader, the report,
    the CSV export and the Table Schema as ``missing``; it becomes "Not reported" here, on the way
    into one page, and it becomes something else here in another language. Nothing downstream of
    this function is data.

    That boundary is the product. The five states are five different facts -- a value nobody
    published, a value withheld to protect a small cohort, a question that does not apply, a
    published number that cannot be a measurement, and a real one -- and a reader who downloads
    the CSV has to be able to join on them. :mod:`disclosed.dataset` therefore does not import
    this module at all, and ``tests/test_i18n.py`` fails if it starts to.

    The copy is written for a person who has just been told their college scored badly and wants
    to know why, which is why the meaning is a sentence rather than a definition.
    """
    return (
        catalog.text(f"classification.{disclosure.value}.label"),
        catalog.text(f"classification.{disclosure.value}.meaning"),
    )


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


def _pct(value: float | None, catalog: Catalog = ENGLISH) -> str:
    """Format a score, or say plainly that there is not one.

    An ungradeable institution has no score. It must never be rendered as 0%, which is the exact
    confusion this whole project exists to prevent, and rendering it as an em dash would be almost
    as bad because a reader would read the dash as "zero" anyway.

    The percentage itself is still formatted with Python's ``%`` convention, which is an English
    one. ``docs/I18N.md`` records that as unfinished rather than solved.
    """
    return catalog.text("score.not_gradeable") if value is None else f"{value:.0%}"


def _name_of(row: dict[str, Any], catalog: Catalog = ENGLISH) -> str:
    """Display name for an institution the source may not have named.

    Never ``str(row["name"])``: that prints the word "None" as a name. An absent name is stated as
    absent, and the unit id is offered instead so the row is still traceable to a record.
    """
    name = row.get("name")
    if isinstance(name, str) and name.strip():
        return name
    unit_id = row.get("unit_id")
    if isinstance(unit_id, str) and unit_id.strip():
        return catalog.text("institution.unnamed_with_unit_id", unit_id=unit_id)
    return catalog.text("institution.unnamed_without_unit_id")


def _grade_badge(letter: str | None, catalog: Catalog = ENGLISH) -> str:
    """A grade, with its meaning available to a reader who cannot see the badge.

    The ungradeable badge used to carry its explanation in a ``title`` attribute, which is not
    reliably announced by screen readers and is invisible to anyone navigating by keyboard. A
    person using a screen reader would have heard "n a" and nothing else, which is the audible
    version of rendering an absence as a bare number: technically present, unreadable as meaning.
    """
    if letter is None:
        return (
            f'<span class="grade grade-none">{catalog.text("grade.none.badge")}'
            f'<span class="visually-hidden">{catalog.text("grade.none.meaning")}</span></span>'
        )
    return (
        f'<span class="grade grade-{letter.lower()}">{html.escape(letter)}'
        f'<span class="visually-hidden">{catalog.text("grade.letter.suffix")}</span></span>'
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
    catalog: Catalog = ENGLISH,
) -> Page:
    """One institution: its grade, every field's disclosure state, and any implausible values.

    With ``ask_endpoint`` the page also carries the opt-in question form and the one inline
    script behind it (see :func:`_ask_widget`); without it the page is exactly what it was.
    """
    name = _name_of(row, catalog)
    letter = row.get("letter")
    score = row.get("score")
    state = row.get("state")
    summary = catalog.text(f"letter.{letter}.summary" if letter in _LETTERS else "letter.none")

    rows = []
    for label in sorted(row.get("fields", {})):
        raw_state = row["fields"][label]
        try:
            disclosure = Disclosure(raw_state)
        except ValueError:
            # A report written by a newer version than this renderer. Say so rather than guessing.
            unrecognized = catalog.text(
                "institution.unrecognized_classification",
                classification=html.escape(str(raw_state)),
            )
            rows.append(
                f'<tr><th scope="row">{_rationale_link(label, label, depth=2)}</th>'
                f'<td colspan="2">{unrecognized}</td></tr>'
            )
            continue
        title, meaning = _classification_copy(disclosure, catalog)
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
            if isinstance(peers, dict):
                unknown = catalog.text("institution.finding.unknown_group")
                said = catalog.text(
                    "institution.finding.peer_check",
                    group=html.escape(str(peers.get("group", unknown))),
                    verdict=html.escape(str(peers.get("verdict", ""))),
                )
            else:
                said = catalog.text("institution.finding.no_peer_group")
            verdict = f'<p class="peers">{said}</p>'
            label = str(finding.get("field", ""))
            published = catalog.text(
                "institution.finding.published_as",
                field=_rationale_link(label, label, depth=2),
                value=html.escape(json.dumps(finding.get("value"))),
            )
            items.append(
                f"<li>{published}{verdict}"
                f'<p class="why">{html.escape(str(finding.get("rationale", "")))}</p></li>'
            )
        findings_html = (
            f"<h2>{catalog.text('institution.findings.heading')}</h2>"
            f"<p>{catalog.text('institution.findings.intro')}</p>"
            f'<ul class="findings">{"".join(items)}</ul>'
        )

    state_link = (
        f'<a href="../../state/{html.escape(slug(state))}/">{html.escape(state)}</a>'
        if isinstance(state, str) and slug(state)
        else catalog.text("institution.state_not_published")
    )
    unit_id = (
        html.escape(str(row.get("unit_id")))
        if row.get("unit_id")
        else catalog.text("institution.unit_id_not_published")
    )
    body = f"""
<nav aria-label="Breadcrumb"><a href="../../">{catalog.text("nav.all_institutions")}</a> \
/ {state_link}</nav>
<h1>{html.escape(name)} {_grade_badge(letter, catalog)}</h1>
<p class="lede">{catalog.text("institution.lede", summary=html.escape(summary))}</p>
<dl class="facts">
  <dt>{catalog.text("institution.facts.score")}</dt><dd>{html.escape(_pct(score, catalog))}</dd>
  <dt>{catalog.text("institution.facts.state")}</dt><dd>{state_link}</dd>
  <dt>{catalog.text("institution.facts.unit_id")}</dt>
  <dd>{unit_id}</dd>
</dl>
<h2>{catalog.text("institution.disclosed.heading")}</h2>
<table>
<caption>{catalog.text("institution.table.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("institution.table.field")}</th>\
<th scope="col">{catalog.text("institution.table.status")}</th>
<th scope="col">{catalog.text("institution.table.meaning")}</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
{findings_html}
<p class="caveat">{catalog.text("institution.caveat", methodology="../../methodology/")}</p>
{_ask_widget(str(row.get("unit_id")), ask_endpoint, catalog) if ask_endpoint else ""}
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
    qualified = (
        catalog.text("institution.qualified_name", name=name, state=state) if state else name
    )
    return Page(
        path=path,
        title=catalog.text("institution.title", name=qualified),
        description=catalog.text(
            "institution.description", name=qualified, score=_pct(score, catalog)
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


def _ask_widget(unit_id: str, endpoint: str, catalog: Catalog = ENGLISH) -> str:
    """The opt-in question form for one institution, and the script behind it.

    Rendered only when the site is built with ``--ask-endpoint``. The section says what it is
    before the reader types anything: optional, nothing sent until Ask is pressed, answers
    AI-generated and unofficial, about disclosure and never about quality.

    The form's own words come from the catalog. The sentences inside :data:`_ASK_SCRIPT` do not,
    and neither does anything ``disclosed.ask`` replies with: the service answers in English
    because its prompts, its verifier and the federal definitions it quotes verbatim are English.
    Wiring the script's strings to the catalog would make the frame of an English answer look
    translated, which is worse than leaving both in one language. ``docs/I18N.md`` records it.
    """
    return f"""
<section class="ask" aria-labelledby="ask-heading">
<h2 id="ask-heading">{catalog.text("ask.heading")}</h2>
<p>{catalog.text("ask.notice")}</p>
<form class="ask-form" data-endpoint="{html.escape(endpoint)}"
      data-unit-id="{html.escape(unit_id)}">
<label for="ask-question">{catalog.text("ask.label")}</label>
<input id="ask-question" name="question" type="text" maxlength="600" required
       placeholder="{catalog.text("ask.placeholder")}">
<button type="submit">{catalog.text("ask.submit")}</button>
</form>
<div id="ask-answer" class="ask-answer" aria-live="polite"></div>
</section>
<script>{_ASK_SCRIPT}</script>
"""


def state_page(
    summary: dict[str, Any], rows: list[dict[str, Any]], *, catalog: Catalog = ENGLISH
) -> Page:
    """One state: how its institutions disclose, and which fields go unreported most often."""
    code = str(summary.get("label", ""))
    graded = int(summary.get("graded", 0))
    ungradeable = int(summary.get("ungradeable", 0))
    mean = summary.get("mean_score")

    listed = []
    for row in sorted(rows, key=lambda r: (_name_of(r, catalog).casefold(), str(r.get("unit_id")))):
        path = _institution_path(row)
        name = html.escape(_name_of(row, catalog))
        linked = f'<a href="../../{path}/">{name}</a>' if path else name
        listed.append(
            f'<tr><th scope="row">{linked}</th>'
            f"<td>{_grade_badge(row.get('letter'), catalog)}</td>"
            f"<td>{html.escape(_pct(row.get('score'), catalog))}</td></tr>"
        )

    worst = "".join(
        "<li>{}</li>".format(
            catalog.count(
                "state.worst.item",
                int(count),
                field=_rationale_link(str(label), str(label), depth=2),
            )
        )
        for label, count in summary.get("worst_fields", [])
    )
    ungradeable_note = (
        f"<p>{catalog.count('state.ungradeable_note', ungradeable)}</p>" if ungradeable else ""
    )
    lede = catalog.count("state.lede", graded, mean=html.escape(_pct(mean, catalog)))
    body = f"""
<nav aria-label="Breadcrumb"><a href="../../">{catalog.text("nav.all_institutions")}</a></nav>
<h1>{catalog.text("state.heading", state=html.escape(code))}</h1>
<p class="lede">{lede}</p>
{ungradeable_note}
<h2>{catalog.text("state.worst.heading", state=html.escape(code))}</h2>
<ul class="worst">{worst}</ul>
<h2>{catalog.text("state.institutions.heading")}</h2>
<table>
<caption>{catalog.text("state.table.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("state.table.institution")}</th>\
<th scope="col">{catalog.text("state.table.grade")}</th>
<th scope="col">{catalog.text("state.table.disclosure")}</th></tr></thead>
<tbody>{"".join(listed)}</tbody>
</table>
<p class="caveat">{catalog.text("state.caveat", methodology="../../methodology/")}</p>
"""
    return Page(
        path=f"state/{slug(code)}",
        title=catalog.text("state.title", state=code),
        description=catalog.count(
            "state.description", graded, state=code, mean=_pct(mean, catalog)
        ),
        body=body,
    )


def methodology_page(*, catalog: Catalog = ENGLISH) -> Page:
    """Every rule, every bound, and the reasoning behind each, at a stable anchor.

    This is the page every finding links to. It exists so that a graded institution arguing with a
    grade is arguing with a stated rule rather than guessing at one, which is the difference
    between a scorecard and an accusation.

    Each field's own label and rationale come from :mod:`disclosed.fields`, not from the catalog.
    They are the wording the grader applies and the wording the CSV's Table Schema publishes, and
    a rationale that read one way on the page and another in the schema would be two rules.
    """

    def render(field: Field) -> str:
        if field.text_is_a_value:
            # A URL column has no credible range to state; what it has is a rule about who the
            # field applies to at all, and that is what a reader needs in its place.
            terms = catalog.text("methodology.field.address_only")
            if field.applies_when is not None:
                terms += catalog.text("methodology.field.applies_when")
        else:
            zero = catalog.text(
                "methodology.field.zero_credible"
                if field.zero_is_credible
                else "methodology.field.zero_artifact"
            )
            terms = catalog.text(
                "methodology.field.credible_range",
                minimum=_bound(field.credible_min, upper=False, catalog=catalog),
                maximum=_bound(field.credible_max, upper=True, catalog=catalog),
                zero=zero,
            )
        weight = catalog.text("methodology.field.weight", weight=f"{field.weight:g}")
        return (
            f'<section id="{field.anchor}">'
            f"<h3>{html.escape(field.label)}</h3>"
            f"<p><code>{html.escape(field.key)}</code></p>"
            f"<p>{html.escape(field.rationale)}</p>"
            f'<p class="bounds">{terms} {weight}</p>'
            f"</section>"
        )

    # Printed from the constants that decide these things, never typed beside them. A grade band
    # and a drift threshold are the two numbers on this page that a graded institution is most
    # likely to check a specific decision against, and a page that states one figure while the
    # grader applies another is the failure this whole page exists to make impossible.
    first_threshold, first_letter = BANDS[0]
    bands = " ".join(
        [
            catalog.text(
                "methodology.band.top", letter=first_letter, threshold=f"{first_threshold:.0%}"
            )
        ]
        + [
            catalog.text("methodology.band.next", letter=letter, threshold=f"{threshold:.0%}")
            for threshold, letter in BANDS[1:]
        ]
        + [catalog.text("methodology.band.bottom", letter=BELOW_EVERY_BAND)]
    )
    systemic = f"{SYSTEMIC_THRESHOLD * 100:g}"

    # Every field this project knows about is documented here, not only the ones in the report
    # being rendered. Findings link to these anchors by label, and a link into a rationale that
    # is not on the page is worse than no link, because it looks answered.
    scorecard_sections = "".join(render(f) for f in FIELDS)
    ipeds_sections = "".join(render(f) for f in IPEDS_FIELDS)
    classification_rows = []
    for d in Disclosure:
        label, meaning = _classification_copy(d, catalog)
        answer = catalog.text(
            "methodology.counts_against.yes"
            if d.counts_against_publisher
            else "methodology.counts_against.no"
        )
        classification_rows.append(
            f'<tr><th scope="row"><span class="tag tag-{d.value.replace("_", "-")}">'
            f"{html.escape(label)}</span></th>"
            f"<td>{html.escape(meaning)}</td>"
            f"<td>{answer}</td></tr>"
        )
    classifications = "".join(classification_rows)
    body = f"""
<nav aria-label="Breadcrumb"><a href="../">{catalog.text("nav.all_institutions")}</a></nav>
<h1>{catalog.text("methodology.heading")}</h1>
<p class="lede">{catalog.text("methodology.lede")}</p>

<h2>{catalog.text("methodology.measured.heading")}</h2>
<p>{catalog.text("methodology.measured.body")}</p>

<h2>{catalog.text("methodology.absent.heading")}</h2>
<p>{catalog.text("methodology.absent.body")}</p>
<table>
<caption>{catalog.text("methodology.absent.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("methodology.absent.column.classification")}</th>\
<th scope="col">{catalog.text("methodology.absent.column.meaning")}</th>
<th scope="col">{catalog.text("methodology.absent.column.counts")}</th></tr></thead>
<tbody>{classifications}</tbody>
</table>
<p>{catalog.text("methodology.absent.suppression")}</p>

<h2>{catalog.text("methodology.published_failure.heading")}</h2>
<p>{catalog.text("methodology.published_failure.body")}</p>

<h2>{catalog.text("methodology.peers.heading")}</h2>
<p>{catalog.text("methodology.peers.rule")}</p>
<p>{catalog.text("methodology.peers.against_itself")}</p>
<p>{catalog.text("methodology.peers.both_counts", min_peers=MIN_PEERS)}</p>

<h2>{catalog.text("methodology.bands.heading")}</h2>
<p>{catalog.text("methodology.bands.body", bands=bands)}</p>

<h2>{catalog.text("methodology.drift.heading")}</h2>
<p>{catalog.text("methodology.drift.why_two_runs")}</p>
<p>{catalog.text("methodology.drift.rate_not_count")}</p>
<p>{catalog.text("methodology.drift.threshold", systemic=systemic)}</p>
<p>{catalog.text("methodology.drift.both_directions")}</p>

<h2>{catalog.text("methodology.fields.scorecard.heading")}</h2>
{scorecard_sections}

<h2>{catalog.text("methodology.fields.ipeds.heading")}</h2>
<p>{catalog.text("methodology.fields.ipeds.three_absences")}</p>
<p>{catalog.text("methodology.fields.ipeds.athletics")}</p>
<p>{catalog.text("methodology.fields.ipeds.veterans")}</p>
{ipeds_sections}

<h2>{catalog.text("methodology.wrong.heading")}</h2>
<p>{catalog.text("methodology.wrong.body")}</p>
"""
    return Page(
        path="methodology",
        title=catalog.text("methodology.title"),
        description=catalog.text("methodology.description"),
        body=body,
    )


def _bound(value: float | None, *, upper: bool, catalog: Catalog = ENGLISH) -> str:
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
        return catalog.text("bound.none.upper" if upper else "bound.none.lower")
    return html.escape(f"{value:,.4f}".rstrip("0").rstrip("."))


def _reported_share(field: dict[str, Any], catalog: Catalog) -> str:
    """The published share for one row of the national and census tables."""
    return html.escape(
        _share(int(field.get("reported", 0)), int(field.get("applicable", 0)), catalog)
    )


def _share(numerator: int, denominator: int, catalog: Catalog = ENGLISH) -> str:
    """A percentage, or words when there is nothing to divide by.

    ``0%`` is a real answer to "what share reported this" and must not also be the answer to
    "there was nobody to ask". A denominator of zero returns the sentence rather than the number.
    """
    if denominator <= 0:
        return catalog.text("share.no_applicable_institutions")
    return f"{numerator / denominator:.0%}"


def national_page(payload: dict[str, Any], *, catalog: Catalog = ENGLISH) -> Page:
    """The one page whose percentages describe the country rather than a slice of it.

    Kept as its own page rather than merged into the home page, because the two rest on different
    corpora and a reader who lands halfway down a page must never be able to carry a national
    figure back up to a sample one or the other way round. The scope sentence is printed from the
    artifact, not from this template, so a page rendered from a different run says what that run
    covered rather than what this paragraph was written believing.

    The scope sentence and the statute names are the artifact's own words and stay in whatever
    language the run recorded them in. Translating a citation of a statute would be inventing one.
    """
    scope = scope_from_payload(payload)
    fields: list[dict[str, Any]] = list(payload.get("fields", []))
    gaps: dict[str, Any] = payload.get("gaps", {}) or {}

    rows = "".join(
        f'<tr><th scope="row">'
        f"{_rationale_link(str(f.get('label', '')), str(f.get('label', '')), depth=1)}</th>"
        f"<td>{int(f.get('applicable', 0)):,}</td>"
        f"<td>{int(f.get('missing', 0)):,}</td>"
        f"<td>{_reported_share(f, catalog)}</td>"
        f"<td>{html.escape(str(f.get('statute')) or catalog.text('national.no_statute'))}</td></tr>"
        for f in fields
    )

    unnamed = catalog.text("institution.unnamed")
    no_unit_id = catalog.text("national.gap.no_unit_id")
    sections = []
    for field in fields:
        label = str(field.get("label", ""))
        listed = gaps.get(label)
        if not isinstance(listed, list) or not listed:
            continue
        items = "".join(
            f"<li>{html.escape(str(row.get('name') or unnamed))}"
            f"{html.escape(' (' + str(row.get('state')) + ')') if row.get('state') else ''}"
            f"{'' if row.get('unit_id') else f' <span class="tag">{no_unit_id}</span>'}"
            "</li>"
            for row in listed
            if isinstance(row, dict)
        )
        heading = catalog.text(
            "national.gap.heading",
            field=_rationale_link(label, label, depth=1),
            count=f"{len(listed):,}",
            applicable=f"{int(field.get('applicable', 0)):,}",
        )
        body_text = catalog.text(
            "national.gap.body", statute=html.escape(str(field.get("statute", "")))
        )
        sections.append(f'<h3>{heading}</h3><p>{body_text}</p><ul class="gaps">{items}</ul>')

    # Read off the table rather than written into the sentence. The paragraph below explains the
    # applicable column by naming the narrowest and widest disclosure in it, and those two numbers
    # move every collection year; hardcoded, the prose would go on citing 2023's denominators
    # underneath a table showing another year's, which is the failure this page exists to describe.
    reach = sorted(int(f.get("applicable", 0)) for f in fields)
    spread = (
        catalog.text(
            "national.middle_column.with_reach",
            narrowest=f"{reach[0]:,}",
            widest=f"{reach[-1]:,}",
        )
        if len(reach) >= 2 and reach[0] != reach[-1]
        else catalog.text("national.middle_column.without_reach")
    )

    lede = html.escape(scope.sentence) if scope else catalog.text("scope.not_stated")
    ungradeable = int(payload.get("ungradeable", 0))
    ungradeable_note = (
        f"<p>{catalog.count('national.ungradeable_note', ungradeable, rows=f'{ungradeable:,}')}</p>"
        if ungradeable
        else ""
    )
    named = "".join(sections) or f"<p>{catalog.text('national.no_named_findings')}</p>"
    body = f"""
<nav aria-label="Breadcrumb"><a href="../">{catalog.text("nav.all_institutions")}</a></nav>
<h1>{catalog.text("national.heading")}</h1>
<p class="lede">{lede}</p>
<p>{catalog.text("national.why_this_page_differs")}</p>

<h2>{catalog.text("national.discloses.heading")}</h2>
<table>
<caption>{catalog.text("national.table.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("national.table.disclosure")}</th>\
<th scope="col">{catalog.text("national.table.reaches")}</th>
<th scope="col">{catalog.text("national.table.record_carries_none")}</th>\
<th scope="col">{catalog.text("national.table.published")}</th>
<th scope="col">{catalog.text("national.table.requirement")}</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p>{spread}</p>
{ungradeable_note}

<h2>{catalog.text("national.named_findings.heading")}</h2>
{named}

<p class="caveat">{catalog.text("national.caveat", methodology="../methodology/")}</p>
"""
    return Page(
        path="national",
        title=catalog.text("national.title"),
        description=catalog.text("national.description"),
        body=body,
    )


def _share_of(count: int, total: int, catalog: Catalog = ENGLISH) -> str:
    return f"{count / total:.1%}" if total else catalog.text("share.no_institutions")


def scorecard_census_page(payload: dict[str, Any], *, catalog: Catalog = ENGLISH) -> Page:
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
        f"<td>{_reported_share(f, catalog)}</td></tr>"
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
        f"<td>{html.escape(_share_of(sample_sectors.get(label, 0), sample_total, catalog))}</td>"
        f"<td>{census_sectors.get(label, 0):,}</td>"
        f"<td>{html.escape(_share_of(census_sectors.get(label, 0), comp_total, catalog))}</td></tr>"
        for label in sector_labels
    )

    ca_sample = int(sample_comp.get("states", {}).get("CA", 0))
    ca_census = int(comp.get("states", {}).get("CA", 0))
    composition = catalog.text(
        "census.composition.body",
        ca_sample=f"{ca_sample:,}",
        sample_total=f"{sample_total:,}",
        ca_sample_share=_share_of(ca_sample, sample_total, catalog),
        sample_states=len(sample_comp.get("states", {})),
        census_total=f"{comp_total:,}",
        census_states=len(comp.get("states", {})),
        ca_census_share=_share_of(ca_census, comp_total, catalog),
    )

    admission = next((f for f in fields if f.get("label") == "Admission rate"), None)
    headline = ""
    if admission is not None and admission.get("applicable"):
        missing = int(admission["missing"])
        applicable = int(admission["applicable"])
        headline = "<p>{}</p>".format(
            catalog.text(
                "census.headline",
                missing=f"{missing:,}",
                applicable=f"{applicable:,}",
                share=f"{missing / applicable:.1%}",
            )
        )

    lede = html.escape(scope.sentence) if scope else catalog.text("scope.not_stated")
    body = f"""
<nav aria-label="Breadcrumb"><a href="../">{catalog.text("nav.all_institutions")}</a></nav>
<h1>{catalog.text("census.heading")}</h1>
<p class="lede">{lede}</p>
<p>{catalog.text("census.why_this_page_differs")}</p>

{headline}

<h2>{catalog.text("census.composition.heading")}</h2>
<p>{composition}</p>
<table>
<caption>{catalog.text("census.sector_table.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("census.sector_table.sector")}</th>\
<th scope="col">{catalog.text("census.sector_table.sample")}</th>\
<th scope="col">{catalog.text("census.sector_table.sample_share")}</th>
<th scope="col">{catalog.text("census.sector_table.census")}</th>\
<th scope="col">{catalog.text("census.sector_table.census_share")}</th></tr></thead>
<tbody>{sector_rows}</tbody>
</table>

<h2>{catalog.text("census.discloses.heading")}</h2>
<table>
<caption>{catalog.text("census.table.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("national.table.disclosure")}</th>\
<th scope="col">{catalog.text("national.table.reaches")}</th>
<th scope="col">{catalog.text("national.table.record_carries_none")}</th>\
<th scope="col">{catalog.text("national.table.published")}</th></tr></thead>
<tbody>{rows}</tbody>
</table>

<p class="caveat">{catalog.text("census.caveat", methodology="../methodology/")}</p>
"""
    return Page(
        path="census",
        title=catalog.text("census.title"),
        description=catalog.text("census.description"),
        body=body,
    )


def home_page(
    report: dict[str, Any],
    *,
    has_national: bool = False,
    has_scorecard_census: bool = False,
    catalog: Catalog = ENGLISH,
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
        f"({int(s.get('graded', 0))}, {html.escape(_pct(s.get('mean_score'), catalog))})</li>"
        for s in sorted(by_state, key=lambda s: str(s.get("label", "")))
    )
    artifacts = "".join(
        "<li>{}</li>".format(
            catalog.text(
                "home.artifact",
                institution=(
                    f'<a href="{html.escape(_institution_path(f) or "")}/">'
                    f"{html.escape(_name_of(f, catalog))}</a>"
                    if _institution_path(f)
                    else html.escape(_name_of(f, catalog))
                ),
                field=_rationale_link(str(f.get("field", "")), str(f.get("field", "")), depth=1),
                value=html.escape(json.dumps(f.get("value"))),
            )
        )
        for f in implausible
    )
    ungradeable_note = (
        f"<p>{catalog.count('home.ungradeable_note', ungradeable)}</p>" if ungradeable else ""
    )
    # Printed from the scope the run recorded, never from a constant in this template. A caveat
    # written into a template stays true only until somebody renders a different report through
    # it, and the sentence this one carries is the one thing on the page a reader must be able to
    # trust without checking anything else.
    scope: Scope | None = scope_from_payload(report)
    if scope is None:
        coverage = (
            f'<p class="caveat"><strong>{catalog.text("home.coverage.label")}</strong> '
            f"{catalog.text('home.coverage.no_scope')}</p>"
        )
    else:
        national_pointer = (
            " " + catalog.text("home.coverage.national_pointer", national="national/")
            if has_national and not scope.is_national
            else ""
        )
        census_pointer = (
            " " + catalog.text("home.coverage.census_pointer", census="census/")
            if has_scorecard_census and not scope.is_national
            else ""
        )
        coverage = (
            f'<p class="caveat"><strong>{catalog.text("home.coverage.label")}</strong> '
            f"{html.escape(scope.sentence)} {html.escape(scope.note)} "
            f"{catalog.text('home.coverage.not_coy')}"
            f"{national_pointer}{census_pointer}</p>"
        )
    body = f"""
<h1>disclosed</h1>
<p class="lede">{catalog.text("home.lede")}</p>
<p>{catalog.text("home.what_this_grades")}</p>
<p>{catalog.text("home.why_it_matters")}</p>

<h2>{catalog.text("home.found.heading")}</h2>
<dl class="facts">
  <dt>{catalog.text("home.found.graded")}</dt><dd>{total}</dd>
  <dt>{catalog.text("home.found.mean")}</dt><dd>{html.escape(_pct(mean, catalog))}</dd>
  <dt>{catalog.text("home.found.not_gradeable")}</dt><dd>{ungradeable}</dd>
  <dt>{catalog.text("home.found.not_measurements")}</dt><dd>{len(implausible)}</dd>
</dl>
{ungradeable_note}

<h2>{catalog.text("home.worst.heading")}</h2>
<table>
<caption>{catalog.text("home.worst.caption")}</caption>
<thead><tr><th scope="col">{catalog.text("home.worst.field")}</th>\
<th scope="col">{catalog.text("home.worst.not_reporting")}</th>
<th scope="col">{catalog.text("home.worst.share")}</th></tr></thead>
<tbody>{worst}</tbody>
</table>

<h2>{catalog.text("home.zeros.heading")}</h2>
<p>{catalog.text("home.zeros.body")}</p>
<ul class="findings">{artifacts}</ul>

<h2>{catalog.text("home.by_state.heading")}</h2>
<ul class="states">{states}</ul>

<h2>{catalog.text("home.how.heading")}</h2>
<p>{catalog.text("home.how.body", methodology="methodology/")}</p>
{coverage}
"""
    return Page(
        path="",
        # Not "disclosed: what US colleges do not tell you". _shell appends
        # " | disclosed" to every title, so that one rendered as "disclosed:
        # what US colleges do not tell you | disclosed" -- the site's name
        # twice in fifty-four characters, on the one page most likely to be
        # seen in a result list.
        title=catalog.text("home.title"),
        description=catalog.text("home.description"),
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


def _shell(
    page: Page, *, canonical: str, origin: str, generated: str, catalog: Catalog = ENGLISH
) -> str:
    """One page, including what a search result and a link preview will say about it.

    The share card repeats this page's own title and description rather than a second set written
    for sharing, which would be an unreviewed description of the project published where nobody
    rereads it. The image is the one part it does not take from the page, because there is only
    one: ``og-card.png``, written into the site root by :func:`build` and named here at an
    absolute address off ``origin``, which is the only kind of address a crawler on another host
    can resolve.

    ``lang`` and ``og:locale`` are read off the catalog rather than written here. A page whose
    prose came from the Spanish catalog and whose ``<html lang>`` still said ``en`` would be
    telling a screen reader to pronounce Spanish with English rules, and telling a crawler the
    page is something it is not -- an absence of translation rendered as a claim of one.
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
    alt = html.escape(catalog.text("share.card.alt"))
    footer_generated = catalog.text(
        "shell.footer.generated",
        generated=html.escape(generated),
        methodology=f"{root}methodology/",
    )
    return f"""<!DOCTYPE html>
<html lang="{catalog.html_lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{catalog.text("shell.title", title=html.escape(page.title))}</title>
<meta name="description" content="{html.escape(page.description)}">
<link rel="canonical" href="{html.escape(canonical)}">
<meta property="og:title" content="{html.escape(page.title)}">
<meta property="og:description" content="{html.escape(page.description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="disclosed">
<meta property="og:locale" content="{catalog.og_locale}">
<meta property="og:url" content="{html.escape(canonical)}">
<meta property="og:image" content="{card}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="{OG_CARD_WIDTH}">
<meta property="og:image:height" content="{OG_CARD_HEIGHT}">
<meta property="og:image:alt" content="{alt}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html.escape(page.title)}">
<meta name="twitter:description" content="{html.escape(page.description)}">
<meta name="twitter:image" content="{card}">
<meta name="twitter:image:alt" content="{alt}">
<style>{_STYLE}</style>
</head>
<body>
<a class="skip" href="#content">{catalog.text("shell.skip_link")}</a>
<main id="content">
{page.body}
</main>
<footer>
<p>{footer_generated}</p>
<p>{catalog.text("shell.footer.source", source=html.escape(SOURCE_URL))}</p>
</footer>
</body>
</html>
"""


def _corpus_pages(
    report: dict[str, Any],
    *,
    national: dict[str, Any] | None,
    scorecard_census: dict[str, Any] | None,
    catalog: Catalog,
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
            catalog=catalog,
        ),
        methodology_page(catalog=catalog),
    ]
    if scorecard_census is not None:
        pages.append(scorecard_census_page(scorecard_census, catalog=catalog))
    if national is not None:
        pages.append(national_page(national, catalog=catalog))
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
    locale: str = SOURCE_LOCALE,
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
        locale: Which message catalog the pages are rendered from. Only catalogs that exist and
            are complete can be named; :func:`disclosed.messages.load` refuses the rest rather
            than filling the gaps with English, so a locale either renders a whole site or none
            of one. Today ``en`` is the only catalog in the repository.

    Returns:
        Every page written, in the order written. Callers use it to assert page counts without
        walking the filesystem.

    Raises:
        CatalogError: If ``locale`` names no catalog, or one that is incomplete.
    """
    catalog = load(locale)
    grades: list[dict[str, Any]] = list(report.get("grades", []))
    findings_by_id: dict[str, list[dict[str, Any]]] = {}
    for finding in report.get("implausible", []):
        unit_id = finding.get("unit_id")
        if isinstance(unit_id, str) and unit_id:
            findings_by_id.setdefault(unit_id, []).append(finding)

    pages: list[Page] = _corpus_pages(
        report, national=national, scorecard_census=scorecard_census, catalog=catalog
    )

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
        pages.append(state_page(summary, by_state.get(code, []), catalog=catalog))

    for row in sorted(grades, key=lambda r: str(r.get("unit_id"))):
        path = _institution_path(row)
        if path is None:
            # Counted in the state listings, but given no URL. See _institution_path.
            continue
        unit_id = str(row.get("unit_id"))
        pages.append(
            institution_page(
                row,
                findings_by_id.get(unit_id, []),
                path=path,
                ask_endpoint=ask_endpoint,
                catalog=catalog,
            )
        )

    for page in pages:
        target = out_dir / page.path if page.path else out_dir
        target.mkdir(parents=True, exist_ok=True)
        canonical = f"{origin}/{page.path + '/' if page.path else ''}"
        (target / "index.html").write_text(
            _shell(
                page,
                canonical=canonical,
                origin=origin,
                generated=generated,
                catalog=catalog,
            ),
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
