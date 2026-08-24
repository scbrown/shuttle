# Design: shuttle — signed runs, windowed export, freezable history

> **Implementation status (2026-08-24):** ✅ **v1 built: CLI + library, the
> full vertical slice.** Pure state machine (`shuttle/model.py`), append-only
> JSONL outbox with a forward-only export watermark (`state.py`), per-agent
> ed25519 signing in quipu's `signing.rs` custody shape (`signing.py`),
> a quipu client riding `/knot`'s strict graph lane, with the shantytown error discipline and the
> `GET /graphs` capability probe (`quipu_client.py`), the camayoc window
> scheme + `urn:shuttle:dataset:open` maintenance (`windows.py`), signed
> Turtle export with pre-export self-verification (`export.py`), and the
> whole surface as `shuttle` subcommands (`cli.py`). 27 unit tests; the
> cross-repo acceptance `scripts/e2e_slice.sh` **passes against quipu main
> (2026-08-24)**: keys → human registration → define → start → three signed
> advances → export → hot query + verify → freeze → identical rows via the
> frozen dataset and `include_kinds` → signatures verifying against the
> frozen window.
> Deliberately NOT built (v1 deferrals, on the tracker): an HTTP/MCP
> server (agents invoke the CLI; quipu is the meeting point), quipu
> write-gate signature enforcement (quipu-8cc — unverifiable transitions
> are *detectable* from day one via `shuttle verify` and the
> `shuttle-unverified-transitions` stored-query pattern), key rotation
> ceremony, and a consumer path in NeuralAmplifier (its v1 stub exports).

**Status:** the stack had both halves of a workflow loop and no engine:
shantytown *pulls* governed workflows from quipu's transaction log, and
NeuralAmplifier *pushes* decision episodes in. Shuttle is the engine between
them — it owns runs, signs every transition, and makes quipu the shared,
governed, freezable record.

## 1. What shuttle is not

No daemon, no queue, no broker, no server. The hot path is a local
append-only JSONL log (the NeuralAmplifier DecisionLog precedent); `export`
drains it into quipu at-least-once with idempotent episode names. Consumers
never talk to shuttle — they read the graph. This keeps shantytown's
no-daemon doctrine intact and makes quipu the single meeting point.

## 2. Append-only, stated as a producer rule

Shantytown measured that quipu triple-level `/retract` deletes nothing, so
workflow state CANNOT be a mutated status field. Shuttle is append-only by
construction: `aegis:TransitionEvent`s are the truth; `aegis:currentState`
is re-asserted per transition as a derived convenience read by latest
`valid_from`. The fold (`model.fold_transitions`) replays every event
through the same validator that admitted it, so a corrupt or reordered log
is a loud error, never a quietly wrong state.

## 3. Signing — agent-specific, freeze-proof

Every transition is signed by the performing agent's ed25519 key over the
canonical message

    shuttle-transition-v1|{run_iri}|{step}|{from}|{to}|{at}|{agent}

which is re-derivable from the exported facts alone. Custody mirrors quipu
`src/signing.rs`: host files, 0600, auto-generated, never regenerated.
Public keys are registered **by a human** as `aegis:VerifierRegistration`
facts in a `dataKind=identity` graph — never frozen, so signatures inside a
frozen window stay verifiable; shuttle never registers its own keys, keeping
the trust root human-owned.

Verification happens three times, deliberately: at `advance` (the signature
is created from validated inputs), at `export` (fail fast on key drift or a
tampered log — an unverifiable transition refuses to export), and at
`shuttle verify` (the consumer-side check, from the graph, against the
identity graph's registrations — works across cold composition unchanged).

## 4. Windows and freeze

Runs land in the window graph of the month they **started**
(`{ns}/window/shuttle/runs/{YYYY-MM}`, the camayoc scheme, pinned by tests
in both repos), so freezing a window never splits a run. `ensure_window`
registers-and-labels or neither (`operational`/`fresh`/`soleRecord`), and
maintains `urn:shuttle:dataset:open` — the dataset consumers put in `FROM`,
because explicit scope is the fix for the silent-zero-rows hazard.
`shuttle freeze-window` calls quipu's hash-verified freeze and drops the
window from the open dataset; `urn:quipu:dataset:frozen` and
`include_kinds:["archive"]` carry it from there.

## 5. The import seam

`shuttle import-run` ingests a foreign transitions JSONL (NeuralAmplifier's
`workflow_export` shape) as a complete run, signed by the **importing**
agent — the importer attests the mapping, since the origin system never
signed shuttle messages. That distinction is honest and queryable:
competency Q6 asks who performed a transition, and for an imported run the
answer is the importer.

## 6. Related

- quipu `docs/design/graph-kinds-and-deep-freeze.md` — the archive half.
- camayoc `docs/design/workflow-and-archive.md` + `competency/workflow-and-archive.md`
  — the vocabulary and the questions it owes.
- shantytown `shuttle_runs` — the consumer stub.
