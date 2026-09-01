from __future__ import annotations

from pathlib import Path

from lifeos.connectors.base import ConnectorRegistry
from lifeos.connectors.example import ExampleConnector
from lifeos.runtime import LifeOSRuntime


def registry():
    return ConnectorRegistry.from_connectors({"example": ExampleConnector()})


def test_runtime_capture_to_canon_and_purge_review(brain):
    with LifeOSRuntime(brain, registry=registry()) as runtime:
        connection = runtime.connect("example", {"fixture_count": 2})
        result = runtime.run_connector(connection.connection_id, stream="backfill")
        assert result.ingest.accepted == 2
        assert result.processed == {"leased": 2, "processed": 2, "failed": 0, "dead": 0}
        proposals = runtime.store.list_proposals()
        assert len(proposals) == 1
        assert len(proposals[0].evidence_event_ids) == 2
        receipt = runtime.autowiki.promote(proposals[0].proposal_id, reviewer="owner")
        target = brain.root / receipt.target_path
        assert target.is_file()
        purge = runtime.purge(connection.connection_id)
        assert purge["canon_deleted"] is False
        assert target.is_file()
        assert (brain.root / purge["canon_review"]).is_file()
        assert runtime.store.stats()["events"] == 0


def test_revoke_deletes_credentials_but_preserves_evidence(brain):
    class SecretExample(ExampleConnector):
        def connect(self, request, context):
            result = super().connect(request, context)
            from lifeos.contracts import ConnectResult

            return ConnectResult(
                connection_id=result.connection_id,
                settings=result.settings,
                granted_scopes=result.granted_scopes,
                secret_payload={"token": "secret"},
            )

    reg = ConnectorRegistry.from_connectors({"example": SecretExample()})
    with LifeOSRuntime(brain, registry=reg) as runtime:
        connection = runtime.connect("example", {})
        assert connection.secret_ref
        runtime.run_connector(connection.connection_id, stream="backfill", process=False)
        assert runtime.store.stats()["events"] == 1
        result = runtime.revoke(connection.connection_id)
        assert result["credentials_deleted"] is True
        assert result["evidence_untouched"] is True
        assert runtime.store.stats()["events"] == 1


def test_replayed_connector_batch_is_idempotent(brain):
    with LifeOSRuntime(brain, registry=registry()) as runtime:
        connection = runtime.connect("example", {})
        first = runtime.run_connector(connection.connection_id, stream="backfill", process=False)
        runtime.store.put_checkpoint(connection.connection_id, "backfill", {})
        second = runtime.run_connector(connection.connection_id, stream="backfill", process=False)
        assert first.ingest.accepted == 1
        assert second.ingest.accepted == 0
        assert second.ingest.duplicates == 1
        assert runtime.store.stats()["events"] == 1
