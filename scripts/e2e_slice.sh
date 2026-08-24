#!/usr/bin/env bash
# The cross-repo acceptance: a live quipu, a signed run, a freeze, and the
# same rows on both sides of it.
#
#   scripts/e2e_slice.sh [path-to-quipu-binary] [path-to-quipu-server-binary]
#
# Needs: python3 (+cryptography), a quipu build with graph kinds + deep
# freeze (>= the 2026-08-24 main), and a free port.
set -euo pipefail

QUIPU="${1:-quipu}"
QUIPU_SERVER_BIN="${2:-quipu-server}"
PORT="${E2E_PORT:-3939}"
WORK="$(mktemp -d)"
trap 'kill "${SERVER_PID:-0}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

export QUIPU_SERVER_URL="http://127.0.0.1:${PORT}"
export QUIPU_SERVER="$QUIPU_SERVER_URL"
export SHUTTLE_STATE_DIR="$WORK/state"
export SHUTTLE_KEY_DIR="$WORK/keys"
DB="$WORK/e2e.db"

say() { printf '\n== %s\n' "$*"; }
fail() { printf 'E2E FAIL: %s\n' "$*" >&2; exit 1; }

query() { # query <json-body> -> stdout
    curl -sS "$QUIPU_SERVER_URL/query" -H 'Content-Type: application/json' -d "$1"
}

say "start quipu-server on :$PORT"
"$QUIPU_SERVER_BIN" --db "$DB" --bind "127.0.0.1:${PORT}" >"$WORK/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 50); do
    curl -sf "$QUIPU_SERVER_URL/health" >/dev/null 2>&1 && break
    sleep 0.2
done
curl -sf "$QUIPU_SERVER_URL/health" >/dev/null || fail "server did not come up ($(tail -3 "$WORK/server.log"))"

say "capability probe"
curl -sf "$QUIPU_SERVER_URL/graphs" >/dev/null || fail "GET /graphs missing — quipu predates graph kinds"

say "keys init + HUMAN registration into the identity graph"
python3 -m shuttle.cli keys init agent-a --workflow triage | tee "$WORK/keys.txt"
REG_TTL="$(awk '/@prefix/,0' "$WORK/keys.txt")"
curl -sS "$QUIPU_SERVER_URL/graph/create" -H 'Content-Type: application/json' \
    -d '{"graph": "urn:shuttle:graph:identity"}' >/dev/null
curl -sS "$QUIPU_SERVER_URL/graph/label" -H 'Content-Type: application/json' \
    -d '{"graph": "urn:shuttle:graph:identity", "kind": "identity", "timestamp": "2026-08-24T00:00:00Z"}' >/dev/null
python3 - "$QUIPU_SERVER_URL" <<PYEOF
import json, sys, urllib.request
ttl = '''$REG_TTL'''
body = {"turtle": ttl, "graph": "urn:shuttle:graph:identity",
        "timestamp": "2026-08-24T00:00:00Z", "actor": "stiwi"}
req = urllib.request.Request(sys.argv[1] + "/knot", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req))
assert resp.get("conforms") is not False, resp
PYEOF

say "define -> start -> advance x2 (signed) -> complete"
python3 -m shuttle.cli define examples/triage.json
python3 -m shuttle.cli start triage run-e2e --agent agent-a --at 2026-08-24T01:00:00Z
python3 -m shuttle.cli advance run-e2e --step claim  --agent agent-a
python3 -m shuttle.cli advance run-e2e --step work   --agent agent-a
python3 -m shuttle.cli advance run-e2e --step finish --agent agent-a
python3 -m shuttle.cli status run-e2e | grep -q terminal || fail "run did not reach terminal"

say "export into the window"
python3 -m shuttle.cli export

WINDOW="https://camayoc.local/window/shuttle/runs/2026-08"
SPARQL='SELECT ?ev ?sig WHERE { ?ev <http://aegis.gastown.local/ontology/inRun> <urn:shuttle:run:run-e2e> ; <http://aegis.gastown.local/ontology/signature> ?sig }'

say "the window holds 3 signed transitions"
HOT=$(query "{\"query\": \"$SPARQL\", \"graph\": \"$WINDOW\"}")
echo "$HOT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["count"]==3, d' || fail "expected 3 transitions pre-freeze"

say "consumer-side signature verification (hot)"
python3 -m shuttle.cli verify run-e2e

say "freeze the window"
python3 -m shuttle.cli freeze-window 2026-08 --agent stiwi

say "identical rows via the frozen dataset AND include_kinds"
COLD=$(query "{\"query\": \"$SPARQL\", \"graph\": \"urn:quipu:dataset:frozen\"}")
echo "$COLD" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["count"]==3, d' || fail "frozen dataset lost rows"
KIND=$(query "{\"query\": \"$SPARQL\", \"include_kinds\": [\"archive\"]}")
echo "$KIND" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["count"]==3, d' || fail "include_kinds lost rows"

say "signatures still verify against the FROZEN window"
python3 -m shuttle.cli verify run-e2e

say "E2E SLICE PASSES"
