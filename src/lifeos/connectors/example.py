from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.contracts import ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.example",
            display_name="Example connector",
            source_classes=["fixture"],
            capabilities=["backfill", "incremental_sync", "revoke", "purge"],
            auth_modes=["none"],
            notes="Synthetic conformance connector.",
        )

    def connect(self, request):
        return ConnectionReceipt(ok=True, connection_id=self._connection_id(request, "example"), state="healthy", provider_identity={"synthetic": True})

    def backfill(self, request):
        return self.fixture_batch()

    def sync(self, request):
        return SyncBatch(checkpoint={"fixture": 1})

    def health(self, request=None):
        return HealthReport(state="healthy")
