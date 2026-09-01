# Contributing

Add a connector by implementing `lifeos.connector/v1` and registering it in
`src/lifeos/connectors/__init__.py`. Do not fork core for a new provider.
Run `python3 -m pytest -q` before opening a PR.
Keep fixtures synthetic. No personal data.
