"""Window graphs and the open-windows dataset — where runs land in quipu.

The camayoc convention (`camayoc scripts/windows.py`), reimplemented rather
than imported (no cross-repo Python dependency); camayoc's
`tests/test_planes_2d.py` pins the IRI scheme so the two cannot drift:

    {WINDOW_NS}shuttle/runs/{YYYY-MM}      e.g.
    https://camayoc.local/window/shuttle/runs/2026-08

A run's whole history lands in the window of the month it STARTED, so
freezing a window never splits a run. Every `ensure_window` also maintains
`urn:shuttle:dataset:open` — the dataset consumers (shantytown) put in
`FROM`, so their queries name scope explicitly instead of silently reading
zero rows from the default graph. `mark_frozen` removes a window from it;
quipu's own `urn:quipu:dataset:frozen` picks the window up on freeze.

Create-and-label is both-or-neither, the plane discipline: a window
registered but unlabelled never earns a freeze and is unfalsifiable from
outside.
"""

from __future__ import annotations

import os
import re

from . import quipu_client as qc

FAMILY = "shuttle/runs"

#: The dataset of not-yet-frozen shuttle windows. Consumers query
#: `FROM <this>`; the name is a contract with shantytown's `shuttle_runs`.
OPEN_DATASET = os.environ.get("SHUTTLE_OPEN_DATASET", "urn:shuttle:dataset:open")

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def window_ns() -> str:
    """Derived from the camayoc plane namespace — one parameter for the
    stack, never a second hostname to drift."""
    plane_ns = os.environ.get("CAMAYOC_PLANE_NS", "https://camayoc.local/plane/")
    return plane_ns.replace("/plane/", "/window/")


def window_iri(yyyymm: str) -> str:
    if not _MONTH.match(yyyymm):
        raise ValueError(f"window month '{yyyymm}' must be YYYY-MM")
    return f"{window_ns()}{FAMILY}/{yyyymm}"


def month_of(timestamp: str) -> str:
    """The window month a run started in: the YYYY-MM prefix of its ISO
    start timestamp. Validated, never guessed."""
    m = timestamp[:7]
    if not _MONTH.match(m):
        raise ValueError(f"timestamp '{timestamp}' has no YYYY-MM prefix")
    return m


def ensure_window(yyyymm: str, timestamp: str) -> str:
    """Register + label the window graph, and add it to the open dataset.

    Idempotent. Label is `operational`/`fresh`/`soleRecord` — soleRecord is
    honest until a freeze relabels to archive/backed.
    """
    iri = window_iri(yyyymm)
    qc.post("/graph/create", {"graph": iri})
    qc.post(
        "/graph/label",
        {
            "graph": iri,
            "timestamp": timestamp,
            "kind": "operational",
            "freshness": "fresh",
            "durability": "soleRecord",
            "actor": "shuttle-windows",
        },
    )
    _open_dataset_update(iri, add=True, timestamp=timestamp)
    return iri


def _open_members() -> list[str]:
    listing = qc.post("/datasets", {"action": "show", "name": OPEN_DATASET})
    members = listing.get("members") or []
    out = []
    for m in members:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict) and m.get("graph"):
            out.append(m["graph"])
    return out


def _open_dataset_update(iri: str, add: bool, timestamp: str) -> None:
    try:
        members = _open_members()
    except qc.QuipuWriteRejected:
        # `show` on an absent dataset is an error on some builds; an absent
        # dataset simply has no members yet.
        members = []
    members = [m for m in members if m != iri]
    if add:
        members.append(iri)
    if members:
        qc.post(
            "/datasets",
            {
                "action": "create",
                "name": OPEN_DATASET,
                "members": members,
                "timestamp": timestamp,
                "actor": "shuttle-windows",
            },
        )
    else:
        qc.post("/datasets", {"action": "remove", "name": OPEN_DATASET})


def mark_frozen(yyyymm: str, timestamp: str) -> None:
    """Drop a window from the open dataset after `quipu graph freeze` — the
    frozen dataset (`urn:quipu:dataset:frozen`) carries it from here."""
    _open_dataset_update(window_iri(yyyymm), add=False, timestamp=timestamp)
