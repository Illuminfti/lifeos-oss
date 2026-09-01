# Implementation map and acceptance bar

This document maps the architecture audit to the public implementation.

## A. Noise-to-canon audit

Implemented in documentation and encoded as invariants across `evidence.py`, `compiler.py`, `reduce.py`, `review.py`, and `promote.py`.

## B. Ontology

- Machine registry: `src/lifeos/resources/ontology.yaml`
- Predicate registry: `src/lifeos/resources/predicates.yaml`
- Runtime validator: `src/lifeos/ontology.py`
- Spawn rules for all eleven core types: `src/lifeos/spawn.py`
- Reversible merge implementation: `src/lifeos/promote.py`

## C. Filter and spawn machinery

- Conservative deterministic disposition: `extract.NoiseFilter`
- Extractor protocol and reference typed extractor: `extract.py`
- Scoped identity and abstain states: `resolve.py`
- Candidate evidence and identifiers: `evidence.py`
- Atomic claim reduction and contradictions: `reduce.py`
- Subject packets and backpressure: `review.py`
- Explicit owner transaction: `promote.py`

## D. Insight layer

Implemented in `insights.py` with relationship radar, self-pattern association, life-function coverage, circumstance changes, decision/outcome ledger, and leverage map. Every record carries input IDs, revision hash, confidence, and limitations.

## E. Concrete schema and layout

- Capture, claim, page, and packet JSON Schemas: `src/lifeos/resources/json-schema/`
- Canonical page parser/renderer: `canon.py`
- Brain tree: `wiki.py`
- Operational evidence database: `evidence.py`
- Disposable canonical projection: `projection.py`
- Graph JSONL export with claim IDs: `projection.py`
- Local migration scanner/planner: `migration.py`

## F. Fourteen-day cut translated into engineering slices

The implementation is structured so each slice remains independently testable:

1. ontology and schemas,
2. capture v2 and metadata preservation,
3. replay queue,
4. typed extraction contract,
5. identity resolution,
6. all-type spawn registry,
7. claim reducer and conflicts,
8. packetizer and backpressure,
9. canonical transaction engine,
10. CLI review control plane,
11. projections and graph export,
12. insight views,
13. migration tooling,
14. replay, privacy, and semantic tests.

## Current acceptance tests

The synthetic suite verifies:

- complete event metadata survives ingestion,
- account scope and revision collisions are enforced,
- jobs are leaseable, retryable, and replay-safe,
- family, relationship, and dashboard are not root world-object types,
- every major type has positive and negative spawn coverage,
- name-only identity remains ambiguous,
- repeated claims collapse into one proposal and one packet operation,
- exclusive overlapping values raise a conflict,
- owner confirmation is mandatory,
- promotion creates claim-level provenance rather than page-level confidence,
- graph edges retain canonical claim lineage,
- canonical merges preserve a redirect page,
- migration reports redact private content,
- self-pattern output preserves observation provenance and avoids causal language,
- MCP exposes no promotion tool.

## Honest remaining work

The following are deliberately not faked:

- live provider API clients and authorization flows,
- a bundled proprietary LLM extractor,
- production episode segmentation for every modality,
- a polished graphical review client,
- distributed multi-process promotion locking,
- calibrated thresholds from a real owner-labelled corpus,
- advanced lagged/self-pattern statistics.

These are extension work on top of the implemented boundaries, not reasons to revert to heuristic page routing.
