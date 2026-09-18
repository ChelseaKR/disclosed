"""Google Analytics 4 loads only where, and only as, ADR 0011 says.

``disclosed.analytics.loader`` renders one inline ``<script id="analytics">`` that
``disclosed.site.build`` puts in the head of every page when it is given a measurement ID. It
must load nothing at all -- no ``dataLayer``, no request, no cookie -- off the published address,
under Global Privacy Control or Do Not Track, or after the reader opts out. When it does load,
the configuration is the one the privacy page describes. Without an ID the build is byte-for-byte
what it was.

A string match over the script cannot tell a guard that runs from one that does not, so the
script is *executed* here, under Node, against a stub window, navigator and document, once per
case. Node is on every GitHub runner; locally these tests skip without it, and in CI (``CI``
set) they fail instead, so a runner that lost Node cannot turn them green by skipping.

The expected values are written out rather than read back out of the module under test: an
expectation parsed out of the thing it checks moves with the mistake.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from disclosed import analytics, cli, site

_ROOT = Path(__file__).resolve().parent.parent

#: GA4 property 554861135's web stream. Public: it is in every page served.
MEASUREMENT_ID = "G-5SE0M4LS60"
PUBLISHED = "https://chelseakr.github.io/disclosed/"
GTAG_SRC = f"https://www.googletagmanager.com/gtag/js?id={MEASUREMENT_ID}"
OPT_OUT_KEY = "disclosed:analytics-opt-out"

#: The 27 EU member states, the rest of the EEA, the UK and Switzerland.
DENIED_REGIONS = [
    *("AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE"),
    *("IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"),
    *("IS", "LI", "NO"),
    *("GB", "CH"),
]

ADS_DENIED = {"ad_storage": "denied", "ad_user_data": "denied", "ad_personalization": "denied"}

_REPORT: dict[str, Any] = {
    "scope": {
        "kind": "sample",
        "source": "College Scorecard",
        "institutions": 1,
        "states": 1,
        "universe": 6300,
        "coverage": 1 / 6300,
        "note": "A slice.",
    },
    "institutions": 1,
    "ungradeable": 0,
    "overall": {
        "label": "all institutions",
        "graded": 1,
        "ungradeable": 0,
        "mean_score": 0.5,
        "worst_fields": [["Admission rate", 1]],
    },
    "by_state": [
        {
            "label": "CA",
            "graded": 1,
            "ungradeable": 0,
            "mean_score": 0.5,
            "worst_fields": [["Admission rate", 1]],
        }
    ],
    "implausible": [],
    "grades": [
        {
            "unit_id": "1",
            "name": "Graded College",
            "state": "CA",
            "score": 0.5,
            "letter": "D",
            "fields": {"Admission rate": "missing", "Enrollment": "reported"},
        }
    ],
}

#: Runs the loader's script against stubs and prints what it did, as JSON.
HARNESS = r"""
const fs = require("fs");
const code = fs.readFileSync(process.argv[2], "utf8");
const scenario = JSON.parse(process.argv[3]);
const appended = [];
const listeners = {};
function control(text, hidden) {
  return {
    hidden, textContent: text, handlers: {},
    addEventListener(type, handler) { this.handlers[type] = handler; },
  };
}
const elements = scenario.footer === false ? {} : {
  "analytics-choice": control("", true),
  "analytics-opt-out": control("Opt out of analytics", true),
  "analytics-status": control("", false),
};
const store = new Map(Object.entries(scenario.storage || {}));
const storage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => { store.set(key, String(value)); },
  removeItem: (key) => { store.delete(key); },
};
const url = new URL(scenario.url);
const window = {
  location: {
    href: url.href, protocol: url.protocol, hostname: url.hostname, origin: url.origin,
    pathname: url.pathname, search: url.search, hash: url.hash,
  },
};
if ("windowDnt" in scenario) window.doNotTrack = scenario.windowDnt;
Object.defineProperty(window, "localStorage", {
  get() {
    if (scenario.storageBlocked) throw new Error("SecurityError: storage is blocked");
    return storage;
  },
});
const navigator = {};
if ("gpc" in scenario) navigator.globalPrivacyControl = scenario.gpc;
if ("dnt" in scenario) navigator.doNotTrack = scenario.dnt;
if ("msDnt" in scenario) navigator.msDoNotTrack = scenario.msDnt;
const document = {
  head: { appendChild(el) { appended.push({ tag: el.tagName, src: el.src, async: el.async }); } },
  createElement(tag) { return { tagName: tag.toUpperCase() }; },
  getElementById(id) { return elements[id] || null; },
  addEventListener(type, handler) { listeners[type] = handler; },
};
new Function("window", "navigator", "document", code)(window, navigator, document);
if (listeners.DOMContentLoaded) listeners.DOMContentLoaded();
const button = elements["analytics-opt-out"];
for (let i = 0; i < (scenario.clicks || 0); i++) {
  if (button && button.handlers.click) button.handlers.click();
}
const view = (id) =>
  elements[id] ? { hidden: elements[id].hidden, text: elements[id].textContent } : null;
