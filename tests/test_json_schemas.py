from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from lifeos.connectors.example import ExampleConnector
from lifeos.contracts import Actor, CaptureEvent, Connection
from lifeos.kernel import LifeOSIntelligenceKernel


ROOT = Path(__file__).parents[1]


def schema(name: str):
    return json.loads((ROOT / "schemas" / name).read_text())


def test_connector_manifests_validate():
    from lifeos.connectors.base import ConnectorRegistry

    validator = jsonschema.Draft202012Validator(schema("connector-manifest.v1.json"))
    for registration in ConnectorRegistry.discover().registrations():
        validator.validate(registration.connector.manifest.to_dict())


def test_capture_event_validates():
    event = CaptureEvent.create(
        connector_id="org.lifeos.example",
        connection_id="con_test",
        source_record_id="one",
        source_revision="1",
        kind="message",
        occurred_at="2026-09-01T00:00:00Z",
        actors=(Actor(provider_ref="example:ada", display_name="Ada"),),
        text="hello",
    )
    jsonschema.Draft202012Validator(schema("capture-event.v1.json")).validate(event.to_dict())


def test_wheel_schema_copies_match_repository_contracts():
    from lifeos.schema import SCHEMAS, load_schema

    for name in SCHEMAS:
        assert load_schema(name) == schema(name)
