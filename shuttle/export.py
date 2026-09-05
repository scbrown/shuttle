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


def _definitions_by_name(recs: list[dict]) -> dict[str, dict]:
    """Every workflow definition in the log, latest wins.

    Read from the WHOLE log rather than from the unexported tail: a
    definition is recorded once, months before the runs that reference it,
    and by then it is long past the watermark.
    """
    out: dict[str, dict] = {}
    for rec in recs:
        if rec.get("type") == "define":
            out[rec["name"]] = rec
    return out


def _workflow_of(rec: dict) -> str | None:
    """The workflow a record asserts `aegis:runOf` against, if any."""
    kind = rec.get("type")
    if kind in ("start", "transition"):
        return rec.get("definition")
    if kind == "define":
        return rec.get("name")
    return None


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


def export(root=None, strict: bool = False) -> dict:
    """Drain the log past the watermark into quipu. Returns a summary.

    Per record: ensure the window (idempotent), ensure the window DECLARES
    the workflow the record references, post the episode, advance the
    watermark by exactly one.

    EACH WINDOW MUST DECLARE THE WORKFLOWS IT REFERENCES.
    ----------------------------------------------------
    A `define` record exports into the window of the month it was RECORDED.
    Runs export into the window of the month they RAN. Those are the same
    month exactly once — the month a workflow is introduced. Every month
    afterwards, a run asserts `aegis:runOf` against a workflow that the new
    window has never heard of, SHACL's class constraint refuses it, and one
    refusal stops the whole drain.

    That is not hypothetical: every shuttle definition was recorded in
    2026-08, so at 00:00 on 2026-09-01 every lane stopped exporting at once,
    and the shared record of 78 apply-lane runs a month went empty while the
    local log kept accepting advances (aegis-pll3fx). Declaring the three
    named workflows into the September graph by hand would have fixed it
    until 2026-10-01.

    So the definition is re-asserted into each window that references it.
    Quipu is idempotent on identical asserts, so a window that already has it
    pays one redundant knot per workflow per drain and changes nothing.

    ONE BAD RECORD MUST NOT COST THE WINDOW.
    ----------------------------------------
    The watermark is linear, so a record that cannot be posted used to stop
    the drain with every LATER record — including every healthy one — stuck
    behind it. By default a failed record is now quarantined and reported,
    and the drain continues; `strict=True` restores stop-on-first-failure.
    The quarantine is returned in the summary and written to the state
    directory, because a skip nobody is told about is silent loss.
    """
    recs = state.records(root)
    start = state.watermark(root)
    definitions = _definitions_by_name(recs)
    exported = 0
    months: set[str] = set()
    declared: set[tuple[str, str]] = set()
    quarantined: list[dict] = []

    for seq in range(start, len(recs)):
        rec = recs[seq]

        # OUTSIDE the quarantine, deliberately. An unverifiable signature is
        # not a bad record to step over — stepping over it would advance the
        # watermark past an integrity failure and drop it from the record
        # forever, which is a worse silent loss than the stall the quarantine
        # exists to prevent. Key drift and a tampered log must still stop the
        # drain.
        if rec.get("type") == "transition":
            verify_own_signature(rec)

        try:
            body = _knot_for(rec, seq)
            month = body.pop("_month")
            if month not in months:
                windows.ensure_window(month, rec["at"])
                months.add(month)

            name = _workflow_of(rec)
            if rec.get("type") == "define":
                # This record already carries the definition turtle; posting it
                # again below would duplicate the knot.
                declared.add((month, name))
            elif name and (month, name) not in declared:
                definition = definitions.get(name)
                if definition is None:
                    raise ExportError(
                        f"record {seq} references workflow {name!r}, which has "
                        "no definition anywhere in the log, so no window can "
                        "declare it. Record one with `shuttle define`."
                    )
                qc.post(
                    "/knot",
                    {
                        "turtle": definition_turtle(definition),
                        "graph": windows.window_iri(month),
                        "timestamp": rec["at"],
                        "actor": rec.get("agent", "shuttle"),
                    },
                )
                declared.add((month, name))

            qc.post("/knot", body)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            if strict:
                raise
            quarantined.append(
                {
                    "seq": seq,
                    "type": rec.get("type"),
                    "run": rec.get("run") or rec.get("name"),
                    "at": rec.get("at"),
                    "error": str(exc),
                }
            )
            state.advance_watermark(seq + 1, root)
            continue

        state.advance_watermark(seq + 1, root)
        exported += 1

    if quarantined:
        state.record_quarantine(quarantined, root)
    return {
        "exported": exported,
        "total": len(recs),
        "windows": sorted(months),
        "quarantined": quarantined,
    }
