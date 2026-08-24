"""The quipu HTTP client — shantytown's hard-won discipline, inherited.

Three rules, each paid for elsewhere in the stack before being written here:

1. **`CouldNotLook` is never "nothing there".** An unreachable store raises
   `QuipuUnreachable`; only a reachable store's answer is evidence. Returning
   `[]` for a network error is the lie every consumer downstream then
   repeats.
2. **A SHACL refusal hides in a 200.** `/knot` and `/episode` report
   `{"conforms": false, "violations": N}` with NO "error" key — shantytown's
   identity writes silently no-op'd for months on exactly this. Every write
   here checks `conforms` explicitly and raises `QuipuWriteRejected`.
3. **Probe capability, never assume it.** The deployed store can lag source
   (NeuralAmplifier measured GRAPH-errors/FROM-ignored on an older build).
   `probe_graph_kinds()` uses `GET /graphs` — 404 means "this store predates
   graph kinds", which is *cannot tell*, never *no graphs*.

Windowed writes go through `/knot` with its STRICT `graph` param (landed
with camayoc-s0h's fix): the target must already be registered committed,
and an unknown graph refuses rather than being minted or silently dropped.
The old measurement — a `graph` key silently dropped, the write landing in
ROOT at canonical standing — is exactly why strictness is required of the
lane shuttle uses.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


class QuipuUnreachable(RuntimeError):
    """Could not look. NOT evidence about what the store holds."""


class QuipuWriteRejected(RuntimeError):
    """The store looked, and refused — SHACL or policy. The write did NOT land."""


class QuipuCapabilityMissing(RuntimeError):
    """The store predates a surface this client needs. Loud, never zero rows."""


def server() -> str:
    return os.environ.get("QUIPU_SERVER", "http://localhost:3030").rstrip("/")


def _auth_token() -> str | None:
    tok = os.environ.get("QUIPU_AUTH_TOKEN")
    if tok:
        return tok
    path = os.environ.get("QUIPU_AUTH_TOKEN_FILE")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None
    default = Path.home() / ".config" / "quipu" / "token"
    if default.exists():
        try:
            return default.read_text().strip()
        except OSError:
            return None
    return None


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{server()}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    tok = _auth_token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            payload = {}
        return e.code, payload
    except urllib.error.URLError as e:
        raise QuipuUnreachable(
            f"{method} {path}: {e.reason} — could not look; this is not "
            "evidence about the store"
        ) from e


def post(path: str, body: dict) -> dict:
    """POST, refusing loudly on HTTP errors AND on conforms:false-in-a-200."""
    status, payload = _request("POST", path, body)
    if status == 404:
        raise QuipuCapabilityMissing(
            f"{path} returned 404 — this quipu predates the surface. "
            "Refusing to continue as if it succeeded."
        )
    if status >= 400:
        raise QuipuWriteRejected(f"{path} failed: HTTP {status} {payload}")
    if payload.get("conforms") is False:
        raise QuipuWriteRejected(
            f"{path} REFUSED by validation: {payload.get('violations', '?')} "
            f"violation(s), issues: {payload.get('issues', [])[:3]} — a "
            "conforms:false 200 is a refusal, not a success"
        )
    return payload


def query(sparql: str, graph: str | None = None, include_kinds: list[str] | None = None) -> dict:
    """POST /query with explicit scope. Never widens silently: pass `graph`
    (a graph or dataset IRI) or `include_kinds` to compose beyond ROOT."""
    body: dict = {"query": sparql}
    if graph is not None:
        body["graph"] = graph
    if include_kinds:
        body["include_kinds"] = include_kinds
    return post("/query", body)


def probe_graph_kinds() -> list[dict]:
    """`GET /graphs` — the capability probe AND the registry listing.

    Raises `QuipuCapabilityMissing` on 404 (a pre-graph-kinds store) so a
    consumer can say "cannot tell" instead of reading zero rows as truth.
    """
    status, payload = _request("GET", "/graphs")
    if status == 404:
        raise QuipuCapabilityMissing(
            "GET /graphs returned 404 — this quipu predates graph kinds. "
            "Shuttle's windowed facts would read as silent zero rows here; "
            "upgrade the store before exporting."
        )
    if status >= 400:
        raise QuipuUnreachable(f"GET /graphs failed: HTTP {status}")
    return payload.get("graphs", [])
