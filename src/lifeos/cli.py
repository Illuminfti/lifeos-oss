from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lifeos import __version__
from lifeos.autowiki import propose_entity
from lifeos.connectors import REGISTRY
from lifeos.connectors.base import load, load_all
from lifeos.ingest import IngestQueue
from lifeos.mcp_server import serve, tool_names
from lifeos.wiki import init_brain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifeos")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("path")

    sub.add_parser("connector-list")
    p_desc = sub.add_parser("connector-describe")
    p_desc.add_argument("id")

    p_fix = sub.add_parser("connector-fixture")
    p_fix.add_argument("id")
    p_fix.add_argument("--brain", default="./brain")

    sub.add_parser("doctor")
    sub.add_parser("mcp-serve")
    sub.add_parser("version")

    args = parser.parse_args(argv)
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "init":
        root = init_brain(Path(args.path))
        print(str(root))
        return 0
    if args.cmd == "connector-list":
        for cid in sorted(REGISTRY):
            plug = load(cid)
            m = plug.describe()
            print(f"{cid}\t{m.display_name}\t{m.custody}")
        return 0
    if args.cmd == "connector-describe":
        print(json.dumps(load(args.id).describe().to_dict(), indent=2))
        return 0
    if args.cmd == "connector-fixture":
        brain = init_brain(Path(args.brain))
        plug = load(args.id)
        report = plug.test_fixture()
        events = plug.backfill({})
        q = IngestQueue(brain / ".lifeos" / "state.sqlite")
        stored = 0
        for ev in events:
            if q.accept(ev):
                stored += 1
                propose_entity(brain, ev, "fixture-entity")
        print(json.dumps({"fixture": report, "stored": stored, "queue": q.count()}))
        return 0
    if args.cmd == "doctor":
        plugins = load_all()
        print(f"connectors={len(plugins)}")
        print("mcp=" + ",".join(tool_names()))
        print("ok")
        return 0
    if args.cmd == "mcp-serve":
        print(serve())
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
