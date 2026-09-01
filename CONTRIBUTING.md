# Contributing

LifeOS accepts connector plugins, core hardening, retrieval integration, tests,
and documentation.

Before opening a pull request:

```bash
python -m pip install -e '.[dev,telegram]'
pytest
python -m compileall -q src
python scripts/scan_public.py
python -m build
```

Authority changes require an architecture note. A connector must use the common
contract and must not add provider branches to core. Keep fixtures synthetic or
irreversibly redacted. Never commit a private wiki, credentials, sessions,
absolute owner paths, raw account exports, or operator logs.