process.stdout.write(JSON.stringify({
  dataLayer: window.dataLayer === undefined ? null : window.dataLayer.map((a) => Array.from(a)),
  appended,
  listeners: Object.keys(listeners),
  choice: view("analytics-choice"),
  button: view("analytics-opt-out"),
  status: view("analytics-status"),
  storage: Object.fromEntries(store),
  gaDisable: window["ga-disable-" + scenario.id] ?? null,
}));
"""


def _script(markup: str) -> str:
    """The JavaScript inside one ``<script id="analytics">`` block."""
    blocks = re.findall(r'<script id="analytics">(.*?)</script>', markup, re.DOTALL)
    assert len(blocks) == 1, f"{len(blocks)} analytics scripts, not 1"
    return str(blocks[0])


def run(script: str, tmp_path: Path, **scenario: Any) -> dict[str, Any]:
    """Execute ``script`` under Node against stubs, and return what it did."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is not on PATH in CI, so the analytics script cannot be executed")
        pytest.skip("node is not installed; the analytics script is executed in CI")
    scenario.setdefault("url", PUBLISHED)
    scenario.setdefault("id", MEASUREMENT_ID)
    code = tmp_path / "analytics.js"
    code.write_text(script, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, files this test wrote
        [node, str(harness), str(code), json.dumps(scenario)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result: dict[str, Any] = json.loads(completed.stdout)
    return result


def loaded_nothing(result: dict[str, Any]) -> bool:
    return result["dataLayer"] is None and result["appended"] == []


def sabotage(script: str, line: str, replacement: str = "") -> str:
    """Remove or replace one exact line of the script, and prove that it happened.

    A negative control whose edit silently matched nothing would leave the script intact and
    read as a pass, so the edit is asserted before any verdict is read.
    """
    assert script.count(line) == 1, f"the line to sabotage is not in the script once: {line!r}"
    changed = script.replace(line, replacement)
    assert changed != script
    assert line not in changed
    return changed


@pytest.fixture(scope="module")
def script() -> str:
    return _script(analytics.loader(MEASUREMENT_ID, site.ENGLISH))


def _build(out: Path, ga4_id: str | None) -> list[site.Page]:
    return site.build(
        _REPORT,
        out,
        origin="https://chelseakr.github.io/disclosed",
        generated="2026-09-17",
        ga4_id=ga4_id,
    )


# -- where the ID lives ------------------------------------------------------------------------


class TestTheMeasurementId:
    def test_the_committed_id_is_this_sites_property(self) -> None:
        assert analytics.GA4_MEASUREMENT_ID == MEASUREMENT_ID
        assert analytics.OPT_OUT_KEY == OPT_OUT_KEY
        assert list(analytics.DENIED_REGIONS) == DENIED_REGIONS
        assert len(set(DENIED_REGIONS)) == 32

    @pytest.mark.parametrize("value", [None, ""])
    def test_no_id_is_none(self, value: str | None) -> None:
        assert analytics.measurement_id_or_none(value) is None

    @pytest.mark.parametrize("value", ["UA-12345-1", "g-5se0m4ls60", " G-5SE0M4LS60", "G-"])
    def test_a_malformed_id_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="is not of the form"):
            analytics.measurement_id_or_none(value)
        with pytest.raises(ValueError):
            analytics.loader(value, site.ENGLISH)

    def test_the_loader_refuses_an_empty_id(self) -> None:
        with pytest.raises(ValueError, match="needs a measurement ID"):
            analytics.loader("", site.ENGLISH)

    def test_the_cli_publishes_the_committed_id_by_default(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """pages.yml runs `disclosed site` without `--ga4-id`, so the default is what ships."""
        report = tmp_path / "report.json"
        report.write_text(json.dumps(_REPORT), encoding="utf-8")
        args = ["site", "--report", str(report), "--generated", "2026-09-17"]

        assert cli.main([*args, "--out", str(tmp_path / "default")]) == 0
        home = (tmp_path / "default" / "index.html").read_text(encoding="utf-8")
        assert home.count(f"var GA4_ID = {json.dumps(MEASUREMENT_ID)};") == 1

        assert cli.main([*args, "--out", str(tmp_path / "off"), "--ga4-id", ""]) == 0
        assert "analytics" not in (tmp_path / "off" / "index.html").read_text(encoding="utf-8")

        with pytest.raises(SystemExit):
            cli.main([*args, "--out", str(tmp_path / "bad"), "--ga4-id", "UA-1-1"])
        assert "is not of the form 'G-XXXXXXXXXX'" in capsys.readouterr().err
        assert not (tmp_path / "bad").exists()


# -- the build, with and without an ID ---------------------------------------------------------


class TestTheBuild:
    def test_without_an_id_the_build_is_byte_for_byte_what_it_was(self, tmp_path: Path) -> None:
        """No script, no privacy page, no analytics sentence: identical to not passing one."""
        site.build(
            _REPORT,
            tmp_path / "before",
            origin="https://chelseakr.github.io/disclosed",
            generated="2026-09-17",
        )
        _build(tmp_path / "none", None)
        before = {p.relative_to(tmp_path / "before"): p for p in (tmp_path / "before").rglob("*")}
        none = {p.relative_to(tmp_path / "none"): p for p in (tmp_path / "none").rglob("*")}
        assert before.keys() == none.keys()
        for relative, path in before.items():
            if path.is_file():
                assert path.read_bytes() == none[relative].read_bytes(), relative
        for page in (tmp_path / "none").rglob("index.html"):
            text = page.read_text(encoding="utf-8")
            assert "<script" not in text.replace('<script type="application/ld+json">', ""), page
            assert "Google" not in text, page
        assert not (tmp_path / "none" / "privacy").exists()

    def test_with_an_id_every_page_carries_one_loader_and_one_control(self, tmp_path: Path) -> None:
        pages = _build(tmp_path, MEASUREMENT_ID)
        loader = analytics.loader(MEASUREMENT_ID, site.ENGLISH)
        written = sorted(tmp_path.rglob("index.html"))
        assert len(written) == len(pages) > 3
        for page in written:
            text = page.read_text(encoding="utf-8")
            assert text.count(loader) == 1, page
            assert text.index(loader) < text.index("<title>") < text.index("</head>"), page
            assert text.count('<span id="analytics-choice" hidden>') == 1, page
            assert text.count('id="analytics-opt-out" class="link-button" hidden>') == 1, page
            assert text.count('<span id="analytics-status" role="status"></span>') == 1, page
            footer = text[text.index("<footer>") : text.index("</footer>")]
            assert "counts visits with Google Analytics 4" in footer, page
            assert re.search(r'href="(\.\./)*privacy/"', footer), page

    def test_the_privacy_page_is_written_and_listed(self, tmp_path: Path) -> None:
        _build(tmp_path, MEASUREMENT_ID)
        assert (tmp_path / "privacy" / "index.html").exists()
        sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
        assert "<loc>https://chelseakr.github.io/disclosed/privacy/</loc>" in sitemap

    def test_the_privacy_page_describes_what_the_script_does(self, tmp_path: Path) -> None:
        _build(tmp_path, MEASUREMENT_ID)
        text = re.sub(r"\s+", " ", (tmp_path / "privacy" / "index.html").read_text("utf-8"))
        for fact in (
            "Google Analytics 4",
            "names the institution, state or report you are reading",
            f"<code>_ga_{MEASUREMENT_ID.removeprefix('G-')}</code>",
            f"<code>{OPT_OUT_KEY}</code>",
            "Global Privacy Control or Do Not Track",
            "Google signals and ad personalization are off",
            "European Economic Area, the UK and Switzerland",
            "cookieless ping",
            "14 months",
            "never sent anywhere",
            "without anything after a <code>#</code> or <code>?</code>",
        ):
            assert fact in text, f"the privacy page no longer says {fact!r}"

    def test_a_malformed_id_fails_the_build_before_anything_is_written(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError):
            _build(tmp_path / "out", "UA-1-1")
        assert not (tmp_path / "out").exists()


# -- the published page, with no signal: GA loads, configured as documented -------------------


@pytest.mark.parametrize(
    "url",
    [
        PUBLISHED,
        PUBLISHED + "institution/110468/",
        PUBLISHED + "privacy/",
        "https://chelseakr.github.io/disclosed",
        PUBLISHED + "state/CA/?from=somewhere#frag-ment",
    ],
)
def test_on_the_published_site_ga_loads_with_the_documented_config(
    script: str, tmp_path: Path, url: str
) -> None:
    result = run(script, tmp_path, url=url)
    path = re.split(r"[#?]", url, maxsplit=1)[0].removeprefix("https://chelseakr.github.io")
    commands = result["dataLayer"]
    assert [c[0] for c in commands] == ["consent", "consent", "js", "config"]
    assert commands[0] == ["consent", "default", {**ADS_DENIED, "analytics_storage": "granted"}]
    assert commands[1] == [
        "consent",
        "default",
        {**ADS_DENIED, "analytics_storage": "denied", "region": DENIED_REGIONS},
    ]
    assert commands[3] == [
        "config",
        MEASUREMENT_ID,
        {
            "page_location": f"https://chelseakr.github.io{path}",
            "allow_google_signals": False,
            "allow_ad_personalization_signals": False,
        },
    ]
    assert result["appended"] == [{"tag": "SCRIPT", "src": GTAG_SRC, "async": True}]
    assert "frag-ment" not in json.dumps(commands)
    assert "from=somewhere" not in json.dumps(commands)
    assert result["choice"]["hidden"] is False
    assert result["button"] == {"hidden": False, "text": "Opt out of analytics"}


# -- not the published site: nothing loads, nothing is offered ---------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:8000/state/CA/",
        "http://localhost:8000/disclosed/",
        "http://chelseakr.github.io/disclosed/",
        "https://chelseakr.github.io/",
        "https://chelseakr.github.io/ctdl-validate/",
        "https://chelseakr.github.io/disclosed-fork/",
        "https://someone-else.github.io/disclosed/",
        "https://chelseakr.github.io.example.com/disclosed/",
        "https://example.test/",
    ],
)
def test_off_the_published_site_nothing_loads(script: str, tmp_path: Path, url: str) -> None:
    result = run(script, tmp_path, url=url)
    assert loaded_nothing(result)
    assert result["listeners"] == []
    assert result["choice"]["hidden"] is True


