# shuttle

The workflow engine of the quipu stack. A shuttle carries the weft through
the warp: runs move through their workflow's declared steps, every
transition is **signed by the agent that performed it**, and the
append-only history is exported into [quipu](https://github.com/scbrown/quipu)'s
time-windowed operational graphs — where completed windows are
**deep-frozen** whole into read-only archives that stay queryable.

No daemon, no queue, no server. A local JSONL log is the hot path; quipu is
the governed record. Consumers ([shantytown](https://github.com/scbrown/shantytown)
crews, [NeuralAmplifier](https://github.com/scbrown/neuralamplifier)) read
the graph, never shuttle.

## Quick start

```bash
pip install -e .                       # Python 3.11+, one dep (cryptography)

shuttle keys init agent-a --workflow triage
#   -> prints the public key + registration Turtle; a HUMAN loads it into
#      the dataKind=identity graph. Shuttle never registers its own keys.

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

## The shape

| file | owns |
|---|---|
| `shuttle/model.py` | the pure state machine; events are the truth, state is a fold |
| `shuttle/state.py` | the append-only JSONL outbox + export watermark |
| `shuttle/signing.py` | per-agent ed25519 (0600 host files), `shuttle-transition-v1` canonical message |
| `shuttle/quipu_client.py` | `/episode`-only writes, `conforms:false` treated as refusal, `GET /graphs` capability probe |
| `shuttle/windows.py` | `{ns}/window/shuttle/runs/{YYYY-MM}` graphs + the `urn:shuttle:dataset:open` dataset |
| `shuttle/export.py` | log → signed Turtle → idempotent `/episode` batches |
| `shuttle/cli.py` | the whole v1 surface |

Design: [docs/design/shuttle.md](docs/design/shuttle.md). Vocabulary:
camayoc's workflow slice (`aegis:WorkflowRun`, `aegis:TransitionEvent`, …),
each term owed to a question in camayoc `competency/workflow-and-archive.md`.

```bash
just check    # parse + file-size ratchet
just test     # check + the unit suite
just e2e      # the cross-repo acceptance against a live quipu
```
