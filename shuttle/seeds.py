"""seeds as a step performer: the pull half of contract C2 (aegis-w3k75d.4).

A transition may declare `seed = {title, labels}`. While a run sits in that
transition's from-state, the step is performed by a seeds work item:

* `sd create --workflow-run <run IRI> --step <step> --visit <n>` names the
  seed. seeds derives the id from (run, step, visit), so repeating the create
  returns the same seed, open or closed (A5). shuttle therefore keeps no
  seed-id record of its own: the key IS the record, and a crashed reconcile
  cannot leave a second seed behind.
* `shuttle reconcile` reads `sd show <id> --json`. A seed closed with outcome
  `done` advances the run through the step. Any other outcome takes the
  step's `on_abandon`, or FLAGS the run, which is never advanced silently.

seeds stays ignorant of workflows beyond the link field, so there is no push
hook: a lost event costs one reconcile tick, never a stuck run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from . import model

# What seeds stores at close (A6, camayoc aegis:outcome). `done` is the only
# outcome that performs the step; every other one is an abandonment.
OUTCOMES = ("done", "abandoned", "superseded", "failed")


class SeedsError(RuntimeError):
    """sd could not be run, or answered outside its contract."""


def sd_binary() -> str:
    """`$SHUTTLE_SD` or `sd` on PATH. The store sd writes to is sd's own
    configuration (.seeds/config.toml, SEEDS_QUIPU_URL, ...), never shuttle's."""
    exe = os.environ.get("SHUTTLE_SD", "sd")
    if shutil.which(exe) is None:
        raise SeedsError(f"seeds CLI '{exe}' not found (set SHUTTLE_SD or install sd)")
    return exe


def _sd(args: list[str]) -> dict:
    proc = subprocess.run(
        [sd_binary(), *args, "--json"], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise SeedsError(
            f"sd {' '.join(args[:1])} exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SeedsError(f"sd {args[0]} printed non-JSON: {proc.stdout[:200]!r}") from exc
    if isinstance(out, list):
        if len(out) != 1:
            raise SeedsError(f"sd {args[0]} returned {len(out)} items, expected 1")
        out = out[0]
    if not isinstance(out, dict) or not out.get("id"):
        raise SeedsError(f"sd {args[0]} answered without an id: {out!r}")
    return out


def ensure_seed(run_iri: str, transition: model.Transition, visit: int) -> str:
    """The seed performing `transition` on this visit, created if absent."""
    args = [
        "create", transition.seed["title"],
        "--workflow-run", run_iri,
        "--step", transition.step,
        "--visit", str(visit),
    ]
    if transition.seed.get("labels"):
        args += ["--labels", ",".join(transition.seed["labels"])]
    return _sd(args)["id"]


def show(seed_id: str) -> dict:
    return _sd(["show", seed_id])


def decide(seed: dict, transition: model.Transition) -> tuple[str | None, str | None]:
    """(step to advance through, flag reason). Both None: still open.

    Branches on the governed outcome field only, never on reason text. A
    closed seed with no outcome is a close sd itself would never write (sd
    stores `done` by default), so it cannot be read either way: flag it.
    """
    if seed.get("status") != "closed":
        return None, None
    outcome = seed.get("outcome")
    if outcome == "done":
        return transition.step, None
    if outcome not in OUTCOMES:
        return None, f"seed {seed['id']} is closed with no recognised outcome ({outcome!r})"
    if transition.on_abandon:
        return transition.on_abandon, None
    return None, (
        f"seed {seed['id']} closed as {outcome}, and step '{transition.step}' "
        f"declares no on_abandon"
    )


def evidence(seed: dict) -> dict:
    """What the advance records about the seed that earned it (A7: the close
    tx is optional, so it is not part of the evidence)."""
    return {
        "seed": seed["id"],
        "outcome": seed.get("outcome"),
        "closed_at": seed.get("closed_at"),
    }
