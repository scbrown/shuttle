"""The `shuttle` CLI — define, run, sign, export, verify, freeze.

Every state change is an append to the local JSONL log; `export` drains it
into quipu's windowed operational graphs. The CLI is the whole v1 surface —
no daemon, no queue, no HTTP server (a deliberate deferral, recorded in the
design doc): shuttle is invoked by agents and harnesses, and quipu is the
shared record they meet in.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from . import export as export_mod
from . import model, quipu_client as qc, signing, state, windows


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CliError(RuntimeError):
    pass


def _index(root=None) -> tuple[dict[str, model.Definition], dict[str, dict]]:
    """Fold the log: definitions by name, runs by id (definition, start
    month, events). The fold IS the read model; corruption is loud."""
    defs: dict[str, model.Definition] = {}
    runs: dict[str, dict] = {}
    for rec in state.records(root):
        t = rec.get("type")
        if t == "define":
            defs[rec["name"]] = model.parse_definition(rec)
        elif t == "start":
            runs[rec["run"]] = {
                "definition": rec["definition"],
                "window": windows.month_of(rec["at"]),
                "events": [],
            }
        elif t == "transition":
            if rec["run"] not in runs:
                raise CliError(f"log corrupt: transition for unknown run '{rec['run']}'")
            runs[rec["run"]]["events"].append(
                model.Event(
                    run=rec["run"],
                    step=rec["step"],
                    from_state=rec["from"],
                    to_state=rec["to"],
                    at=rec["at"],
                    agent=rec["agent"],
                    signature=rec["signature"],
                )
            )
    return defs, runs


def _run_state(defs, runs, run_id: str) -> tuple[model.Definition, dict, str]:
    if run_id not in runs:
        raise CliError(f"unknown run '{run_id}'")
    run = runs[run_id]
    if run["definition"] not in defs:
        raise CliError(f"run '{run_id}' references undefined workflow '{run['definition']}'")
    defn = defs[run["definition"]]
    return defn, run, model.fold_transitions(defn, run["events"])


def cmd_keys_init(args) -> int:
    wf = export_mod.workflow_iri(args.workflow) if args.workflow else f"{export_mod.entity_ns()}workflow:*"
    print(f"agent:      {args.agent}")
    print(f"public key: {signing.public_key_hex(args.agent)}")
    print()
    print("# Registration Turtle — review and load into the IDENTITY graph")
    print("# (dataKind=identity, never frozen) YOURSELF; shuttle never")
    print("# registers its own keys. The trust root stays human-owned.")
    print(signing.registration_turtle(args.agent, wf))
    return 0


def cmd_define(args) -> int:
    raw = json.loads(Path(args.file).read_text())
    defn = model.parse_definition(raw)  # refuse before recording
    rec = {
        "type": "define",
        "name": defn.name,
        "initial": defn.initial,
        "terminal": list(defn.terminal),
        "transitions": [
            {"step": t.step, "from": t.from_state, "to": t.to_state}
            for t in defn.transitions
        ],
        "at": args.at or now_iso(),
    }
    seq = state.append(rec)
    print(f"defined workflow '{defn.name}' (record {seq})")
    return 0


def cmd_start(args) -> int:
    defs, runs = _index()
    if args.definition not in defs:
        raise CliError(f"unknown workflow '{args.definition}'; run `shuttle define` first")
    if args.run in runs:
        raise CliError(f"run '{args.run}' already exists")
    at = args.at or now_iso()
    rec = {
        "type": "start",
        "run": args.run,
        "definition": args.definition,
        "state": defs[args.definition].initial,
        "agent": args.agent,
        "at": at,
    }
    state.append(rec)
    print(
        f"started run '{args.run}' of '{args.definition}' in state "
        f"'{defs[args.definition].initial}' (window {windows.month_of(at)})"
    )
    return 0


def cmd_advance(args) -> int:
    defs, runs = _index()
    defn, run, current = _run_state(defs, runs, args.run)
    to_state = model.validate_advance(defn, current, args.step)
    at = args.at or now_iso()
    sig = signing.sign_transition(
        args.agent, export_mod.run_iri(args.run), args.step, current, to_state, at
    )
    state.append(
        {
            "type": "transition",
            "run": args.run,
            "definition": run["definition"],
            "window": run["window"],
            "step": args.step,
            "from": current,
            "to": to_state,
            "at": at,
            "agent": args.agent,
            "signature": sig,
        }
    )
    terminal = " (terminal)" if to_state in defn.terminal else ""
    print(f"run '{args.run}': {current} -[{args.step}]-> {to_state}{terminal}")
    return 0


def cmd_status(args) -> int:
    defs, runs = _index()
    if args.run:
        defn, run, current = _run_state(defs, runs, args.run)
        terminal = "terminal" if current in defn.terminal else "open"
        print(f"{args.run}\t{run['definition']}\t{current}\t{terminal}\t{len(run['events'])} events")
    else:
        for rid in sorted(runs):
            defn, run, current = _run_state(defs, runs, rid)
            terminal = "terminal" if current in defn.terminal else "open"
            print(f"{rid}\t{run['definition']}\t{current}\t{terminal}")
    return 0


def cmd_export(args) -> int:
    qc.probe_graph_kinds()  # capability first: cannot-tell beats zero rows
    summary = export_mod.export()
    print(
        f"exported {summary['exported']} of {summary['total']} records "
        f"(windows: {', '.join(summary['windows']) or 'none'})"
    )
    return 0


def cmd_verify(args) -> int:
    """Re-verify a run's transitions FROM THE GRAPH — the consumer-side
    check, workable against a hot or a frozen window (cold composition)."""
    defs, runs = _index()
    _, run, _ = _run_state(defs, runs, args.run)
    r = export_mod.run_iri(args.run)
    a = export_mod.AEGIS
    rows = qc.query(
        f"SELECT ?ev ?step ?from ?to ?at ?agent ?sig WHERE {{ "
        f"?ev <{a}inRun> <{r}> ; <{a}atStep> ?step ; <{a}fromState> ?from ; "
        f"<{a}toState> ?to ; <{export_mod.PROV}endedAtTime> ?at ; "
        f"<{a}performedBy> ?agent ; <{a}signature> ?sig . }}",
        graph=windows.window_iri(run["window"]),
    ).get("rows", [])
    if not rows:
        raise CliError(
            f"no exported transitions found for run '{args.run}' in window "
            f"{run['window']} — export first, or the window moved"
        )
    regs = qc.query(
        f"SELECT ?verifier ?pk WHERE {{ ?reg a <{a}VerifierRegistration> ; "
        f"<{a}verifier> ?verifier ; <{a}publicKey> ?pk . }}",
        graph=args.identity_graph,
    ).get("rows", [])
    keys = {row["verifier"]: row["pk"] for row in regs}
    bad = 0
    for row in rows:
        agent = row["agent"].rsplit(":", 1)[-1]
        step = row["step"].rsplit("/", 1)[-1]
        pk = keys.get(agent)
        if pk is None:
            print(f"UNREGISTERED: {row['ev']} performed by '{agent}' has no key in the identity graph")
            bad += 1
            continue
        ok = signing.verify_transition(
            pk, row["sig"], r, step, row["from"], row["to"], row["at"], agent
        )
        if not ok:
            print(f"BAD SIGNATURE: {row['ev']}")
            bad += 1
    if bad:
        print(f"{bad} of {len(rows)} transitions FAILED verification")
        return 1
    print(f"all {len(rows)} exported transitions verify")
    return 0


def cmd_import_run(args) -> int:
    """Import a foreign transitions JSONL (the NeuralAmplifier export shape)
    as a complete run: define (if carried), start, then each transition
    signed by the IMPORTING agent — the importer attests the mapping, since
    the original system did not sign shuttle messages."""
    lines = [json.loads(x) for x in Path(args.file).read_text().splitlines() if x.strip()]
    if not lines:
        raise CliError(f"{args.file} holds no records")
    defs, runs = _index()
    for rec in lines:
        t = rec.get("type")
        if t == "define":
            if rec["name"] not in defs:
                model.parse_definition(rec)  # refuse before recording
                state.append(rec)
        elif t == "start":
            if rec["run"] in runs:
                raise CliError(f"run '{rec['run']}' already exists; refusing to merge histories")
            state.append({**rec, "agent": args.agent})
        elif t == "transition":
            at = rec["at"]
            sig = signing.sign_transition(
                args.agent, export_mod.run_iri(rec["run"]), rec["step"], rec["from"], rec["to"], at
            )
            state.append({**rec, "agent": args.agent, "signature": sig})
        else:
            raise CliError(f"unknown record type {t!r} in {args.file}")
    print(f"imported {len(lines)} records from {args.file} (signed by '{args.agent}')")
    return 0


def cmd_freeze_window(args) -> int:
    """Freeze a completed window via quipu, then drop it from the open
    dataset. The freeze itself is quipu's (hash-verified relocation); this
    is the producer's bookkeeping around it."""
    iri = windows.window_iri(args.month)
    at = now_iso()
    result = qc.post("/graph/freeze", {"graph": iri, "timestamp": at, "actor": args.agent})
    windows.mark_frozen(args.month, at)
    print(
        f"froze {iri}\n  pack: {result.get('pack')}\n  hash: {result.get('content_hash')}\n"
        f"  compose it back in via FROM <{iri}>, FROM <urn:quipu:dataset:frozen>, "
        f"or include_kinds:[\"archive\"]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shuttle", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keys", help="key management")
    ksub = k.add_subparsers(dest="keys_cmd", required=True)
    ki = ksub.add_parser("init", help="create/show an agent key + registration turtle")
    ki.add_argument("agent")
    ki.add_argument("--workflow", help="workflow the registration attests")
    ki.set_defaults(fn=cmd_keys_init)

    d = sub.add_parser("define", help="record a workflow definition from JSON")
    d.add_argument("file")
    d.add_argument("--at")
    d.set_defaults(fn=cmd_define)

    s = sub.add_parser("start", help="start a run")
    s.add_argument("definition")
    s.add_argument("run")
    s.add_argument("--agent", required=True)
    s.add_argument("--at")
    s.set_defaults(fn=cmd_start)

    a = sub.add_parser("advance", help="perform one signed transition")
    a.add_argument("run")
    a.add_argument("--step", required=True)
    a.add_argument("--agent", required=True)
    a.add_argument("--at")
    a.set_defaults(fn=cmd_advance)

    st = sub.add_parser("status", help="run state(s), folded from the log")
    st.add_argument("run", nargs="?")
    st.set_defaults(fn=cmd_status)

    e = sub.add_parser("export", help="drain the log into quipu windows")
    e.set_defaults(fn=cmd_export)

    v = sub.add_parser("verify", help="re-verify a run's signatures from the graph")
    v.add_argument("run")
    v.add_argument(
        "--identity-graph",
        default="urn:shuttle:graph:identity",
        help="the dataKind=identity graph holding VerifierRegistrations",
    )
    v.set_defaults(fn=cmd_verify)

    ir = sub.add_parser("import-run", help="import a foreign transitions JSONL as a signed run")
    ir.add_argument("file")
    ir.add_argument("--agent", required=True)
    ir.set_defaults(fn=cmd_import_run)

    fw = sub.add_parser("freeze-window", help="freeze a completed window via quipu")
    fw.add_argument("month")
    fw.add_argument("--agent", default="shuttle")
    fw.set_defaults(fn=cmd_freeze_window)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (
        CliError,
        model.WorkflowError,
        state.StateError,
        signing.SigningError,
        export_mod.ExportError,
        qc.QuipuUnreachable,
        qc.QuipuWriteRejected,
        qc.QuipuCapabilityMissing,
        ValueError,
    ) as exc:
        print(f"shuttle: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
