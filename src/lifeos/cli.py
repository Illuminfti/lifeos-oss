from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from lifeos import __version__
from lifeos.autowiki import AutoWiki, AutoWikiWorker, ProposalStore
from lifeos.connectors import registered_connector_ids
from lifeos.connectors.base import ConnectorContext, ConnectorManager, load
from lifeos.ingest import IngestQueue
from lifeos.mcp_server import MCPApplication, serve as serve_mcp, tool_names
from lifeos.retrieval import GBrainAdapter, LifeOSIntelligenceKernel, PgGraphAdapter, QueryService
from lifeos.webhook_server import serve as serve_webhooks
from lifeos.wiki import init_brain


def _emit(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _load_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    if value.startswith("@"):
        raw = Path(value[1:]).expanduser().read_text(encoding="utf-8")
    else:
        raw = value
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("configuration must be a JSON object")
    return parsed


def _brain(args: argparse.Namespace) -> Path:
    return init_brain(Path(args.brain).expanduser())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lifeos", description="Open-source capture-to-canon world model")
    parser.add_argument("--brain", default="./brain", help="LifeOS Markdown brain directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="create the canonical Markdown tree")
    init.add_argument("path", nargs="?")
    sub.add_parser("version")
    sub.add_parser("doctor")

    connector = sub.add_parser("connector", help="manage capture plugins")
    connector_sub = connector.add_subparsers(dest="connector_cmd", required=True)
    connector_sub.add_parser("list")
    describe = connector_sub.add_parser("describe")
    describe.add_argument("id")
    connect = connector_sub.add_parser("connect")
    connect.add_argument("id")
    connect.add_argument("--secret-ref")
    connect.add_argument("--config", help="JSON object or @path")
    for name in ["backfill", "sync"]:
        command = connector_sub.add_parser(name)
        command.add_argument("id")
        command.add_argument("--options", help="JSON object or @path")
    health = connector_sub.add_parser("health")
    health.add_argument("id", nargs="?")
    revoke = connector_sub.add_parser("revoke")
    revoke.add_argument("id")
    purge = connector_sub.add_parser("purge")
    purge.add_argument("id")
    fixture = connector_sub.add_parser("fixture")
    fixture.add_argument("id")

    ingest = sub.add_parser("ingest", help="process durable capture events")
    ingest_sub = ingest.add_subparsers(dest="ingest_cmd", required=True)
    work = ingest_sub.add_parser("work")
    work.add_argument("--limit", type=int, default=20)

    staging = sub.add_parser("staging", help="review auto-wiki proposals")
    staging_sub = staging.add_subparsers(dest="staging_cmd", required=True)
    listing = staging_sub.add_parser("list")
    listing.add_argument("--status")
    show = staging_sub.add_parser("show")
    show.add_argument("proposal_id")
    promote = staging_sub.add_parser("promote")
    promote.add_argument("proposal_id")
    promote.add_argument("--owner", required=True)
    promote.add_argument("--confirm", action="store_true")
    promote.add_argument("--summary")
    promote.add_argument("--aliases", help="JSON array")
    reject = staging_sub.add_parser("reject")
    reject.add_argument("proposal_id")
    reject.add_argument("--owner", required=True)
    reject.add_argument("--reason", default="")
    reverse = staging_sub.add_parser("reverse")
    reverse.add_argument("receipt_path")
    reverse.add_argument("--owner", required=True)
    reverse.add_argument("--confirm", action="store_true")

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--limit", type=int, default=10)
    get = sub.add_parser("get")
    get.add_argument("path_or_entity")
    get.add_argument("--entity", action="store_true")
    context = sub.add_parser("context")
    context.add_argument("--purpose", required=True)
    context.add_argument("--entity", action="append", default=[])
    context.add_argument("--previous-digest")
    context.add_argument("--max-tokens", type=int, default=800)

    gbrain = sub.add_parser("gbrain")
    gbrain_sub = gbrain.add_subparsers(dest="gbrain_cmd", required=True)
    gbrain_sub.add_parser("sync")
    graph = sub.add_parser("graph")
    graph_sub = graph.add_subparsers(dest="graph_cmd", required=True)
    graph_sub.add_parser("rebuild")

    mcp = sub.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser("tools")
    mcp_sub.add_parser("serve")

    webhook = sub.add_parser("webhook")
    webhook_sub = webhook.add_subparsers(dest="webhook_cmd", required=True)
    webhook_serve = webhook_sub.add_parser("serve")
    webhook_serve.add_argument("--host", default="127.0.0.1")
    webhook_serve.add_argument("--port", type=int, default=4789)

    # v0.1 compatibility aliases.
    sub.add_parser("connector-list")
    old_describe = sub.add_parser("connector-describe")
    old_describe.add_argument("id")
    old_fixture = sub.add_parser("connector-fixture")
    old_fixture.add_argument("id")
    old_fixture.add_argument("--brain", dest="alias_brain")
    sub.add_parser("mcp-serve")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "version":
            print(__version__)
            return 0
        if args.cmd == "init":
            root = init_brain(Path(args.path or args.brain).expanduser())
            _emit({"ok": True, "brain": str(root)})
            return 0

        if args.cmd == "connector-list":
            args.cmd, args.connector_cmd = "connector", "list"
        elif args.cmd == "connector-describe":
            args.cmd, args.connector_cmd = "connector", "describe"
        elif args.cmd == "connector-fixture":
            if args.alias_brain:
                args.brain = args.alias_brain
            args.cmd, args.connector_cmd = "connector", "fixture"
        elif args.cmd == "mcp-serve":
            args.cmd, args.mcp_cmd = "mcp", "serve"

        brain = _brain(args)
        manager = ConnectorManager(brain)

        if args.cmd == "doctor":
            application = MCPApplication(brain, connectors=manager)
            report = application.system_health()
            report["version"] = __version__
            report["registered_connectors"] = registered_connector_ids()
            report["mcp_tools"] = tool_names()
            _emit(report)
            return 0

        if args.cmd == "connector":
            if args.connector_cmd == "list":
                _emit(
                    {
                        "connectors": [
                            {"key": key, **load(key, manager.context).describe().to_dict()}
                            for key in registered_connector_ids()
                        ]
                    }
                )
                return 0
            if args.connector_cmd == "describe":
                _emit(load(args.id, manager.context).describe())
                return 0
            if args.connector_cmd == "connect":
                receipt = manager.connect(
                    args.id,
                    {"secret_ref": args.secret_ref, "config": _load_object(args.config)},
                )
                _emit(receipt)
                return 0 if receipt.ok else 2
            if args.connector_cmd in {"backfill", "sync"}:
                _emit(manager.run(args.id, args.connector_cmd, _load_object(args.options)))
                return 0
            if args.connector_cmd == "health":
                if args.id:
                    _emit(manager.health(args.id))
                else:
                    _emit(
                        {
                            "connectors": {
                                row["connector_key"]: manager.health(str(row["connector_key"])).to_dict()
                                for row in manager.queue.list_connections()
                            }
                        }
                    )
                return 0
            if args.connector_cmd == "revoke":
                _emit(manager.revoke(args.id))
                return 0
            if args.connector_cmd == "purge":
                _emit(manager.purge(args.id))
                return 0
            if args.connector_cmd == "fixture":
                plugin = load(args.id, ConnectorContext(brain=brain, queue=manager.queue))
                batch = plugin.fixture_batch()
                stored, duplicates = manager.queue.accept_batch(batch.events)
                _emit({"ok": True, "stored": stored, "duplicates": duplicates, "events": len(batch.events)})
                return 0

        if args.cmd == "ingest" and args.ingest_cmd == "work":
            _emit(AutoWikiWorker(manager.queue, AutoWiki(brain)).work(limit=args.limit))
            return 0

        if args.cmd == "staging":
            store = ProposalStore(brain)
            if args.staging_cmd == "list":
                _emit({"proposals": [proposal.to_dict() for proposal in store.list(args.status)]})
                return 0
            if args.staging_cmd == "show":
                _emit(store.get(args.proposal_id))
                return 0
            if args.staging_cmd == "reject":
                _emit(store.reject(args.proposal_id, args.owner, args.reason))
                return 0
            if args.staging_cmd == "reverse":
                _emit(store.reverse(args.receipt_path, owner=args.owner, confirm=args.confirm))
                return 0
            if args.staging_cmd == "promote":
                aliases = None
                if args.aliases:
                    parsed = json.loads(args.aliases)
                    if not isinstance(parsed, list):
                        raise ValueError("--aliases must be a JSON array")
                    aliases = [str(value) for value in parsed]
                result = store.promote(
                    args.proposal_id,
                    owner=args.owner,
                    confirm=args.confirm,
                    edited_summary=args.summary,
                    aliases=aliases,
                )
                derived: dict[str, Any] = {}
                degraded: list[str] = []
                try:
                    derived["gbrain"] = GBrainAdapter(brain).sync()
                except Exception as exc:
                    degraded.append(f"gbrain:{type(exc).__name__}")
                try:
                    derived["pggraph"] = PgGraphAdapter(brain).rebuild()
                except Exception as exc:
                    degraded.append(f"pggraph:{type(exc).__name__}")
                result.update({"canon_valid": True, "derived": derived, "degraded": degraded})
                _emit(result)
                return 0

        query_service = QueryService(brain)
        if args.cmd == "search":
            _emit(query_service.search(args.query, limit=args.limit))
            return 0
        if args.cmd == "query":
            _emit(query_service.query(args.question, limit=args.limit))
            return 0
        if args.cmd == "get":
            _emit(
                query_service.get_entity(args.path_or_entity)
                if args.entity
                else query_service.get_page(args.path_or_entity)
            )
            return 0
        if args.cmd == "context":
            _emit(
                LifeOSIntelligenceKernel(brain, query_service=query_service).context(
                    purpose=args.purpose,
                    entities=args.entity,
                    previous_digest=args.previous_digest,
                    max_tokens=args.max_tokens,
                )
            )
            return 0
        if args.cmd == "gbrain" and args.gbrain_cmd == "sync":
            _emit(GBrainAdapter(brain).sync())
            return 0
        if args.cmd == "graph" and args.graph_cmd == "rebuild":
            _emit(PgGraphAdapter(brain).rebuild())
            return 0
        if args.cmd == "mcp":
            if args.mcp_cmd == "tools":
                _emit({"tools": tool_names()})
                return 0
            serve_mcp(brain)
            return 0
        if args.cmd == "webhook" and args.webhook_cmd == "serve":
            serve_webhooks(brain, args.host, args.port)
            return 0
        parser.error("unhandled command")
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
