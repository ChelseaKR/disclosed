PYTHON ?= .venv/bin/python

.PHONY: verify lint typecheck test fetch grade site dataset crosscheck national census-report \
        snapshot replay census-replay scorecard-snapshot-replay ipeds-snapshots registry-fetch \
        registry-join registry-replay registry-properties registry-property-report \
        registry-property-replay reachability-capture reachability-report reachability-replay

verify: lint typecheck test

# .github/scripts is in the gate for the same reason data/HD2023.zip is in the repository: the
# thing that decides whether the published site may name the host it names cannot be the one
# file nobody checks. check_site_origin.py sat outside `src` and was therefore outside the lint,
# outside strict mypy, and outside the coverage floor, so the 98% the report prints was 98% of
# the code that was being looked at.
LINTED = src tests .github/scripts tools

lint:
	$(PYTHON) -m ruff check $(LINTED)
	$(PYTHON) -m ruff format --check $(LINTED)

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q

# Walk the College Scorecard once, with provenance, into a capture that everything else can read
# without a key. Needs DATA_GOV_API_KEY for a full walk; the page cache makes a rerun free.
fetch:
	$(PYTHON) -m disclosed.cli fetch --out data/census/scorecard.json --cache-dir .cache/scorecard

grade:
	$(PYTHON) -m disclosed.cli grade --out data/report.json

site:
	$(PYTHON) -m disclosed.cli site --report data/report.json --national data/national.json --scorecard-census data/scorecard-census.json --receipts-from data/sample.json --snapshots-from data/snapshots --package datapackage.json --disputes-from disputes --reachability data/reachability-report.json --out site --generated $(shell date -u +%F)

dataset:
	$(PYTHON) -m disclosed.cli dataset --report data/report.json --out data/dataset.csv --disputes-from disputes --package datapackage.json --root .

crosscheck:
	$(PYTHON) -m disclosed.cli crosscheck --cache data/HD2023.zip --characteristics data/IC2023.zip --source data/sample.json --out data/crosscheck.json

national:
	$(PYTHON) -m disclosed.cli national --report data/crosscheck.json --out data/national.json

# Grades the committed full-population capture (no key: `grade --source` reads the file) and
# reduces it to the artifact the census site page and the README's beside-figure are built from.
# `census-replay` below is the test this target's output has to pass.
census-report:
	$(PYTHON) -m disclosed.cli grade --source data/census/scorecard.json --out /tmp/census-graded.json
	$(PYTHON) -m disclosed.cli census-report --report /tmp/census-graded.json --source data/census/scorecard.json --out data/scorecard-census.json

snapshot:
	$(PYTHON) -m disclosed.cli snapshot --taken $(TAKEN) --out data/snapshots/scorecard/$(TAKEN).json

# The daily Scorecard series, regenerated from the committed capture. Only one date in it can be
# checked this way: the capture is a single walk, and the other days' captures were ninety-day
# workflow artifacts that have since expired. That one is enough to make the series reproducible
# rather than merely committed, and it is the day this target replays -- read out of the capture's
# own provenance, so refreshing the capture moves it. No network and no key.
# `tests/test_census_replay.py::TestTheCommittedScorecardSnapshots` is the pytest form and is what
# gates `make verify`; this is what you run to see the diff when it fails.
scorecard-snapshot-replay:
	$(PYTHON) -m disclosed.cli grade --source data/census/scorecard.json --out /tmp/census-graded.json
	taken=$$($(PYTHON) -c "import json; print(json.load(open('data/census/scorecard.json'))['provenance']['walked_at'][:10])") && \
	$(PYTHON) -m disclosed.cli snapshot --report /tmp/census-graded.json --taken $$taken --out /tmp/scorecard-snapshot.json && \
	diff -u data/snapshots/scorecard/$$taken.json /tmp/scorecard-snapshot.json && \
	echo "data/snapshots/scorecard/$$taken.json replays exactly"

# Regenerate the committed national artifact from the committed archives and show what moved. The
# test suite asserts these are identical on every push; this is the target you run when it says
# they are not, because a diff is more use than an assertion failure. No network and no key: both
# IPEDS archives are in data/.
replay: crosscheck
	$(PYTHON) -m disclosed.cli national --report data/crosscheck.json --out /tmp/national.json
	diff -u data/national.json /tmp/national.json && echo "data/national.json replays exactly"

# Same contract as `replay`, for the Scorecard census: no network and no key, because the capture
# is committed. `tests/test_census_replay.py` is the pytest form of this same check and is the one
# that actually gates `make verify`; this target is what you run to see the diff when it fails.
census-replay:
	$(PYTHON) -m disclosed.cli grade --source data/census/scorecard.json --out /tmp/census-graded.json
	$(PYTHON) -m disclosed.cli census-report --report /tmp/census-graded.json --source data/census/scorecard.json --out /tmp/scorecard-census.json
	diff -u data/scorecard-census.json /tmp/scorecard-census.json && echo "data/scorecard-census.json replays exactly"

