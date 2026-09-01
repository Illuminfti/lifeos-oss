from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from lifeos import __version__
from lifeos.compiler import SemanticCompiler
from lifeos.connectors import REGISTRY
from lifeos.connectors.base import load, load_all
from lifeos.contracts import CaptureEvent
from lifeos.evidence import EvidenceStore
from lifeos.insights import InsightEngine
from lifeos.mcp_server import serve, tool_names
from lifeos.migration import LegacyVaultScanner, MigrationPlanner
from lifeos.ontology import Ontology
from lifeos.projection import ProjectionBuilder
from lifeos.promote import PromotionService
from lifeos.canon import CanonicalVault
from lifeos.spawn import SpawnPolicyRegistry
from lifeos.wiki import init_brain


def _brain_path(value: str) -> Path:
    return Path(value).resolve()


def _store(brain: Path) -> EvidenceStore:
    return EvidenceStore(brain / ".lifeos" / "evidence.sqlite")


def _add_brain(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brain", default="./brain")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lifeos")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create a private v2 brain directory")
    p_init.add_argument("path")

    sub.add_parser("connector-list")
    p_desc = sub.add_parser("connector-describe")
    p_desc.add_argument("id")

    p_fix = sub.add_parser("connector-fixture", help="ingest a connector's synthetic fixture")
    p_fix.add_argument("id")
    _add_brain(p_fix)

    p_demo = sub.add_parser("demo-compile", help="run a synthetic end-to-end semantic fixture")
    _add_brain(p_demo)

    p_review = sub.add_parser("review-list")
    _add_brain(p_review)
    p_review.add_argument("--limit", type=int, default=12)
    p_review.add_argument("--kind")

    p_show = sub.add_parser("review-show")
    _add_brain(p_show)
    p_show.add_argument("packet_id")

    p_promote = sub.add_parser("promote", help="explicit owner promotion transaction")
    _add_brain(p_promote)
    p_promote.add_argument("packet_id")
    p_promote.add_argument("--actor", required=True)
    p_promote.add_argument("--accept-all", action="store_true", required=True)

    p_project = sub.add_parser("project-rebuild")
    _add_brain(p_project)

    p_graph = sub.add_parser("graph-export")
    _add_brain(p_graph)
    p_graph.add_argument("destination")

    p_migrate = sub.add_parser("migrate-scan")
    p_migrate.add_argument("vault")
    p_migrate.add_argument("--output", required=True)

    p_plan = sub.add_parser("migrate-plan")
    p_plan.add_argument("vault")
    p_plan.add_argument("--output", required=True)
    p_plan.add_argument("--include-private-paths", action="store_true")

    p_insight = sub.add_parser("insight")
    _add_brain(p_insight)
    p_insight.add_argument(
        "kind",
        choices=["relationship", "self-pattern", "functions", "changes", "decisions", "leverage"],
    )
    p_insight.add_argument("--subject-id")
    p_insight.add_argument("--metric-a")
    p_insight.add_argument("--metric-b")

    p_stats = sub.add_parser("evidence-stats")
    _add_brain(p_stats)

    p_mcp = sub.add_parser("mcp-serve")
    _add_brain(p_mcp)

    sub.add_parser("doctor")
    sub.add_parser("version")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "init":
        print(str(init_brain(Path(args.path))))
        return 0
    if args.cmd == "connector-list":
        for cid in sorted(REGISTRY):
            manifest = load(cid).describe()
            print(f"{cid}\t{manifest.display_name}\t{manifest.custody}")
        return 0
    if args.cmd == "connector-describe":
        print(json.dumps(load(args.id).describe().to_dict(), indent=2))
        return 0
    if args.cmd == "connector-fixture":
        brain = init_brain(_brain_path(args.brain))
        plugin = load(args.id)
        report = plugin.test_fixture()
        events = plugin.backfill({})
        with _store(brain) as store:
            stored = sum(1 for event in events if store.accept(event))
        print(json.dumps({"fixture": report, "stored": stored, "semantic_packets": 0}))
        return 0
    if args.cmd == "demo-compile":
        brain = init_brain(_brain_path(args.brain))
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        event = CaptureEvent(
            event_id="evt_demo_semantic_v2",
            connector_id="org.lifeos.example",
            connection_id="con_demo",
            source_record_id="demo_1",
            source_revision="1",
            kind="message.created",
            occurred_at=now,
            observed_at=now,
            text="Synthetic example asked for a project follow-up.",
            metadata={
                "synthetic": True,
                "semantic": {
                    "mentions": [
                        {
                            "local_id": "person",
                            "surface_text": "Synthetic Example",
                            "proposed_type": "person",
                            "identifiers": [
                                {"namespace": "example-user", "value": "synthetic-1"}
                            ],
                            "spawn_evidence": {
                                "proposed_type": "person",
                                "display_name": "Synthetic Example",
                                "stable_identifier_count": 1,
                                "independent_evidence_count": 1,
                                "meaningful_interaction": True,
                            },
                        }
                    ],
                    "operations": [
                        {
                            "kind": "create_open_loop",
                            "subject_id": "mention:person",
                            "priority": 0.9,
                            "payload": {
                                "title": "Follow up with Synthetic Example",
                                "kind": "follow_up",
                                "claims": [],
                            },
                        }
                    ],
                },
            },
        )
        with _store(brain) as store:
            stored = store.accept(event)
            packets = SemanticCompiler(store).compile_event(event.event_id)
            print(
                json.dumps(
                    {
                        "stored": stored,
                        "packets": [packet.to_dict() for packet in packets],
                        "stats": store.stats(),
                    },
                    indent=2,
                )
            )
        return 0
    if args.cmd in {"review-list", "review-show", "evidence-stats"}:
        brain = init_brain(_brain_path(args.brain))
        with _store(brain) as store:
            if args.cmd == "review-list":
                packets = store.list_review_packets(
                    limit=args.limit, packet_kind=args.kind
                )
                print(json.dumps([packet.to_dict() for packet in packets], indent=2))
            elif args.cmd == "review-show":
                packet = store.get_review_packet(args.packet_id)
                print(json.dumps(packet.to_dict() if packet else None, indent=2))
            else:
                print(json.dumps(store.stats(), indent=2))
        return 0
    if args.cmd == "promote":
        brain = init_brain(_brain_path(args.brain))
        with _store(brain) as store:
            service = PromotionService(CanonicalVault(brain), store)
            transaction_id = service.promote_packet(
                args.packet_id,
                actor=args.actor,
                owner_confirmed=bool(args.accept_all),
            )
            projection = ProjectionBuilder(CanonicalVault(brain)).rebuild()
        print(json.dumps({"transaction_id": transaction_id, "projection": projection}, indent=2))
        return 0
    if args.cmd == "project-rebuild":
        brain = init_brain(_brain_path(args.brain))
        print(json.dumps(ProjectionBuilder(CanonicalVault(brain)).rebuild(), indent=2))
        return 0
    if args.cmd == "graph-export":
        brain = init_brain(_brain_path(args.brain))
        builder = ProjectionBuilder(CanonicalVault(brain))
        if not builder.index_path.exists():
            builder.rebuild()
        print(json.dumps(builder.export_graph_jsonl(Path(args.destination)), indent=2))
        return 0
    if args.cmd == "migrate-scan":
        scanner = LegacyVaultScanner(Path(args.vault))
        print(str(scanner.write_redacted_report(Path(args.output))))
        return 0
    if args.cmd == "migrate-plan":
        scanner = LegacyVaultScanner(Path(args.vault))
        plan = MigrationPlanner(scanner).plan(include_private_paths=args.include_private_paths)
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(str(destination))
        return 0
    if args.cmd == "insight":
        brain = init_brain(_brain_path(args.brain))
        builder = ProjectionBuilder(CanonicalVault(brain))
        if not builder.index_path.exists():
            builder.rebuild()
        with _store(brain) as store:
            engine = InsightEngine(builder.index_path, store)
            if args.kind == "relationship":
                if not args.subject_id:
                    raise SystemExit("--subject-id is required")
                output = engine.relationship_radar(args.subject_id).to_dict()
            elif args.kind == "self-pattern":
                if not args.metric_a or not args.metric_b:
                    raise SystemExit("--metric-a and --metric-b are required")
                output = engine.self_pattern(args.metric_a, args.metric_b).to_dict()
            elif args.kind == "functions":
                output = [item.to_dict() for item in engine.life_function_coverage()]
            elif args.kind == "changes":
                output = engine.circumstance_changes().to_dict()
            elif args.kind == "decisions":
                output = [item.to_dict() for item in engine.decision_outcomes()]
            else:
                output = engine.leverage_map().to_dict()
        print(json.dumps(output, indent=2))
        return 0
    if args.cmd == "doctor":
        ontology = Ontology.default()
        SpawnPolicyRegistry(ontology)
        plugins = load_all()
        print(f"connectors={len(plugins)}")
        print(f"ontology_types={len(ontology.types)}")
        print(f"predicates={len(ontology.predicates)}")
        print("mcp=" + ",".join(tool_names()))
        print("promotion_tool_exposed=false")
        print("ok")
        return 0
    if args.cmd == "mcp-serve":
        print(serve(_brain_path(args.brain)))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
