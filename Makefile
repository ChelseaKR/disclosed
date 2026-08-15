PYTHON ?= .venv/bin/python

.PHONY: verify lint typecheck test grade site dataset crosscheck national snapshot replay \
        ipeds-snapshots

verify: lint typecheck test

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q

grade:
	$(PYTHON) -m disclosed.cli grade --out data/report.json

site:
	$(PYTHON) -m disclosed.cli site --report data/report.json --national data/national.json --out site --generated $(shell date -u +%F)

dataset:
	$(PYTHON) -m disclosed.cli dataset --report data/report.json --out data/dataset.csv

crosscheck:
	$(PYTHON) -m disclosed.cli crosscheck --cache data/HD2023.zip --characteristics data/IC2023.zip --source data/sample.json --out data/crosscheck.json

national:
	$(PYTHON) -m disclosed.cli national --report data/crosscheck.json --out data/national.json

snapshot:
	$(PYTHON) -m disclosed.cli snapshot --taken $(TAKEN) --out data/snapshots/scorecard/$(TAKEN).json

# Regenerate the committed national artifact from the committed archives and show what moved. The
# test suite asserts these are identical on every push; this is the target you run when it says
# they are not, because a diff is more use than an assertion failure. No network and no key: both
# IPEDS archives are in data/.
replay: crosscheck
	$(PYTHON) -m disclosed.cli national --report data/crosscheck.json --out /tmp/national.json
	diff -u data/national.json /tmp/national.json && echo "data/national.json replays exactly"

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
