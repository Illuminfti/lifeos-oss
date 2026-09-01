#!/usr/bin/env python3
"""Fail a distribution that contains private-estate or obvious secret material."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".sh", ".txt"}

FORBIDDEN = (
    "/home/" + "ubuntu/",
    "illumi" + "-wiki",
    "Hermes" + " Guardian",
    "Guardian" + " soul",
    "BEGIN OPENSSH" + " PRIVATE KEY",
    "BEGIN RSA" + " PRIVATE KEY",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*['\"][A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)

errors: list[str] = []
for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in SKIP for part in path.parts):
        continue
    if path.suffix not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Makefile"}:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for marker in FORBIDDEN:
        if marker in text:
            errors.append(f"{path.relative_to(ROOT)}: forbidden marker {marker!r}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            errors.append(f"{path.relative_to(ROOT)}: possible secret matching {pattern.pattern}")

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
print("public boundary scan: clean")
