PYTHON ?= .venv/bin/python

.PHONY: verify lint typecheck test grade snapshot

verify: lint typecheck test

lint:
	$(PYTHON) -m ruff check src tests

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing -q

grade:
	$(PYTHON) -m disclosed.cli grade --out data/report.json

snapshot:
	$(PYTHON) -m disclosed.cli snapshot --taken $(TAKEN) --out data/snapshots/$(TAKEN).json