# -- GPC, DNT and the opt-out: nothing loads ---------------------------------------------------


@pytest.mark.parametrize(
    "signal",
    [
        {"gpc": True},
        {"dnt": "1"},
        {"dnt": "yes"},
        {"windowDnt": "1"},
        {"msDnt": "1"},
        {"gpc": True, "dnt": "1"},
    ],
)
def test_gpc_or_dnt_loads_nothing(script: str, tmp_path: Path, signal: dict[str, Any]) -> None:
    result = run(script, tmp_path, **signal)
    assert loaded_nothing(result)
    assert result["choice"]["hidden"] is False
    assert result["button"]["hidden"] is True
    assert result["status"]["text"] == (
        "Analytics is off: your browser sends Global Privacy Control or Do Not Track."
    )


@pytest.mark.parametrize("signal", [{"gpc": False}, {"dnt": "0"}, {"dnt": "unspecified"}])
def test_a_signal_that_is_off_does_not_stop_ga(
    script: str, tmp_path: Path, signal: dict[str, Any]
) -> None:
    assert not loaded_nothing(run(script, tmp_path, **signal))


def test_the_opt_out_flag_loads_nothing(script: str, tmp_path: Path) -> None:
    result = run(script, tmp_path, storage={OPT_OUT_KEY: "1"})
    assert loaded_nothing(result)
    assert result["button"] == {"hidden": False, "text": "Opt back in"}
    assert result["status"]["text"] == "Analytics is off on this device."


