PYTHON ?= .venv/bin/python

.PHONY: verify lint typecheck test grade site dataset crosscheck national snapshot

verify: lint typecheck test

lint:
	$(PYTHON) -m ruff check src tests

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
	$(PYTHON) -m disclosed.cli snapshot --taken $(TAKEN) --out data/snapshots/$(TAKEN).json
