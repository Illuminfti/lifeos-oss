"""LifeOS command-line interface."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets as secretlib
import sys
from typing import Any, Mapping

from lifeos import __version__
from lifeos.config import atomic_write_text
from lifeos.errors import LifeOSError
from lifeos.mcp_server import run_mcp
from lifeos.runtime import LifeOSRuntime
from lifeos.webhook import serve_webhooks
from lifeos.wiki import init_brain, read_page


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def _read_json_file(path: str | None, *, private: bool = False) -> dict[str, Any]:
    if not path:
        return {}
    file = Path(path).expanduser()
    if private and os.name == "posix":
        mode = file.stat().st_mode & 0o777
        if mode & 0o077:
            raise ValueError(f"secret file {file} must be mode 0600")
    value = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{file} must contain a JSON object")
    return value


def _brain(args: argparse.Namespace) -> str:
    return str(getattr(args, "brain", None) or os.environ.get("LIFEOS_BRAIN") or "./brain")


def _print(value: Any, *, json_mode: bool = False) -> None:
    if json_mode or isinstance(value, (dict, list, tuple)):
        print(_json(value))
    else:
        print(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lifeos", description="Open-source capture-to-canon world model")
    parser.add_argument("--brain", default=os.environ.get("LIFEOS_BRAIN", "./brain"), help="LifeOS brain directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a canonical Markdown brain")
    init.add_argument("path", nargs="?")
    sub.add_parser("version")
    sub.add_parser("doctor")

    secret = sub.add_parser("secret", help="create a private setup secret file")
    secret_sub = secret.add_subparsers(dest="secret_command", required=True)
    generate = secret_sub.add_parser("generate")
    generate.add_argument("path")
    generate.add_argument("--field", default="ingest_token")

    connector = sub.add_parser("connector", help="manage capture plugins and connections")
    connector_sub = connector.add_subparsers(dest="connector_command", required=True)
    list_cmd = connector_sub.add_parser("list")
    list_cmd.add_argument("--connections", action="store_true")
    describe = connector_sub.add_parser("describe")
    describe.add_argument("name")
    connect = connector_sub.add_parser("connect")
    connect.add_argument("name")
    connect.add_argument("--settings-file")
    connect.add_argument("--secret-file")
    connect.add_argument("--path")
    connect.add_argument("--authorize", action="store_true")
    for name in ("backfill", "sync", "health", "pause", "resume", "revoke", "purge"):
        command = connector_sub.add_parser(name)
        command.add_argument("connection_id")
        if name in {"backfill", "sync"}:
            command.add_argument("--no-process", action="store_true")
            command.add_argument("--process-limit", type=int, default=1000)
    test = connector_sub.add_parser("test")
    test.add_argument("name")

    process = sub.add_parser("process", help="drain queued events through auto-wiki")
    process.add_argument("--limit", type=int, default=100)

    staging = sub.add_parser("staging", help="review and promote proposals")
    staging_sub = staging.add_subparsers(dest="staging_command", required=True)
    stage_list = staging_sub.add_parser("list")
    stage_list.add_argument("--status", default="awaiting_review")
    stage_list.add_argument("--limit", type=int, default=100)
    stage_show = staging_sub.add_parser("show")
    stage_show.add_argument("proposal_id")
    promote = staging_sub.add_parser("promote")
    promote.add_argument("proposal_id")
    promote.add_argument("--reviewer", default=os.environ.get("USER", "local-owner"))
    reject = staging_sub.add_parser("reject")
    reject.add_argument("proposal_id")
    reject.add_argument("--reviewer", default=os.environ.get("USER", "local-owner"))
    reject.add_argument("--reason", default="")

    page = sub.add_parser("page", help="read canonical Markdown")
    page_sub = page.add_subparsers(dest="page_command", required=True)
    page_get = page_sub.add_parser("get")
    page_get.add_argument("path")

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--limit", type=int, default=10)
    sync_brain = sub.add_parser("gbrain-sync")
    sync_brain.add_argument("--embed", action="store_true")

    context = sub.add_parser("context")
    context.add_argument("purpose")
    context.add_argument("--subject", action="append", default=[])
    context.add_argument("--known-digest")

    mcp = sub.add_parser("mcp-serve")
    mcp.add_argument("--profile", choices=("read", "staging"), default="read")
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8787)

    webhook = sub.add_parser("webhook-serve")
    webhook.add_argument("--host", default="127.0.0.1")
    webhook.add_argument("--port", type=int, default=8765)

    # Compatibility aliases from the initial public skeleton.
    sub.add_parser("connector-list")
    legacy_describe = sub.add_parser("connector-describe")
    legacy_describe.add_argument("name")
    legacy_fixture = sub.add_parser("connector-fixture")
    legacy_fixture.add_argument("name")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_mode = bool(args.json)
    try:
        if args.command == "version":
            _print(__version__, json_mode=json_mode)
            return 0
        if args.command == "init":
            path = Path(args.path or _brain(args))
            config = init_brain(path)
            # Opening once commissions the private state DB and secret store.
            with LifeOSRuntime(config) as runtime:
                result = runtime.doctor()
            _print({"brain": str(config.root), "initialized": True, "doctor": result}, json_mode=True)
            return 0
        if args.command == "secret":
            if args.secret_command == "generate":
                path = Path(args.path).expanduser()
                payload = {str(args.field): secretlib.token_urlsafe(32)}
                atomic_write_text(path, _json(payload) + "\n", mode=0o600)
                _print({"created": str(path), "mode": "0600", "field": args.field}, json_mode=True)
                return 0

        with LifeOSRuntime.open(_brain(args)) as runtime:
            if args.command == "doctor":
                _print(runtime.doctor(), json_mode=True)
                return 0

            if args.command in {"connector-list", "connector-describe", "connector-fixture"}:
                if args.command == "connector-list":
                    value = [registration.connector.manifest.to_dict() | {"name": registration.name, "distribution": registration.distribution} for registration in runtime.registry.registrations()]
                elif args.command == "connector-describe":
                    value = runtime.registry.get(args.name).manifest.to_dict()
                else:
                    value = runtime.registry.get(args.name).test_fixture(runtime.context)
                _print(value, json_mode=True)
                return 0

            if args.command == "connector":
                action = args.connector_command
                if action == "list":
                    plugins = [registration.connector.manifest.to_dict() | {"name": registration.name, "distribution": registration.distribution} for registration in runtime.registry.registrations()]
                    value: Any = {"plugins": plugins}
                    if args.connections:
                        value["connections"] = [
                            {
                                "name": name,
                                "connection_id": connection.connection_id,
                                "connector_id": connection.connector_id,
                                "status": connection.status,
                                "scopes": list(connection.granted_scopes),
                            }
                            for name, connection in runtime.store.list_connections(include_revoked=True)
                        ]
                    _print(value, json_mode=True)
                    return 0
                if action == "describe":
                    registration = next(
                        (item for item in runtime.registry.registrations() if item.name == args.name),
                        None,
                    )
                    if registration is None:
                        runtime.registry.get(args.name)  # raises a typed unknown-connector error
                        raise AssertionError("registry lookup returned without a registration")
                    _print(
                        registration.connector.manifest.to_dict()
                        | {"name": registration.name, "distribution": registration.distribution},
                        json_mode=True,
                    )
                    return 0
                if action == "connect":
                    request = _read_json_file(args.settings_file)
                    secret = _read_json_file(args.secret_file, private=True)
                    if secret:
                        request["secret"] = secret
                    if args.path:
                        request["path"] = args.path
                    if args.authorize:
                        request["authorize"] = True
                    connection = runtime.connect(args.name, request)
                    connector = runtime.registry.get(args.name)
                    value = {
                        "connection_id": connection.connection_id,
                        "connector": args.name,
                        "connector_id": connection.connector_id,
                        "status": connection.status,
                        "scopes": list(connection.granted_scopes),
                        "secret_custody": "owner-only local file; mode 0600; not encrypted by LifeOS",
                        "webhook_path": f"/v1/webhooks/{connection.connection_id}" if "webhooks" in connector.manifest.capabilities else None,
                    }
                    _print(value, json_mode=True)
                    return 0
                if action in {"backfill", "sync"}:
                    value = runtime.run_connector(
                        args.connection_id,
                        stream=action,
                        process=not args.no_process,
                        process_limit=args.process_limit,
                    ).to_dict()
                    _print(value, json_mode=True)
                    return 0
                if action == "health":
                    _print(runtime.health(args.connection_id).to_dict(), json_mode=True)
                    return 0
                if action == "pause":
                    runtime.pause(args.connection_id)
                    _print({"connection_id": args.connection_id, "status": "paused"}, json_mode=True)
                    return 0
                if action == "resume":
                    runtime.resume(args.connection_id)
                    _print({"connection_id": args.connection_id, "status": "connected"}, json_mode=True)
                    return 0
                if action == "revoke":
                    _print(runtime.revoke(args.connection_id), json_mode=True)
                    return 0
                if action == "purge":
                    _print(runtime.purge(args.connection_id), json_mode=True)
                    return 0
                if action == "test":
                    _print(runtime.registry.get(args.name).test_fixture(runtime.context), json_mode=True)
                    return 0

            if args.command == "process":
                _print(runtime.process(limit=args.limit), json_mode=True)
                return 0
            if args.command == "staging":
                if args.staging_command == "list":
                    status = None if args.status == "all" else args.status
                    _print([proposal.to_dict() for proposal in runtime.store.list_proposals(status=status, limit=args.limit)], json_mode=True)
                    return 0
                if args.staging_command == "show":
                    proposal, payload = runtime.store.get_proposal(args.proposal_id)
                    _print({"proposal": proposal.to_dict(), "payload": {key: value for key, value in payload.items() if key != "provider_ref_private"}}, json_mode=True)
                    return 0
                if args.staging_command == "promote":
                    receipt = runtime.autowiki.promote(args.proposal_id, reviewer=args.reviewer)
                    sync = runtime.gbrain.sync(embed=False) if runtime.gbrain.available() else {"skipped": "GBrain unavailable"}
                    graph = runtime.pggraph.rebuild()
                    _print({"receipt": receipt.to_dict(), "gbrain": sync, "pggraph": graph}, json_mode=True)
                    return 0
                if args.staging_command == "reject":
                    archive = runtime.autowiki.reject(args.proposal_id, reviewer=args.reviewer, reason=args.reason)
                    _print({"proposal_id": args.proposal_id, "status": "rejected", "archive": str(archive)}, json_mode=True)
                    return 0
            if args.command == "page":
                _print({"path": args.path, "content": read_page(runtime.config, args.path)}, json_mode=True)
                return 0
            if args.command == "search":
                _print(runtime.gbrain.search(args.query, limit=args.limit), json_mode=True)
                return 0
            if args.command == "query":
                _print(runtime.gbrain.query(args.question, limit=args.limit), json_mode=True)
                return 0
            if args.command == "gbrain-sync":
                _print(runtime.gbrain.sync(embed=args.embed), json_mode=True)
                return 0
            if args.command == "context":
                packet = runtime.kernel.turn_context(purpose=args.purpose, subjects=tuple(args.subject), known_digest=args.known_digest)
                _print(packet.to_dict(), json_mode=True)
                return 0
            if args.command == "mcp-serve":
                run_mcp(runtime, profile=args.profile, transport=args.transport, host=args.host, port=args.port)
                return 0
            if args.command == "webhook-serve":
                serve_webhooks(runtime.registry, runtime.context, host=args.host, port=args.port)
                return 0

    except (LifeOSError, ValueError, FileNotFoundError, RuntimeError, json.JSONDecodeError) as exc:
        if json_mode:
            print(_json({"ok": False, "error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        else:
            print(f"lifeos: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
