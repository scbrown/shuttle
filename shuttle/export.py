"""Export the run log into quipu — append-only episodes, signed facts.

Reads the local JSONL log past the export watermark and posts one
`/knot` per record into the run's WINDOW graph — the strict lane: the
target must already be registered committed (`ensure_window` does that),
unknown graphs refuse rather than minting, and SHACL context is
graph-aware. Quipu's write path is idempotent on identical asserts, so
at-least-once delivery re-sends harmlessly. The watermark advances only
after a record's write lands — never speculatively.

What a transition exports (the camayoc workflow slice,
`competency/workflow-and-archive.md` Q1–Q8):

- an `aegis:TransitionEvent` with step, from/to states, time,
  `aegis:performedBy` and `aegis:signature` (hex ed25519 over the canonical
  `shuttle-transition-v1` message — re-derivable from these facts alone);
- a re-asserted `aegis:currentState` on the run — derived convenience; the
  events are the truth, nothing is mutated in place.

Pre-export, shuttle verifies its OWN signature (fail fast on key drift);
consumers re-verify from the graph via `shuttle verify`.
"""

from __future__ import annotations

import os

from . import quipu_client as qc
from . import signing, state, windows


class ExportError(RuntimeError):
    """An export could not proceed. The watermark did not advance past it."""


def entity_ns() -> str:
    return os.environ.get("SHUTTLE_ENTITY_NS", "urn:shuttle:")


def run_iri(run_id: str) -> str:
    return f"{entity_ns()}run:{run_id}"


def workflow_iri(name: str) -> str:
    return f"{entity_ns()}workflow:{name}"


def event_iri(run_id: str, seq: int) -> str:
    return f"{entity_ns()}event:{run_id}:{seq}"


AEGIS = "http://aegis.gastown.local/ontology/"
PROV = "http://www.w3.org/ns/prov#"


def _prefixes() -> str:
    return f"@prefix aegis: <{AEGIS}> .\n@prefix prov: <{PROV}> .\n"


def definition_turtle(rec: dict) -> str:
    """A WorkflowDefinition and its declared steps — durable knowledge shape,
    but exported beside the runs so the window is self-describing."""
    wf = workflow_iri(rec["name"])
    lines = [_prefixes(), f"<{wf}> a aegis:WorkflowDefinition ."]
    for t in rec["transitions"]:
        step = f"{wf}/step/{t['step']}"
        lines.append(f"<{wf}> aegis:hasStep <{step}> .")
        lines.append(f"<{step}> a aegis:WorkflowStep .")
    return "\n".join(lines) + "\n"


def start_turtle(rec: dict) -> str:
    r = run_iri(rec["run"])
    return (
        _prefixes()
        + f"<{r}> a aegis:WorkflowRun ;\n"
        + f'    aegis:runOf <{workflow_iri(rec["definition"])}> ;\n'
        + f'    aegis:currentState "{rec["state"]}" ;\n'
        + f'    aegis:createdAt "{rec["at"]}" ;\n'
        + f'    aegis:sourceKind "observed" .\n'
    )


def transition_turtle(rec: dict, seq: int) -> str:
    r = run_iri(rec["run"])
    ev = event_iri(rec["run"], seq)
    wf_step = f'{workflow_iri(rec["definition"])}/step/{rec["step"]}'
    return (
        _prefixes()
        + f"<{ev}> a aegis:TransitionEvent ;\n"
        + f"    aegis:inRun <{r}> ;\n"
        + f"    aegis:atStep <{wf_step}> ;\n"
        + f'    aegis:fromState "{rec["from"]}" ;\n'
        + f'    aegis:toState "{rec["to"]}" ;\n'
        + f'    prov:endedAtTime "{rec["at"]}" ;\n'
        + f'    aegis:performedBy <{entity_ns()}agent:{rec["agent"]}> ;\n'
        + f'    aegis:signature "{rec["signature"]}" ;\n'
        + f'    aegis:sourceKind "observed" .\n'
        + f'<{r}> aegis:currentState "{rec["to"]}" .\n'
    )


def _knot_for(rec: dict, seq: int) -> dict:
    """The `/knot` body one log record earns: the record's Turtle, targeted
    at its window graph."""
    kind = rec.get("type")
    if kind == "define":
        month = windows.month_of(rec["at"])
        body = definition_turtle(rec)
    elif kind == "start":
        month = windows.month_of(rec["at"])
        body = start_turtle(rec)
    elif kind == "transition":
        month = rec["window"]
        body = transition_turtle(rec, seq)
    else:
        raise ExportError(f"record {seq} has unknown type {kind!r}")
    return {
        "turtle": body,
        "graph": windows.window_iri(month),
        "timestamp": rec["at"],
        "actor": rec.get("agent", "shuttle"),
        "_month": month,
    }


def verify_own_signature(rec: dict) -> None:
    """Fail fast on key drift BEFORE export: the record's signature must
    verify under the agent's current local key."""
    ok = signing.verify_transition(
        signing.public_key_hex(rec["agent"]),
        rec["signature"],
        run_iri(rec["run"]),
        rec["step"],
        rec["from"],
        rec["to"],
        rec["at"],
        rec["agent"],
    )
    if not ok:
        raise ExportError(
            f"transition of run '{rec['run']}' at {rec['at']} does not verify "
            f"under agent '{rec['agent']}''s current local key — the key "
            "changed since signing, or the log was edited. Refusing to export "
            "an unverifiable transition."
        )


def export(root=None) -> dict:
    """Drain the log past the watermark into quipu. Returns a summary.

    Per record: ensure the window (idempotent), post the episode, advance
    the watermark by exactly one. A failure stops the drain with the
    watermark before the failed record, so the retry is the same record.
    """
    recs = state.records(root)
    start = state.watermark(root)
    exported = 0
    months: set[str] = set()
    for seq in range(start, len(recs)):
        rec = recs[seq]
        if rec.get("type") == "transition":
            verify_own_signature(rec)
        body = _knot_for(rec, seq)
        month = body.pop("_month")
        if month not in months:
            windows.ensure_window(month, rec["at"])
            months.add(month)
        qc.post("/knot", body)
        state.advance_watermark(seq + 1, root)
        exported += 1
    return {"exported": exported, "total": len(recs), "windows": sorted(months)}
