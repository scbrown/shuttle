"""Per-agent ed25519 signing — the identity on every transition.

Mirrors quipu `src/signing.rs`'s custody exactly: one keypair per agent in a
host file created 0600, auto-generated on first use, never printed. The
private key never leaves the host; the PUBLIC key is registered by a HUMAN
as an `aegis:VerifierRegistration` fact in a `dataKind=identity` graph —
shuttle never registers its own keys, which is what keeps the trust root
human-owned (the quipu governance rule: quipu never self-registers either).

What is signed: the canonical transition message

    shuttle-transition-v1|{run_iri}|{step}|{from_state}|{to_state}|{at}|{agent}

— deterministic field order, re-derivable from the exported facts alone
(quipu's `verdict_message` discipline), so any consumer can re-check a
signature from the graph without shuttle present. The signature travels as
an ordinary fact on the TransitionEvent, so it survives a deep freeze;
the registrations live in the identity graph, which is never frozen.

v1 limits, stated like signing.rs states its own: no rotation ceremony (a
new key is a new registration; old signatures verify against the old one),
and the registration graph is not itself signed.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class SigningError(RuntimeError):
    """A key or signature operation failed. Raised, never a soft verdict."""


def key_dir() -> Path:
    """`$SHUTTLE_KEY_DIR` or `~/.config/shuttle/keys`."""
    d = os.environ.get("SHUTTLE_KEY_DIR")
    if d:
        return Path(d)
    return Path.home() / ".config" / "shuttle" / "keys"


def load_or_generate(agent: str, root: Path | None = None) -> Ed25519PrivateKey:
    """The agent's private key, generating it 0600 on first use.

    The signing.rs shape: generation is silent and idempotent; a key that
    exists is loaded, never regenerated (regenerating would orphan every
    signature already in the graph).
    """
    if not agent or "/" in agent or agent.startswith("."):
        raise SigningError(f"agent name {agent!r} is not a safe key filename")
    root = root or key_dir()
    path = root / f"{agent}.pk8"
    if path.exists():
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (ValueError, OSError) as exc:
            raise SigningError(f"cannot load key {path}: {exc}") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise SigningError(f"{path} is not an ed25519 key")
        return key
    root.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def public_key_hex(agent: str, root: Path | None = None) -> str:
    """The agent's public key as hex — what a human registers."""
    key = load_or_generate(agent, root)
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw.hex()


def transition_message(
    run_iri: str, step: str, from_state: str, to_state: str, at: str, agent: str
) -> bytes:
    """The canonical signed message. Any `|` inside a field would make the
    encoding ambiguous, so fields containing one are refused — IRIs, step
    names, states, timestamps and agent names never legitimately carry it."""
    fields = [run_iri, step, from_state, to_state, at, agent]
    for f in fields:
        if "|" in f:
            raise SigningError(f"field {f!r} contains '|'; the canonical message would be ambiguous")
    return "|".join(["shuttle-transition-v1", *fields]).encode()


def sign_transition(
    agent: str,
    run_iri: str,
    step: str,
    from_state: str,
    to_state: str,
    at: str,
    root: Path | None = None,
) -> str:
    """Hex signature over the canonical message, by the agent's own key."""
    key = load_or_generate(agent, root)
    return key.sign(
        transition_message(run_iri, step, from_state, to_state, at, agent)
    ).hex()


def verify_transition(
    public_key_hex_str: str,
    signature_hex: str,
    run_iri: str,
    step: str,
    from_state: str,
    to_state: str,
    at: str,
    agent: str,
) -> bool:
    """True iff the signature verifies. False, never an exception, for a bad
    signature — the caller's question is a verdict, and 'could not check'
    (malformed key/signature hex) IS an exception, distinguished on purpose."""
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex_str))
        sig = bytes.fromhex(signature_hex)
    except ValueError as exc:
        raise SigningError(f"malformed key or signature hex: {exc}") from exc
    try:
        key.verify(sig, transition_message(run_iri, step, from_state, to_state, at, agent))
        return True
    except Exception:
        return False


def registration_turtle(agent: str, workflow_iri: str, root: Path | None = None) -> str:
    """The `aegis:VerifierRegistration` a HUMAN loads into the identity graph.

    Printed for a person to review and load — shuttle never posts this
    itself. The vocabulary is quipu-owned aegis terms, the same shape quipu's
    governance router verifies decisions against.
    """
    pk = public_key_hex(agent, root)
    return f"""@prefix aegis: <http://aegis.gastown.local/ontology/> .

<urn:shuttle:registration:{agent}> a aegis:VerifierRegistration ;
    aegis:verifier  "{agent}" ;
    aegis:attests   <{workflow_iri}> ;
    aegis:publicKey "{pk}" .
"""
