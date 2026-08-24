# shuttle - Agent Instructions

> `CLAUDE.md` is a symlink to this file. Edit this file; if a tool ever
> replaces `CLAUDE.md` with a copy, restore it: `ln -sf AGENTS.md CLAUDE.md`.

## Project Overview

The workflow engine of the quipu stack: signed, append-only workflow runs
exported into quipu's time-windowed operational graphs, deep-frozen when
complete. Deliberately not a store (quipu), not a harness (shantytown), not
an ontology (camayoc) — shuttle owns the RUNS.

Sibling repos: scbrown/quipu (store + freeze), scbrown/camayoc (vocabulary +
windows convention), scbrown/shantytown (consumer), scbrown/neuralamplifier
(producer via `shuttle import-run`).

## Conventions

- **Events are the truth; state is a fold.** Never mutate a status in
  place — quipu triple retraction deletes nothing (measured), so the
  producer is append-only by construction. `model.fold_transitions` replays
  and refuses corruption loudly.
- **Refuse, never default.** Unknown step → the error names what was legal.
  Unreachable quipu → `QuipuUnreachable`, never `[]`. A `conforms:false`
  inside a 200 is a refusal. `GET /graphs` 404 → "cannot tell", never
  "no graphs".
- **Signing custody is quipu's `signing.rs` shape.** Per-agent ed25519 in
  0600 host files, auto-generated, never regenerated, never printed. The
  PUBLIC key is registered by a human; shuttle never registers its own keys.
- **One dependency (`cryptography`).** Everything else is stdlib. The
  zero-dep doctrine is shantytown's; the documented exception here is ed25519.
- **The window IRI scheme is a cross-repo contract** pinned by camayoc
  `tests/test_planes_2d.py` and `tests/test_export_and_cli.py` here. Change
  it in both places or not at all.
- Source files under 400 lines (`scripts/check-file-size.sh`, quipu's
  ratchet; tests exempt).

## Build Commands

```bash
just check           # parse + file-size ratchet
just test            # check + unit suite
just e2e             # cross-repo acceptance against a live quipu build
```

## Git Workflow — trunk-based, straight to `main`

**Work on `main` and push to `main`.** Do not create feature branches, and
do not open pull requests, unless explicitly asked. `just check` and
`just test` green before every push; never force-push `main`; small complete
commits. Work is not complete until `git push` succeeds.

## Beads: this repo is JSONL-only, no Dolt

Do not run `bd init`. `.beads/issues.jsonl` IS the tracker; append/close by
editing the JSONL additively (a record never disappears, a closed issue
never reopens, notes only grow), committed as
`chore(beads): ... (jsonl export)`.
