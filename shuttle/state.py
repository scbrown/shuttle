"""The append-only JSONL run log — shuttle's hot path and crash-safe outbox.

Local state is a convenience and a buffer; **quipu is the governed record**.
The log holds three record types (`define`, `start`, `transition`), each one
JSON object per line, appended and never rewritten — the NeuralAmplifier
DecisionLog precedent. `export.py` drains records past a persisted watermark
into quipu `/episode` batches; at-least-once delivery, idempotent replays
(episode names are deterministic).

Nothing here is durable by itself: a crashed export re-sends from the
watermark, and a lost state dir loses only what was never exported — which
is why `shuttle export` belongs at the end of every session that wrote.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class StateError(RuntimeError):
    """The state dir refused an operation. Never silently swallowed."""


def state_dir() -> Path:
    """The state directory: `$SHUTTLE_STATE_DIR` or `~/.local/state/shuttle`."""
    d = os.environ.get("SHUTTLE_STATE_DIR")
    if d:
        return Path(d)
    return Path.home() / ".local" / "state" / "shuttle"


def _log_path(root: Path) -> Path:
    return root / "runs.jsonl"


def _watermark_path(root: Path) -> Path:
    return root / "export-watermark.json"


def append(record: dict, root: Path | None = None) -> int:
    """Append one record; returns its 0-based sequence number.

    The only write primitive. `type` is mandatory so a reader never guesses;
    the file is opened in append mode and one record is one `write()` call,
    which on a local filesystem keeps concurrent appenders line-atomic for
    records under the pipe buffer size — and shuttle records are far under.
    """
    if "type" not in record:
        raise StateError("a state record needs a 'type'")
    root = root or state_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _log_path(root)
    seq = sum(1 for _ in path.open()) if path.exists() else 0
    with path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return seq


def records(root: Path | None = None) -> list[dict]:
    """Every record, in append order. A malformed line is a loud error —
    the log has no schema enforcement, and one bad line silently skipped is
    a run that folds to the wrong state."""
    root = root or state_dir()
    path = _log_path(root)
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.open()):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise StateError(f"{path}:{i + 1} is not JSON: {exc}") from exc
    return out


def watermark(root: Path | None = None) -> int:
    """How many records have been fully exported. 0 when never exported."""
    root = root or state_dir()
    path = _watermark_path(root)
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text())["exported"])
    except (OSError, ValueError, KeyError) as exc:
        raise StateError(
            f"{path} is unreadable ({exc}); refusing to guess the export "
            "position — a wrong watermark either re-sends (harmless, "
            "idempotent) or SKIPS (silent loss), and it cannot be told which."
        ) from exc


def advance_watermark(count: int, root: Path | None = None) -> None:
    """Move the watermark forward to `count`. Refuses to move backward —
    a shrinking watermark re-exports at best and masks loss at worst."""
    root = root or state_dir()
    current = watermark(root)
    if count < current:
        raise StateError(
            f"watermark cannot move backward ({current} -> {count})"
        )
    root.mkdir(parents=True, exist_ok=True)
    tmp = _watermark_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"exported": count}))
    tmp.replace(_watermark_path(root))
