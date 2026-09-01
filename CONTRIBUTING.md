# Contributing

LifeOS accepts connector, ontology, compiler, review, projection, migration, and insight improvements that preserve the sovereignty boundary.

## Rules

1. Use synthetic fixtures only. Never commit private wiki pages, captures, migration output, credentials, or provider sessions.
2. Connectors stop at `CaptureEvent`. They do not pick canonical subjects, folders, or facts.
3. New canonical predicates need domain, range, object kind, temporal behavior, cardinality, and tests.
4. New root types require an architecture discussion. Prefer a kind, role, relation, artifact, or workflow state when that is what the concept actually is.
5. Agents and MCP tools may propose but may not promote canon.
6. Every derived edge or insight must retain input provenance.
7. Add positive and negative semantic tests, not only happy-path serialization tests.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
pytest -q
```
