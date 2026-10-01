# shuttle

*The workflow engine of the quipu stack: every transition signed, every window
frozen.*

Shuttle has no daemon, no queue, no server and no dashboard. A **local JSONL
log is the hot path**, and [quipu](https://github.com/scbrown/quipu) is the
governed record. Runs move through their workflow's declared steps. Every
transition is **signed by the agent that performed it** (per-agent ed25519).
The append-only history is exported into quipu's time-windowed operational
graphs, where a completed window is **deep-frozen** whole into a read-only
archive that stays queryable.

Consumers read the graph, never shuttle.

```text
  agents / crews                          (advance runs; each step signed)
        │
        ▼
    shuttle                               (state machine + JSONL outbox)
        │  export: signed Turtle, idempotent batches
        ▼
     quipu                                (windowed graphs · deep-frozen archives)
        │
        ▼
  shantytown · creel · NeuralAmplifier    (read the graph, never shuttle)
```

- [camayoc](https://github.com/scbrown/camayoc) owns the vocabulary: the
  workflow slice (`WorkflowRun`, `TransitionEvent`, …).
- [caboodle](https://github.com/scbrown/caboodle) installs and verifies
  shuttle as a stack member.
- [seeds](https://github.com/scbrown/seeds) work items can perform a step
  (see [Seed steps](seed-steps.md)).

## A run, end to end

```bash
shuttle keys init agent-a --workflow triage
#   prints the public key and registration Turtle. A HUMAN loads it into the
#   dataKind=identity graph; shuttle never registers its own keys.

shuttle define examples/triage.json
shuttle start triage run-1 --agent agent-a
shuttle advance run-1 --step claim  --agent agent-a    # signed
shuttle advance run-1 --step work   --agent agent-a
shuttle advance run-1 --step finish --agent agent-a    # terminal
shuttle status

shuttle export                          # drain the log into quipu windows
shuttle verify run-1                    # re-verify signatures FROM the graph
shuttle freeze-window 2026-07           # archive a completed window
```

Everything up to `status` is local. `export`, `verify` and `freeze-window`
talk to quipu.
