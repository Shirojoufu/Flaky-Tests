"""Read and write run records: one JSON file per CI run attempt (per environment, in a matrix)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1


def run_filename(record: dict) -> str:
    stamp = record["timestamp"].replace("-", "").replace(":", "")
    name = f"{stamp}-{record['run_id']}-{record['run_attempt']}"
    if record.get("environment"):
        name += f"-{record['environment']}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".json"


def save_run(record: dict, store: Path) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / run_filename(record)
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_run(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_identity(record: dict) -> tuple[str, int, str]:
    """A CI run attempt, plus the matrix environment when there is one."""
    return str(record.get("run_id")), int(record.get("run_attempt", 1)), record.get("environment") or ""


def load_runs(store: Path) -> list[dict]:
    """All runs in the store, oldest first. A re-ingested run attempt replaces the earlier copy."""
    runs: dict[tuple[str, int, str], dict] = {}
    for path in sorted(Path(store).glob("*.json")):
        try:
            record = load_run(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: skipping unreadable run record {path}: {exc}", file=sys.stderr)
            continue
        if record.get("schema") != SCHEMA_VERSION:
            continue
        runs[run_identity(record)] = record
    return sorted(runs.values(), key=lambda r: (r["timestamp"], *run_identity(r)))