@pytest.mark.parametrize(
    "storage",
    [
        {},
        {OPT_OUT_KEY: "0"},
        {"analytics-opt-out": "1"},
        {"ctdl-validate:analytics-opt-out": "1"},
        {"trout-truck:analytics-opt-out": "1"},
    ],
)
def test_only_this_sites_key_opts_out(script: str, tmp_path: Path, storage: dict[str, str]) -> None:
    """chelseakr.github.io project sites share one origin, so one localStorage."""
    assert not loaded_nothing(run(script, tmp_path, storage=storage))


def test_a_click_is_remembered_and_a_second_click_undoes_it(script: str, tmp_path: Path) -> None:
    once = run(script, tmp_path, clicks=1)
    assert once["storage"] == {OPT_OUT_KEY: "1"}
    assert once["gaDisable"] is True
    assert once["button"]["text"] == "Opt back in"
    assert once["status"]["text"] == "Analytics is off on this device."

    assert loaded_nothing(run(script, tmp_path, storage=once["storage"]))

    twice = run(script, tmp_path, clicks=2)
    assert twice["storage"] == {}
    assert twice["gaDisable"] is False
    assert twice["button"]["text"] == "Opt out of analytics"
    assert twice["status"]["text"] == "Analytics is back on from the next page you open."


