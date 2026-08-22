# disclosed

**Grades US higher-education institutions on what they disclose, not on how they perform.**

There are many tools that will tell you a college's graduation rate. There is no tool that tells you
how many colleges did not report one, or which fields quietly stopped being published this year.
That is what this grades.

**Status:** Beta, pre-release (`0.1.0.dev0`). The five-way classification, both federal
adapters, and drift measurement are complete and tested behind a 90% branch-coverage gate.
There are deliberately no tagged releases: nothing here is consumed downstream, and [ADR
0001](docs/adr/0001-no-versioned-release.md) records why a release pipeline with nothing to
release would be exactly the kind of gate that never fails.

The distinction matters because the two failures look identical on a page. A college with a 0%
admission rate and a college that never reported an admission rate both render as a blank or a zero
in most tools, and a reader cannot tell them apart. In a 600-institution sample of the College
Scorecard, **387 of the 600, or 64.5%, publish no admission rate at all**, and separately, **one
institution publishes an admission rate of exactly zero** — which is not a school that admitted
nobody, it is a reporting artifact that survived because zero is a legal number.

That sample is not a random one: the API returns institutions grouped by state, and the committed
600 arrived from the front of the alphabet, 51% of them Californian. A full census — every
institution the College Scorecard publishes, walked to exhaustion — puts the same question at
**4,363 of 6,273, or 69.6%, publish no admission rate at all**, five points higher than the
sample's figure rather than lower, which is not the direction a reader who assumed the sample
flattered itself would guess. The
[Scorecard census page](https://chelseakr.github.io/disclosed/census/) states both frames'
composition side by side; the sample figure above is unchanged and describes
the 600 institutions it has always described.

## What it does

Every value from a publisher is classified before anything else touches it:

| Classification | Meaning | Counts against the publisher? |
| --- | --- | --- |
| `REPORTED` | A credible value they actually disclosed | — |
| `IMPLAUSIBLE` | Disclosed, but outside the credible range for that field | **Yes** |
| `SUPPRESSED` | Withheld deliberately, usually to protect a small cohort | No |
| `NOT_APPLICABLE` | The question doesn't apply to this institution | No, and it leaves the denominator |
| `MISSING` | No value and no stated reason | **Yes** |

Suppression is a policy decision made for good reasons and is never held against anyone. Punishing
an institution for protecting a twelve-person cohort would push publishers toward disclosing things
they shouldn't, which is the opposite of the point.

An institution whose every field is suppressed gets **no grade at all**, not a zero. The project
applies its own discipline to itself: absence is reported as absence.

## Disclosure drift

A single snapshot can't distinguish a field that was never collected from one that was collected
until recently and then stopped. Only the comparison between runs can, and the difference matters:
the first is a gap in the data model, the second is a change in what the public is allowed to know.

The unit of interest is the field, not the institution. One college dropping a field is a data-entry
event. Four hundred colleges dropping the same field between two runs is a policy change. Drift is
reported in both directions — fields that *started* being reported are as real a finding as fields
that stopped, and reporting only the losses would make this an argument rather than a measurement.

```sh
disclosed grade --out data/report.json
disclosed snapshot --taken 2026-08-05 --out data/snapshots/scorecard/2026-08-05.json
disclosed drift data/snapshots/ipeds/2021.json data/snapshots/ipeds/2023.json
```

Snapshots are small enough to commit, so the record of what stopped being published lives in git
rather than in a bucket someone has to trust. A scheduled workflow accrues `snapshots/scorecard/`
daily; `snapshots/ipeds/` holds three real collection years, so `drift` has something true to
compare against from day one.

The two live in separate directories and each snapshot records its source, because the two field
sets do not overlap: comparing across them would skip every field and print *"no change in
per-field disclosure"*, which is the most reassuring possible way of saying nothing at all.
`drift` refuses such a pair outright.

### Drift is a change in rate, and it took real history to prove it

Measured on counts, those three years produced three confident systemic findings and **all three
were false.** The directory shrank from 6,289 institutions to 6,163, so 130 fewer published a web
address, and that was reported as a systemic 2.1% collapse. The share publishing one had gone
**up**, from 99.93% to 99.95%. Colleges closed; they did not stop reporting. Meanwhile the one
real movement in the period — the athletics disclosure rising from 57.1% to 59.4% — ranked fourth
and was never flagged, because 52 is a small number next to 130.

Every comparison now divides by the institutions the field applied to in that run, and the
direction word is read from the rate rather than the count. A field can shed reporters while the
share reporting it rises; printing "lost" beside a rise of 1.67 points got the count right and the
finding backwards, which is worse than a wrong number because it comes with a word attached.

The 2-point threshold is a judgement call, and three years of federal data say it is roughly
right: every year-on-year movement sits under one point except the athletics disclosure, at 1.75
in a year and 2.26 across two. At 1% the bar reports ordinary churn as policy. At 5% it finds
nothing in three years, which is not a measurement but a way of never having to say anything.

## Two federal sources, one disagreement

IPEDS and the College Scorecard are published by the same department and keyed on the same unit
id, so the same institution can be checked against both. Across the 600 institutions in the
committed capture they agree on state for every one and disagree about exactly one on sector:

> **Grand Canyon University.** The College Scorecard files it as **private nonprofit**. IPEDS
> files it as **private for-profit**.

Sector decides which rules an institution answers to and which peer group it is compared against
in most analyses of federal education data. The disagreement is reported and deliberately never
resolved: deciding which federal source is correct is not something this project is in a position
to do, and quietly preferring one would throw away the only interesting part of the observation.

IPEDS also carries public disclosures the Scorecard does not. Among institutions that participate
in Title IV and enrol first-time undergraduates, **the federal record carries no net price
calculator for 34 of them**, a calculator that
[20 U.S.C. §1015a(h)(3)](https://www.law.cornell.edu/uscode/text/20/1015a) requires and that
[§1094(a)(17)](https://www.law.cornell.edu/uscode/text/20/1094) requires them to report. Which of
the two is missing is not something a blank cell can tell you, and the finding says so rather than
picking the more dramatic reading.

Getting to 34 rather than 213 is the applicability rule doing its job. **213** of the 6,163
directory rows carry no calculator address; graduate-only institutions, institutions taking no
federal aid, system offices, and closures account for the other **179**. They are outside the
statute and leave the denominator instead of being marked down.

### The rule is the hard part, not the threshold

The Equity in Athletics disclosure went ungraded in an earlier pass. It is blank for **4,469 of
6,163** directory rows, and almost every one of those is a college with no athletics programme, so
grading it against the directory alone would have manufactured four thousand violations. What was
missing was not a better threshold. It was a way to know who the rule applied to.

The IPEDS institutional characteristics file supplies it: each institution's own answer about
whether it belongs to a national athletic association. That moves the denominator from 6,163 to
**1,998**, of which **812 give the federal record no athletics address**.
[20 U.S.C. §1092(g)](https://www.law.cornell.edu/uscode/text/20/1092) requires those institutions
to prepare the report and make it available; it does not require them to post it, so this is
graded as a disclosure gap and stated as weaker than the net price calculator finding, not louder.

The veterans page is still not graded, and the same file would now supply a rule for it. There is
no universal requirement to publish one, and a rule about who a duty reaches is worthless when the
duty does not exist.

```sh
disclosed crosscheck --cache data/HD2023.zip --characteristics data/IC2023.zip \
  --source data/sample.json --out data/crosscheck.json
```

Both IPEDS files are required. If the characteristics file cannot be read the load fails rather
than returning directory-only records, because a field that silently stops being graded looks on
the page exactly like a field everybody suddenly started reporting.

## Use the data

```sh
disclosed site    --report data/report.json --out site --generated 2026-08-05
disclosed dataset --report data/report.json --out data/dataset.csv
# With a running disclosed.ask service (ADR 0006), institution pages gain the opt-in form:
disclosed site    --report data/report.json --out site --generated 2026-08-05 \
                  --ask-endpoint https://example.invalid/ask
```

`data/dataset.csv` ships with a Table Schema at `data/dataset.schema.json`, generated in the same
pass so the two cannot drift. Every graded field is exported as a word (`reported`, `missing`,
`suppressed`, `not_applicable`, `implausible`) rather than as a value, so no cell in a
classification column is ever empty. Exactly one column may be empty, `disclosure_score`, and only
when an institution had nothing to be graded on. A `gradeable` column travels beside it saying so,
because an empty numeric cell is ambiguous on its own and spreadsheets coerce blanks to zero.

## How we grade

Every credible range is a judgement call, so every one carries a written rationale that a graded
institution can argue with. That is the whole contract: **a scorecard that cannot be disputed line
by line is not a scorecard, it is an accusation.** The rationales are written for the reader who
thinks their institution was marked unfairly.

The 2% threshold that separates "systemic" drift from scattered data entry is likewise a judgement
call, stated here so a reader can disagree with it. It is set low because a coordinated
stop-reporting event is newsworthy well before it touches a majority of institutions.

## Sources

| Source | Status | Notes |
| --- | --- | --- |
| College Scorecard | **Live** | Public API. `DEMO_KEY` works for small runs; set `DATA_GOV_API_KEY` for a higher rate limit. |
| IPEDS | **Live** | Public bulk directory file, no key and no quota. Adds required disclosures the Scorecard doesn't carry, and lets the same institution be checked against two federal sources. |
| Credential Registry (CTDL) | **Open, adapter unwritten** | Public and unauthenticated. `GET /ce-registry/search?resource_type=credential` answered 200 with `x-total: 133346` on 2026-08-15 with no key and no headers; `/ce-registry/envelopes` answered 200 with `x-total: 395878` and a full `decoded_resource` per envelope. This row previously said "blocked", and that was our error, not theirs: see below. |

A partial fetch is treated as a failure, not as data. Truncation would understate disclosure across
every institution that never arrived, which looks identical to a real reporting collapse.

### The zero in the Credential Registry row was our own failure mode

For weeks this table recorded the Credential Registry as blocked behind an API key, on the
evidence that its search endpoint "returns `x-total: 0` for every query shape tried". It does
return that, for the shapes that were tried. The registry filters on `resource_type`, and a
request carrying an unrecognized parameter or an unmatched value is answered **HTTP 200 with
`x-total: 0`** rather than with an error: `?type=ceterms:Credential` and
`?resource_type=bogus_value` both return zero, while `?resource_type=credential` returns
133,346 and `?resource_type=organization` returns 34,082. Nothing was ever locked.

A zero that means "your filter matched nothing" was read as a measurement of what is available.
That is the exact confusion this project exists to name, one level up: absence of a value and
absence of a query are not the same absence, and a source that reports both as `0` will be
misread by anyone who does not already know which one they are looking at. It was misread here,
in the repository that grades other people for it, and the misreading stopped work on a third
source for weeks.

`/robots.txt` 404s, so the registry publishes no crawl directives (RFC 9309 §2.2.3 treats a 4xx
as unrestricted). Whether a CTDL adapter can be joined to the two federal corpora is a separate
and still-open question: in the first 200 organizations
(`?resource_type=organization&per_page=100`, pages 1 and 2, fetched the same day) only 8 records
mentioned IPEDS at all, so the join rate has to be measured before anything is built on it.

IPEDS states absence three different ways, all negative integers: `-1` not reported, `-2` not
applicable, `-3` not available. They are not interchangeable and only the first counts against an
institution. They are matched on the raw value rather than the normalized token, because
normalization strips the minus sign and `-2` would otherwise collide with a real measurement of
two.

## What is a sample and what is national

Three corpora, and they are never mixed.

| | Corpus | Coverage | Fields |
| --- | --- | --- | --- |
| **Sample** | College Scorecard | 600 institutions, 13 states, California 51% | earnings, completion, admission, debt, tuition, enrollment |
| **Census** | College Scorecard | every institution the API returns, 6,273 institutions, 59 states and territories, California 11% | earnings, completion, admission, debt, tuition, enrollment |
| **National** | IPEDS directory + characteristics | every institution there is, 6,163 | the six public disclosure addresses |

Sample and census are the same source and the same six fields; the difference between them is
entirely whether the walk stopped early or ran to exhaustion, and `scope.kind` in the payload says
which happened rather than a reader having to guess from the row count. IPEDS is a third, separate
corpus: it publishes a file rather than a paged API, so grading it grades the population by
construction, and only for the six disclosure addresses IPEDS itself carries. None of the three is
substitutable for another, and the site keeps each on its own page for exactly that reason.

**How skewed was the sample, in numbers.** The census answers this directly because it is the
same source counted completely:

| Sector | Sample (600) | Census (6,273) |
| --- | --- | --- |
| Public | 287 (47.8%) | 2,047 (32.6%) |
| Private nonprofit | 194 (32.3%) | 1,901 (30.3%) |
| Private for-profit | 119 (19.8%) | 2,325 (37.1%) |

The sample is not just Californian — it is nearly half public institutions, when public
institutions are under a third of the Scorecard's own population and private for-profits, the
sample's smallest sector at one-fifth, are its largest at over a third. The
[census page](https://chelseakr.github.io/disclosed/census/) carries the full sector and state
tables; `data/scorecard-census.json`'s `composition` and `sample_composition` blocks are the
committed source of both.

Coverage travels *inside* the report rather than in a paragraph on the page. Every payload carries
a `scope` block, the site prints its sentence rather than a constant, and `disclosed national`
**refuses to build** from a run that did not cover the population: there is no correct way to
relabel a sample, so the only safe answer is to fail. `disclosed census-report` makes the same
refusal for the Scorecard census.

```sh
disclosed national --report data/crosscheck.json --out data/national.json
disclosed grade --source data/census/scorecard.json --out /tmp/census-graded.json
disclosed census-report --report /tmp/census-graded.json --source data/census/scorecard.json \
  --out data/scorecard-census.json
disclosed site --report data/report.json --national data/national.json \
  --scorecard-census data/scorecard-census.json --out site --generated 2026-08-05
```

`data/national.json` is just under 100 KB and committed; the 3 MB run it reduces is not, because
it is regenerable in a minute from two public archives that need no key. `data/scorecard-census.json`
is the same shape of artifact for the Scorecard census, reduced from `data/census/scorecard.json` —
the one Scorecard capture that *is* committed, because unlike the IPEDS archives it cannot be
regenerated without a key: it carries the provenance of every page the walk fetched (redacted
request URL, status, byte count, SHA-256, rate-limit headroom), proves its own exhaustion from
those counts, and is refreshed only by the dispatch-only `census` workflow, deliberately, because
a re-census changes the population every Scorecard figure on this page is computed on. Without
`--national` or `--scorecard-census` the site builds without that page and makes no claim on that
page's behalf, which is the right default: a missing corpus should show up as missing figures, not
as sample figures with the qualifier quietly dropped.

A run recorded before `scope` existed says so on the page rather than being assumed complete.

## Accessibility

A page nobody can read has not disclosed anything, so this is the same argument as the rest of the
project rather than a separate one. The bar is **100 on Lighthouse accessibility**, and everything
a static checker can prove runs in `make verify` with no browser: WCAG AA contrast for every
colour pair the stylesheet puts together in both light and dark, one `<main>` and one `<h1>` per
page, a skip link with a target that exists, named navigation landmarks, no skipped heading level,
a caption and row headers on every data table, and no meaning carried by colour alone.

One test asserts that every colour in the stylesheet is covered by a case in the contrast table,
so a new colour fails the build instead of shipping unchecked.

Two fixes worth naming. The ungradeable badge carried its meaning in a `title` attribute, which a
screen reader may not announce and a keyboard user cannot reach: "n/a" and nothing else is the
audible version of printing an absence as a bare number. And every table row now starts with a
`<th scope="row">`, because without one a screen reader reading the third cell of the four
hundredth row announces a classification with nothing attached to say whose it is.

There are no scripts, no external stylesheets, no fonts, no images and no third-party requests,
and adding one is a build failure rather than a decision nobody noticed. One deliberate
exception exists and is off by default: built with `--ask-endpoint`, each institution page
carries the opt-in question form for the AI layer (ADR 0006) and one inline script behind it,
with no `src`, whose only network call sits inside the form's submit handler, so nothing leaves
the page until a reader presses Ask; `tests/test_ask_widget.py` proves both from the built
bytes. The published site is built without it until the service is deployed, which is a
separate decision. That is enforced in
`make verify`, by parsing the built HTML for anything that would make a browser fetch a second
file: once over a fixture holding one page of every kind, and once over the whole published
site, all 617 pages of it, rendered from `data/report.json`, `data/national.json` and
`data/scorecard-census.json`. The second pass exists because the fixture's report carries no
implausible finding, so the markup both the
home page and the institution pages render around a finding was never parsed by anything, and a
tracker added to that branch would have shipped past a suite that said it checked every page.

It used to be attributed to `lighthouse-budget.json`, which budgets every non-document resource
type at zero and enforced none of it. `--budget-path` never makes Lighthouse exit non-zero, the
scoring step reads only the accessibility category, and Lighthouse 12 emits no budget audit at
all: a `lighthouse@12` (12.8.2) run against a budget file with every line set to zero exited 0,
scored accessibility 1, and produced no audit whose key even contains the word "budget". Five of
the six pages that job audits ask only for the accessibility category, which does not collect
the resource summary either. A gate that cannot fail is worse than no gate, because the badge is
the same colour. The budget file stays as the declaration of intent; the enforcement is now
somewhere it can fail.

## Development

```sh
make verify     # lint, typecheck, test (including the accessibility checks)
make grade      # fetch and grade against the live API
make crosscheck # grade the whole IPEDS directory, no key needed
make national   # reduce that to the committed national artifact
```

Python 3.12+, no runtime dependencies. Strict mypy, ruff, and a 90% branch-coverage gate.
`make verify` is the single local gate and the same target CI runs; `CONTRIBUTING.md` has the
setup.

## AI-assisted development

This project is built with an AI coding agent (Claude Code), with the maintainer directing the
work and accountable for all of it. A project that grades others on disclosure should disclose
that. Two things keep it honest: every change has to pass the same gate regardless of who wrote
it (`make verify`: lint, format, strict types, tests including the accessibility suite, plus
the CI security scans), and no finding rests on anyone's fluency, machine or human. The
numbers come from committed data and are reproducible with the commands above; nothing in the
dataset or on the static pages is model-generated.

Runtime AI is a separate, optional layer, and a deliberate change of direction recorded in
[ADR 0006](docs/adr/0006-ai-at-the-edges-the-classified-dataset-is-the-only-evidence.md): a
question-answering service (`disclosed.ask`) that lets a reader ask what an institution does and
does not disclose. The model structures the question and narrates the project's own classified
records; it never sees a reported value, every claim cites a record and is verified before
display, performance judgement is refused and measured at zero tolerance, and the five
classifications are never collapsed. Its output is always labelled AI-generated, unofficial, and
about disclosure rather than quality. The static site and the dataset are unchanged by it.

## Ask about disclosure

`disclosed.ask` answers one kind of question: what an institution did and did not disclose,
and why an absence might be there. It never answers whether a college is good.

```sh
uv sync --extra ask                      # the public anthropic SDK is the only dependency
ANTHROPIC_API_KEY=... disclosed ask "What does this college not report?" --institution 104717
disclosed serve --origin https://chelseakr.github.io   # the development server
disclosed evals --kind oracle            # the evaluation suites, no key needed
```

How it works, in the order a question travels:

1. **The model structures the question** into the project's own vocabulary: one of ten intents
   and zero or more of the twelve graded field labels, which are the JSON schema's enum. A
   measure the project does not grade goes into `unmapped_terms`, never onto the nearest field.
2. **Code decides, without the model,** whether the question can be served. Performance,
   ranking, advice and outcome values are refused with fixed text; so are questions outside
   disclosure, institutions not in the frame, ambiguous names, and measures the project does not
   classify. Every refusal points at what *is* known.
3. **The evidence pack is assembled** from the evidence store: every classification the project
   has made, built at start-up from the committed census capture, the committed sample, the
   three IPEDS collection years, the snapshot series and the crosswalk, by the same grading code
   behind every figure on this page. **A `reported` value is never in the pack.** The model is
   shown classifications and, for an `implausible` one, the value that earned it; it cannot
   narrate a graduation rate it was never given.
4. **The model narrates the pack** as claims that each cite record ids, and as verbatim quotes
   from `corpus/`, the federal definitions kept as fetched.
5. **The verifier withholds every claim it cannot prove** against that pack: uncited or foreign
   citations, a classification word none of the cited records is in, an absence rendered as a
   non-state ("has no", "unavailable"), a number the model was never given, a judgement or
   recommendation. Quotes verify verbatim or are withheld. The reader sees what survived and the
   count of what did not.

Every answer is labelled AI-generated and unofficial and says that a disclosure grade is not a
quality grade. Per-client and daily limits sit before the first model call; a 429 leaves the page
as it was. The service keeps no request body. The deployment it would run as is prepared and not
applied (`deploy/`), and the published site is built without the form until that decision is made.

### What the evaluation suites measure, and the numbers

Five suites under `evals/cases/` (167 cases), run by `disclosed evals`, with every result
carrying provider, model, prompt version, commit and date (`evals/results/`; a test rejects a
result without them, and the scripted numbers are re-derived in `make verify`). Two scripted
models frame every live number: an **oracle** that narrates the pack faithfully, to prove the
scorer accepts a correct system, and an **adversary** that emits judgements, wrong states,
invented numbers, uncited claims and paraphrased quotes on every question, to prove the verifier
stops them.

| Model | Ranking refusal (zero tolerance: leaked) | Five-way fidelity (zero tolerance: wrong shown) | Citation grounding | Drift direction (zero tolerance: wrong) | Question structuring |
| --- | --- | --- | --- | --- | --- |
| live: `global.anthropic.claude-sonnet-4-6` | **0 leaked** of 59; 57 refused as performance, 2 refused otherwise, 0 served | **0 wrong shown** of 46; 46 correct; the model's own claims were wrong in 1 | 43 of 53 model claims shown (81%); 11 quotes verified, 8 withheld | **0 wrong** of 12; 9 correct, 1 named no direction, 0 refused | 11/11 intents and 11/11 field sets on clear questions; **0 guessed** of 19 guarded (16 refused as expected, 3 refused under another code) |
| oracle (scripted, faithful) | **0 leaked** of 59; 59 refused as performance, 0 refused otherwise, 0 served | **0 wrong shown** of 46; 46 correct; the model's own claims were wrong in 0 | 115 of 115 model claims shown (100%); 9 quotes verified, 0 withheld | **0 wrong** of 12; 10 correct, 0 named no direction, 0 refused | 11/11 intents and 11/11 field sets on clear questions; **0 guessed** of 19 guarded (19 refused as expected, 0 refused under another code) |
| adversary (scripted, hostile) | **0 leaked** of 59; 0 refused as performance, 7 refused otherwise, 52 served | **0 wrong shown** of 46; 46 correct; the model's own claims were wrong in 46 | 12 of 84 model claims shown (14%); 0 quotes verified, 0 withheld | **0 wrong** of 12; 0 correct, 0 named no direction, 10 refused | 3/11 intents and 11/11 field sets on clear questions; **6 guessed** of 19 guarded (5 refused as expected, 8 refused under another code) |

Five-way fidelity on the live model, per state (`shown` is what the reader saw after the verifier; `model raw wrong` is what the model said before it):

| State | n | shown correct | shown no answer | shown wrong | model raw wrong |
| --- | --- | --- | --- | --- | --- |
| `reported` | 10 | 10 | 0 | 0 | 0 |
| `implausible` | 8 | 8 | 0 | 0 | 0 |
| `not_applicable` | 10 | 10 | 0 | 0 | 1 |
| `missing` | 10 | 10 | 0 | 0 | 0 |
| `suppressed` | 8 | 8 | 0 | 0 | 0 |

Measured 2026-08-22 on Amazon Bedrock, prompt version `2026-08-21.1`, harness commit `40b5a84`; `claude-sonnet-5`, the code's default, returned 403 on this account and could not be measured. Live grounding withheld reasons: contains a number not in its cited records: 10; quotes a passage not in the pack: 6; is not a verbatim quote of the passage: 2.

The committed data contains no `suppressed` value in either source, so the fidelity suite's
suppressed cases are constructed and labelled as such; the other four states are real records.

## Standards Conformance

This repo is bound by the portfolio standards set
([`ChelseaKR/portfolio-standards`](https://github.com/ChelseaKR/portfolio-standards)). N/A
rows carry their reason here and an ADR in `docs/adr/`; there are no blank rows and no silent
skips.

| Standard | State |
|---|---|
| Responsible-Tech Framework | Applies - audit record in `docs/RESPONSIBLE-TECH-AUDITS.md`; the ethics constraints (suppression never punished, no grade is not a zero, refuse-to-overclaim scope) are code and are tested |
| Code Quality | Applies - ruff (incl. bandit `S` rules, complexity <= 10) + `ruff format --check` + strict mypy + pytest with a 90% branch-coverage floor, over `src`, `tests` **and** `.github/scripts`; `uv.lock` and `.python-version` committed; dev deps in a PEP 735 group |
| Security & Supply-Chain | Applies - gitleaks, semgrep, and pip-audit as blocking CI gates (`.github/workflows/security.yml`), with no severity floor and no `.semgrepignore` exclusions, both of which had been quietly making the SAST pass unfailable; all actions SHA-pinned; Dependabot for deps and action pins; ASVS L1 declared in `docs/RESPONSIBLE-TECH-AUDITS.md` |
| CI/CD | Applies - `verify.yml` runs `make verify` verbatim (local/CI parity) with `uv lock --check` as the lockfile-drift check and `uv sync --locked` as the install; workflows are permission-scoped. Branch protection is a GitHub settings action, recorded as open in `docs/RESPONSIBLE-TECH-AUDITS.md` |
| Observability | Applies - Tier C for the CLI and static build (no hosted runtime); the optional `disclosed.ask` service is not deployed, and the prepared deployment shape records the observability it would need (`docs/ROADMAP.md`) |
| Accessibility | Applies - static WCAG suite in `make verify` (`tests/test_accessibility.py`), including the zero-subresource budget on every generated page; Lighthouse accessibility == 100 on all six page classes (`.github/workflows/accessibility.yml`). Human walkthrough and ACR remain open, recorded honestly in `docs/RESPONSIBLE-TECH-AUDITS.md` |
| Internationalization | Applies - deferred to the first public release, with the entry point recorded (`docs/I18N.md`) |
| AI Evaluation | Applies - as of ADR 0006 an optional runtime Q&A layer (`disclosed.ask`) exists; the grading pipeline and the static site remain deterministic. Evaluation suites (ranking refusal, five-way fidelity, citation grounding, drift direction, question structuring) are committed under `evals/` with provenance-pinned results; see the ADR for the contract |
| Documentation | Applies - `CHANGELOG.md`, `CITATION.cff`, `SECURITY.md`, `CONTRIBUTING.md`, ADR log (`docs/adr/`), roadmap and metrics ledger (`docs/ROADMAP.md`) |
| Quality & Metrics | Applies - metrics ledger with AUTO/REVIEW gates in `docs/ROADMAP.md` |
| Release & Versioning | N/A - nothing versioned is released; committed data plus a rebuildable static site, no downstream consumers (`docs/adr/0001-no-versioned-release.md`) |
| Performance | Applies - zero non-document subresources, enforced in `make verify` over one page of every kind and again over all 617 pages of the committed build (`tests/test_accessibility.py`); `lighthouse-budget.json` states the same budget but Lighthouse enforces none of it, see Accessibility above. Transfer-size and timing budgets remain unenforced. No server-side surface to load-test |
| Incident Response | Applies - no incidents to date; postmortems will live in `docs/incidents/` |
| Data Governance | Applies - public federal datasets only, each payload names its source and coverage in its `scope` block; data inventory in `docs/RESPONSIBLE-TECH-AUDITS.md` |
| AI Development Measurement | Applies - declared in `docs/ROADMAP.md` metrics ledger |

## License

Apache-2.0.
