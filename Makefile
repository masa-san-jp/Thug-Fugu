.PHONY: verify verify-fast

PYTHON ?= python3

verify:
	$(PYTHON) scripts/verify.py

verify-fast:
	$(PYTHON) scripts/verify.py --fast