def test_blocked_storage_still_honors_a_click_for_this_page(script: str, tmp_path: Path) -> None:
    result = run(script, tmp_path, storageBlocked=True, clicks=1)
    assert not loaded_nothing(result), "blocked storage is not an opt-out"
    assert result["gaDisable"] is True
    assert "lasts only until you leave this page" in result["status"]["text"]


def test_a_page_without_the_footer_control_still_loads(script: str, tmp_path: Path) -> None:
    assert not loaded_nothing(run(script, tmp_path, footer=False))


# -- negative controls: the harness sees a missing guard ---------------------------------------


@pytest.mark.parametrize(
    ("guard", "scenario"),
    [
        ("  if (n.globalPrivacyControl === true) return;\n", {"gpc": True}),
        ('  if (dnt === "1" || dnt === "yes") return;\n', {"dnt": "1"}),
        ("  if (optedOut) return;\n", {"storage": {OPT_OUT_KEY: "1"}}),
        (
            '  if (l.protocol !== "https:" || l.hostname !== PUBLISHED_HOST) return;\n',
            {"url": "http://127.0.0.1:8000/disclosed/"},
        ),
        (
            '  if (l.pathname !== "/disclosed" && l.pathname.indexOf(PUBLISHED_PATH) !== 0) '
            "return;\n",
            {"url": "https://chelseakr.github.io/ctdl-validate/"},
        ),
    ],
)
def test_removing_a_guard_is_caught(
    script: str, tmp_path: Path, guard: str, scenario: dict[str, Any]
) -> None:
    assert loaded_nothing(run(script, tmp_path, **scenario)), "the intact script must hold"
    broken = sabotage(script, guard)
    assert not loaded_nothing(run(broken, tmp_path, **scenario)), (
        f"with {guard!r} removed the harness still saw nothing load, so it cannot see that guard"
    )


def test_turning_google_signals_on_is_caught(script: str, tmp_path: Path) -> None:
    broken = sabotage(script, "allow_google_signals: false,", "allow_google_signals: true,")
    assert run(broken, tmp_path)["dataLayer"][3][2]["allow_google_signals"] is True


# -- the claims around it ----------------------------------------------------------------------

#: Claims that were true before the site had analytics and are false with it. Each is matched
#: case-insensitively against whitespace-normalized text.
FALSE_NOW = (
    "there are no scripts",
    "the site ships no script",
    "no script anywhere",
    "there is no server, no accounts, no telemetry",
    "without it the site has no script at all",
)

#: Where the site's own account of itself lives. docs/RESPONSIBLE-TECH-AUDITS.md is append-only
#: (AGENTS.md), so its findings keep their dated wording and the addendum supersedes them.
CLAIM_FILES = (
    "README.md",
    "AGENTS.md",
    "docs/ROADMAP.md",
    ".github/workflows/accessibility.yml",
    "src/disclosed/site.py",
    "src/disclosed/cli.py",
)


def test_nothing_still_claims_the_site_has_no_script_or_telemetry() -> None:
    for name in CLAIM_FILES:
        text = re.sub(r"\s+", " ", (_ROOT / name).read_text(encoding="utf-8")).lower()
        text = re.sub(r"\s*#\s*", " ", text)
        found = [claim for claim in FALSE_NOW if claim in text]
        assert found == [], f"{name} still says {found}"


def test_the_false_claims_list_would_have_caught_the_old_wording() -> None:
    """The negative control for the list above: every claim matches the sentence it replaced."""
    old = (
        "there are no scripts, no external stylesheets. the site ships no script, so 34 ms. "
        "without it the build is byte-for-byte what it was, with no script anywhere. "
        "there is no server, no accounts, no telemetry. "
        "without it the site has no script at all"
    )
    assert [claim for claim in FALSE_NOW if claim not in old] == []
