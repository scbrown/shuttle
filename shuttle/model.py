"""The workflow state machine — pure, no I/O.

The shape of the engine: a WorkflowDefinition declares states, steps and the
transitions between them; a Run is a fold over its append-only transition
events. Nothing here touches disk, network, keys or clocks, so every rule is
testable without a store — the shantytown `workflow.py` discipline.

State is NEVER mutated in place. The events are the truth
(`fold_transitions`); "current state" is a derived reading. This is the
producer half of the measured rule that quipu triple-level retraction
deletes nothing: a workflow engine that updates a status field in place
would need exactly the retraction that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class WorkflowError(ValueError):
    """A definition or transition is invalid. Raised, never returned softly."""


@dataclass(frozen=True)
class Transition:
    """One declared edge: performing `step` moves `from_state` to `to_state`."""

    step: str
    from_state: str
    to_state: str


@dataclass(frozen=True)
class Definition:
    """A named workflow: its states, terminal states, and legal transitions."""

    name: str
    initial: str
    terminal: tuple[str, ...]
    transitions: tuple[Transition, ...]

    def states(self) -> set[str]:
        out = {self.initial, *self.terminal}
        for t in self.transitions:
            out.add(t.from_state)
            out.add(t.to_state)
        return out


@dataclass(frozen=True)
class Event:
    """One performed transition of one run — the append-only atom."""

    run: str
    step: str
    from_state: str
    to_state: str
    at: str
    agent: str
    signature: str = ""


@dataclass
class Run:
    """A run folded from its events. `events` is the truth; `state` derived."""

    id: str
    definition: str
    state: str
    events: list[Event] = field(default_factory=list)


def parse_definition(raw: dict) -> Definition:
    """Parse and validate a definition. Refuses rather than repairing.

    Rules, each refused with the reason: a name, an initial state, at least
    one terminal state, at least one transition; every terminal reachable
    from nothing (terminals need no outgoing edge, but an edge OUT of a
    terminal is refused — a terminal a run can leave is not terminal);
    no two transitions may share (step, from_state) — the engine must never
    tiebreak silently.
    """
    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise WorkflowError("definition needs a 'name'")
    initial = raw.get("initial")
    if not initial or not isinstance(initial, str):
        raise WorkflowError(f"definition '{name}' needs an 'initial' state")
    terminal = raw.get("terminal")
    if not terminal or not isinstance(terminal, list):
        raise WorkflowError(f"definition '{name}' needs a non-empty 'terminal' list")
    raw_ts = raw.get("transitions")
    if not raw_ts or not isinstance(raw_ts, list):
        raise WorkflowError(f"definition '{name}' needs a non-empty 'transitions' list")

    transitions = []
    seen: set[tuple[str, str]] = set()
    for t in raw_ts:
        try:
            tr = Transition(step=t["step"], from_state=t["from"], to_state=t["to"])
        except (KeyError, TypeError) as exc:
            raise WorkflowError(
                f"definition '{name}': every transition needs step/from/to ({t!r})"
            ) from exc
        key = (tr.step, tr.from_state)
        if key in seen:
            raise WorkflowError(
                f"definition '{name}': two transitions share step '{tr.step}' from "
                f"state '{tr.from_state}' — the engine never tiebreaks silently"
            )
        seen.add(key)
        if tr.from_state in terminal:
            raise WorkflowError(
                f"definition '{name}': transition '{tr.step}' leaves terminal state "
                f"'{tr.from_state}' — a terminal a run can leave is not terminal"
            )
        transitions.append(tr)

    return Definition(
        name=name,
        initial=initial,
        terminal=tuple(terminal),
        transitions=tuple(transitions),
    )


def validate_advance(defn: Definition, current: str, step: str) -> str:
    """The to-state performing `step` from `current` earns, or a refusal.

    Refuses rather than guessing: an unknown step, a step not legal from the
    current state, or advancing a terminal run are all errors that name what
    WAS legal, because "which move was I allowed to make" is the question the
    caller is actually asking.
    """
    if current in defn.terminal:
        raise WorkflowError(
            f"run is terminal ('{current}'); no step advances a finished run"
        )
    legal = [t for t in defn.transitions if t.from_state == current]
    for t in legal:
        if t.step == step:
            return t.to_state
    steps = sorted({t.step for t in legal})
    raise WorkflowError(
        f"step '{step}' is not legal from state '{current}'. Legal here: {steps}"
    )


def fold_transitions(defn: Definition, events: list[Event]) -> str:
    """The state a run is in after its events — the truth, re-derived.

    Replays every event through `validate_advance`, so a corrupted or
    reordered log is a loud error rather than a quietly wrong state.
    """
    state = defn.initial
    for ev in events:
        if ev.from_state != state:
            raise WorkflowError(
                f"event log corrupt: event at '{ev.at}' claims from-state "
                f"'{ev.from_state}' but the fold is at '{state}'"
            )
        state = validate_advance(defn, state, ev.step)
        if state != ev.to_state:
            raise WorkflowError(
                f"event log corrupt: event at '{ev.at}' claims to-state "
                f"'{ev.to_state}' but '{ev.step}' from '{ev.from_state}' "
                f"yields '{state}'"
            )
    return state