# Walk the Credential Registry once, with provenance, into the capture the join measurement is
# read from. No key and no quota; the registry is public and unauthenticated. Committed for the
# reason the Scorecard census capture is: the registry's publishers edit it continuously, so a
# rerun does not reproduce it and the file is the only durable record of what it held that day.
registry-fetch:
	$(PYTHON) -m disclosed.cli registry-fetch --out data/registry/organizations.json --cache-dir .cache/registry

# Measure the join, offline, from three committed inputs. docs/ROADMAP.md names this as the thing
# that comes before a Credential Registry adapter; docs/adr/0007 records what the answer licenses.
registry-join:
	$(PYTHON) -m disclosed.cli registry-join --capture data/registry/organizations.json --cache data/HD2023.zip --source data/census/scorecard.json --out data/registry-join.json

# Same contract as `replay`: no network and no key, because all three inputs are committed.
# `tests/test_registry.py::TestTheCommittedMeasurement` is the pytest form and is what gates
# `make verify`; this target is what you run to see the diff when it fails.
registry-replay:
	$(PYTHON) -m disclosed.cli registry-join --capture data/registry/organizations.json --cache data/HD2023.zip --source data/census/scorecard.json --out /tmp/registry-join.json
	diff -u data/registry-join.json /tmp/registry-join.json && echo "data/registry-join.json replays exactly"

# Walk the registry again, capturing which CTDL property names each organization publishes rather
# than the identifiers a join needs. A second capture and not two more columns on the first one:
# written per organization the property names add about 8.5 MB to a 7.9 MB file, and aggregated to
# distinct property sets they are 245 KiB. Serves from the same page cache, so a rerun after
# `registry-fetch` costs no network at all.
registry-properties:
	$(PYTHON) -m disclosed.cli registry-properties --out data/registry/properties.json --cache-dir .cache/registry

# Reduce the census to the rates docs/adr/0009 is argued from. Offline: the census is committed.
registry-property-report:
	$(PYTHON) -m disclosed.cli registry-property-report --census data/registry/properties.json --out data/registry-properties.json

# Same contract as `registry-replay`. `tests/test_registry_properties.py` is the pytest form and
# is what gates `make verify`; this is what you run to see the diff when it fails.
registry-property-replay:
	$(PYTHON) -m disclosed.cli registry-property-report --census data/registry/properties.json --out /tmp/registry-properties.json
	diff -u data/registry-properties.json /tmp/registry-properties.json && echo "data/registry-properties.json replays exactly"

# The one target in this file that touches a page this project did not publish, and the only one
# that cannot be replayed. It visits the addresses the federal record declares, obeys robots.txt,
# makes one request per host at a time with 1.5s between them, reads at most 64 KiB per response,
# asks a second time before writing down anything other than a document, and stops dead at its
# request budget. docs/adr/0011 records the decision and the conditions; CAPTURED_ON is required
# rather than defaulted to today, because the date is part of the claim and a date read off the
# clock is a date nobody chose.
reachability-capture:
	$(PYTHON) -m disclosed.cli reachability-capture --cache data/HD2023.zip --characteristics data/IC2023.zip --cohort-source data/sample.json --cohort "$(COHORT)" --captured-on $(CAPTURED_ON) --budget $(or $(BUDGET),2200) --out data/reachability/$(CAPTURED_ON).json

# Reduce the committed capture to the two numbers the site prints. Offline and keyless, the same
# contract `national` and `census-report` hold: the denominator comes from the graded national
# artifact, so a capture that covered a cohort cannot shrink the population it is measured against.
reachability-report:
	$(PYTHON) -m disclosed.cli reachability-report --capture data/reachability/$(CAPTURED_ON).json --national data/national.json --out data/reachability-report.json

# Same contract as `replay`. `tests/test_reachability.py::TestTheCommittedCapture` is the pytest
# form and is what gates `make verify`; this is what you run to see the diff when it fails.
reachability-replay:
	$(PYTHON) -m disclosed.cli reachability-report --capture data/reachability/$(CAPTURED_ON).json --national data/national.json --out /tmp/reachability-report.json
	diff -u data/reachability-report.json /tmp/reachability-report.json && echo "data/reachability-report.json replays exactly"

# The three-year IPEDS history the systemic threshold is argued from. Same contract as `replay`.
ipeds-snapshots:
	@for year in 2021 2022 2023; do \
		$(PYTHON) -m disclosed.cli crosscheck --year $$year \
			--cache data/HD$$year.zip --characteristics data/IC$$year.zip \
			--out /tmp/crosscheck-$$year.json >/dev/null || exit 1; \
		$(PYTHON) -m disclosed.cli snapshot --report /tmp/crosscheck-$$year.json \
			--taken $$year --out /tmp/ipeds-$$year.json >/dev/null || exit 1; \
		diff -u data/snapshots/ipeds/$$year.json /tmp/ipeds-$$year.json || exit 1; \
		echo "data/snapshots/ipeds/$$year.json replays exactly"; \
	done
