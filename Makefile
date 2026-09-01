.PHONY: install test verify build smoke

install:
	python -m pip install -e '.[dev,telegram]'

test:
	pytest

verify: test
	python -m compileall -q src
	python scripts/scan_public.py

build: verify
	python -m build

smoke:
	rm -rf /tmp/lifeos-smoke
	lifeos --brain /tmp/lifeos-smoke init
	lifeos --brain /tmp/lifeos-smoke doctor
