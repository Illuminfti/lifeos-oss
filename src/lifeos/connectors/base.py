from __future__ import annotations

import importlib
from typing import Any

from lifeos.connectors import REGISTRY


def load(connector_id: str) -> Any:
    mod_name = REGISTRY[connector_id]
    mod = importlib.import_module(mod_name)
    return mod.Plugin()


def load_all() -> dict[str, Any]:
    return {cid: load(cid) for cid in REGISTRY}
