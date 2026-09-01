"""Adapters for GBrain and pgGraph.

LifeOS does not ship a third search engine. Search and synthesis go through
GBrain. pgGraph is an optional derived accelerator, never canon.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any, Mapping

from lifeos.config import BrainConfig
from lifeos.errors import GBrainUnavailable

CANONICAL_IMPORT_DIRS = (
    "00-dashboards",
    "03-entities",
    "04-work",
    "05-knowledge",
    "06-execution",
)


class GBrainAdapter:
    def __init__(self, config: BrainConfig):
        self.config = config
        command = config.get("gbrain", "command", default="gbrain")
        self.command = shlex.split(str(command)) if isinstance(command, str) else list(command)

    def available(self) -> bool:
        return bool(self.command) and shutil.which(self.command[0]) is not None

    def _run(self, args: list[str], *, timeout: int = 180) -> str:
        if not self.available():
            raise GBrainUnavailable(
                "GBrain is not installed or not on PATH. LifeOS will not substitute a hidden search engine."
            )
        completed = subprocess.run(
            [*self.command, *args],
            cwd=self.config.root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise GBrainUnavailable(
                f"GBrain command failed ({completed.returncode}): {completed.stderr[:2000]}"
            )
        return completed.stdout

    @staticmethod
    def _decode(output: str) -> Any:
        stripped = output.strip()
        if not stripped:
            return {}
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return {"text": stripped}

    def doctor(self) -> Mapping[str, Any]:
        try:
            return self._decode(self._run(["doctor", "--json"], timeout=60))
        except GBrainUnavailable as exc:
            return {"available": False, "error": str(exc)}

    def sync(self, *, embed: bool = False) -> dict[str, Any]:
        imported: list[str] = []
        for relative in CANONICAL_IMPORT_DIRS:
            path = self.config.root / relative
            if not path.is_dir():
                continue
            self._run(["import", str(path), "--no-embed"], timeout=600)
            imported.append(relative)
        if embed:
            self._run(["embed", "--stale"], timeout=1800)
        return {"imported": imported, "embedded": embed}

    def search(self, query: str, *, limit: int = 10) -> Any:
        payload = json.dumps({"query": query, "limit": limit})
        try:
            return self._decode(self._run(["call", "search", payload]))
        except GBrainUnavailable as call_error:
            try:
                return self._decode(self._run(["search", query]))
            except GBrainUnavailable:
                raise call_error

    def query(self, question: str, *, limit: int = 10) -> Any:
        payload = json.dumps({"question": question, "query": question, "limit": limit})
        try:
            return self._decode(self._run(["call", "query", payload], timeout=300))
        except GBrainUnavailable as call_error:
            try:
                return self._decode(self._run(["query", question], timeout=300))
            except GBrainUnavailable:
                raise call_error


class PgGraphAdapter:
    """Optional command seam for the derived pgGraph projection.

    The public product does not invent a graph database or write provider text
    into pgGraph. Installations may configure the audited pgGraph builder they
    already operate.
    """

    def __init__(self, config: BrainConfig):
        self.config = config
        command = config.get("pggraph", "command", default=None)
        self.command = shlex.split(str(command)) if command else []

    def available(self) -> bool:
        return bool(self.command) and shutil.which(self.command[0]) is not None

    def rebuild(self) -> Mapping[str, Any]:
        if not self.available():
            return {
                "available": False,
                "derived": True,
                "reason": "pgGraph builder is not configured",
            }
        completed = subprocess.run(
            [*self.command, "rebuild", "--brain", str(self.config.root)],
            text=True,
            capture_output=True,
            timeout=1800,
            check=False,
        )
        return {
            "available": True,
            "derived": True,
            "ok": completed.returncode == 0,
            "output": completed.stdout[-4000:],
            "error": completed.stderr[-4000:] if completed.returncode else None,
        }

    def health(self) -> Mapping[str, Any]:
        return {
            "available": self.available(),
            "derived": True,
            "canonical": False,
        }
