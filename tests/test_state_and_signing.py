"""The outbox and the keys: append-only, watermark-forward, 0600, verifiable."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from shuttle import signing, state


class StateTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_append_returns_sequence_numbers_in_order(self):
        self.assertEqual(0, state.append({"type": "a"}, self.root))
        self.assertEqual(1, state.append({"type": "b"}, self.root))
        self.assertEqual(["a", "b"], [r["type"] for r in state.records(self.root)])

    def test_a_record_without_a_type_is_refused(self):
        with self.assertRaises(state.StateError):
            state.append({"run": "r1"}, self.root)

    def test_a_malformed_line_is_loud_never_skipped(self):
        state.append({"type": "a"}, self.root)
        with (self.root / "runs.jsonl").open("a") as f:
            f.write("not json\n")
        with self.assertRaises(state.StateError) as c:
            state.records(self.root)
        self.assertIn(":2", str(c.exception))

    def test_the_watermark_never_moves_backward(self):
        state.advance_watermark(3, self.root)
        self.assertEqual(3, state.watermark(self.root))
        with self.assertRaises(state.StateError):
            state.advance_watermark(2, self.root)
        state.advance_watermark(3, self.root)  # idempotent re-set is fine


class SigningTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_keys_generate_0600_and_reload_stable(self):
        k1 = signing.load_or_generate("agent-a", self.root)
        mode = stat.S_IMODE(os.stat(self.root / "agent-a.pk8").st_mode)
        self.assertEqual(0o600, mode)
        k2 = signing.load_or_generate("agent-a", self.root)
        self.assertEqual(
            k1.private_bytes_raw(), k2.private_bytes_raw(),
            "a key that exists is loaded, never regenerated — regeneration "
            "orphans every signature already in the graph",
        )

    def test_an_unsafe_agent_name_is_refused(self):
        for bad in ["", "../escape", ".hidden", "a/b"]:
            with self.assertRaises(signing.SigningError):
                signing.load_or_generate(bad, self.root)

    def test_sign_verify_round_trip_and_tamper_detection(self):
        args = ("urn:shuttle:run:r1", "claim", "open", "claimed",
                "2026-08-01T00:00:00Z", "agent-a")
        sig = signing.sign_transition("agent-a", *args[:-1], root=self.root)
        pk = signing.public_key_hex("agent-a", self.root)
        self.assertTrue(signing.verify_transition(pk, sig, *args))
        # Any flipped field fails verification.
        tampered = ("urn:shuttle:run:r1", "claim", "open", "done",
                    "2026-08-01T00:00:00Z", "agent-a")
        self.assertFalse(signing.verify_transition(pk, sig, *tampered))
        # The wrong agent's key fails too.
        other = signing.public_key_hex("agent-b", self.root)
        self.assertFalse(signing.verify_transition(other, sig, *args))

    def test_a_pipe_in_any_field_is_refused_not_encoded(self):
        with self.assertRaises(signing.SigningError):
            signing.transition_message("urn:r", "cl|aim", "open", "x", "t", "a")

    def test_malformed_hex_is_could_not_check_not_false(self):
        """'could not check' and 'checked and failed' are opposite verdicts."""
        with self.assertRaises(signing.SigningError):
            signing.verify_transition("zz", "zz", "r", "s", "f", "t", "at", "a")

    def test_the_registration_turtle_names_agent_key_and_workflow(self):
        t = signing.registration_turtle("agent-a", "urn:shuttle:workflow:triage", self.root)
        self.assertIn('aegis:verifier  "agent-a"', t)
        self.assertIn(signing.public_key_hex("agent-a", self.root), t)
        self.assertIn("VerifierRegistration", t)


if __name__ == "__main__":
    unittest.main()
