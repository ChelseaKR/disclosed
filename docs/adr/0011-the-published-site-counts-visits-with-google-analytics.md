# 0011. The published site counts visits with Google Analytics 4

- Status: Accepted
- Date: 2026-09-17
- Deciders: Chelsea Kelly-Reif

## Context

On 2026-09-17 the owner decided that every public site in the portfolio gets Google Analytics 4,
with its privacy pages and claims updated to match. https://chelseakr.github.io/disclosed/ is one
of those sites.

Until now this site shipped no script. The README said so, the budget test enforced it over every
generated page, and `tests/test_accessibility.py` put the reason in its failure message: "a page
about undisclosed information should not be quietly shipping a tracker". This decision reverses
that part. What this ADR can still hold is the other half of the sentence: the tracker is not
shipped quietly. It is disclosed on every page, described precisely on a privacy page, and fenced
in the test that used to forbid it.

## Decision

`src/disclosed/analytics.py` holds the measurement ID, `G-5SE0M4LS60` (GA4 property 554861135,
with 14-month retention and Google signals off), and renders one inline `<script id="analytics">`.
`disclosed site` passes that ID by default, so `pages.yml` publishes it. `--ga4-id ""` builds a
site with no analytics. `site.build()` defaults to none, and without an ID the build is
byte-for-byte what it was: no script, no privacy page, no analytics sentence.

With an ID, every page carries the loader in its head and, in its footer, a sentence saying the
site counts visits with Google Analytics 4, a link to the new `privacy/` page, and an opt-out
control. The loader loads nothing at all (no `dataLayer`, no request to Google, no cookie) unless
all four of these hold:

1. the ID is well formed;
2. the page is served from `https://chelseakr.github.io/disclosed/`. A local build, the Lighthouse
   job on 127.0.0.1 and a fork's Pages site all fail this;
3. the browser sends neither Global Privacy Control nor Do Not Track;
4. the reader has not opted out. The footer's "Opt out of analytics" button sets
   `disclosed:analytics-opt-out` in localStorage. The key names this project because every
   `chelseakr.github.io` project site shares one origin, and so one localStorage.

When it loads, it sets Consent Mode v2 defaults. The three ad signals are denied everywhere.
`analytics_storage` is denied in the EEA, the UK and Switzerland, where GA sends cookieless pings,
and granted elsewhere. It also turns off Google signals and ad personalisation, and sends
`page_location` as the origin and path only.

The privacy page says the plain thing a reader of this site most needs to know: a page's address
names the institution, state or report being read, so Google learns which ones were looked at.

## The budget

`lighthouse-budget.json` is unchanged, and that is a decision rather than an omission. It states
the pages as this project writes and serves them. As built, the loader fetches nothing: it checks
the address first, and it returns on every host but the published one, including the 127.0.0.1
where the Lighthouse job measures the timing lines.

On the published host, and only there, it adds gtag.js. That is one third-party script, 154,395
bytes brotli-compressed when measured on 2026-09-17, plus GA's measurement requests. Those are
outside the budget by the owner's decision, and this is the place that says so.

`tests/test_accessibility.py` fences the exception the way it already fences the schema.org data
block. The loader is exempt from the count only as its exact bytes for the committed ID, once, in
the head. A loader with a byte changed, a second copy, a copy in the body, or one for another ID
is still counted.

## Consequences

- The site now has a data flow about its readers: page paths, referrer, browser and device, a
  coarse location Google derives from the IP address, scroll, outbound-click and download events,
  and a random client ID in `_ga`/`_ga_5SE0M4LS60` cookies, processed by Google in the US. The
  grading pipeline, the dataset and the CSV export are untouched and still carry no personal data.
  `docs/RESPONSIBLE-TECH-AUDITS.md` records this in an addendum, because that file is append-only.
- The site grows by one page, from 619 to 620, and the page count is restated wherever it is
  cited.
- `tests/test_analytics.py` runs the loader under Node. No ID, the wrong host or path, GPC, each
  form of DNT and the opt-out each load nothing; otherwise GA loads with the configuration above.
  Its negative controls remove a guard, assert that the removal landed, and assert that GA then
  loads.
- Owner follow-up in the GA4 property, with no API: user-provided data collection off, data
  sharing off, and the Data Processing Terms accepted.
