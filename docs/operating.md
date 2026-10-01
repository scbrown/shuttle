# Install and operate

## Install

Shuttle is a Python package (3.11+) with one dependency, `cryptography`, used
for ed25519 only. Each release publishes a wheel, an sdist and
`checksums.txt`.

| route | use it for |
|---|---|
| `caboodle install --tool shuttle` | a stack box. Installs the release wheel whose SHA-256 caboodle was reviewed with, then `caboodle verify --tool shuttle` runs a real run round trip in a throwaway HOME. |
| the release wheel | anything else: check it against `checksums.txt`, then `pipx install shuttle-<version>-py3-none-any.whl`. |
| `pip install -e .` | development. |

`shuttle version` prints one line, `shuttle <version>`. Deploy tooling parses
the second field, and CI asserts the shape, so treat it as an interface.

## Where state lives

| what | default | override |
|---|---|---|
| the run log (`runs.jsonl`) and export watermark | `~/.local/state/shuttle` | `SHUTTLE_STATE_DIR` |
| per-agent private keys (0600) | `~/.config/shuttle/keys` | `SHUTTLE_KEY_DIR` |

The log is append-only. Never edit it by hand. A bad record is quarantined
by `export`, not rewritten.

## Talking to quipu

| variable | default | meaning |
|---|---|---|
| `QUIPU_SERVER` | `http://localhost:3030` | the quipu `export`, `verify` and `freeze-window` write to and read from |
| `QUIPU_AUTH_TOKEN` / `QUIPU_AUTH_TOKEN_FILE` | unset | bearer for quipu writes |
| `SHUTTLE_ENTITY_NS` | `urn:shuttle:` | IRI namespace for runs, events and workflows |
| `SHUTTLE_OPEN_DATASET` | `urn:shuttle:dataset:open` | dataset holding the open windows |
| `CAMAYOC_PLANE_NS` | `https://camayoc.local/plane/` | namespace of the window graphs |
| `SHUTTLE_SD` | `sd` | the seeds binary `reconcile` runs |

A SHACL refusal from quipu is reported as a refusal. Shuttle never treats it
as a silent success.

## Checking an install

```bash
shuttle version
caboodle verify --tool shuttle     # define -> start -> advance -> status, hermetic
```
