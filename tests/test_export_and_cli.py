"""The export shapes and the CLI fold — no network: quipu calls captured."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from shuttle import cli, export, quipu_client as qc, state, windows


class TurtleShapeTests(unittest.TestCase):
    def test_a_transition_exports_event_signature_and_reasserted_state(self):
        rec = {
            "type": "transition", "run": "r1", "definition": "triage",
            "window": "2026-08", "step": "claim", "from": "open",
            "to": "claimed", "at": "2026-08-01T00:00:00Z",
            "agent": "agent-a", "signature": "ab" * 64,
        }
        t = export.transition_turtle(rec, 3)
        self.assertIn("aegis:TransitionEvent", t)
        self.assertIn('aegis:signature "' + "ab" * 64, t)
        self.assertIn('aegis:fromState "open"', t)
        self.assertIn('aegis:toState "claimed"', t)
        # The re-asserted derived state rides along; the event is the truth.
        self.assertIn('aegis:currentState "claimed"', t)
        self.assertIn('aegis:sourceKind "observed"', t)

    def test_a_start_exports_the_run_shape(self):
        rec = {"type": "start", "run": "r1", "definition": "triage",
               "state": "open", "at": "2026-08-01T00:00:00Z", "agent": "a"}
        t = export.start_turtle(rec)
        self.assertIn("aegis:WorkflowRun", t)
        self.assertIn("urn:shuttle:workflow:triage", t)


class EnvIsolatedCase(unittest.TestCase):
    """A temp state dir, a temp key dir, and captured quipu POSTs."""

    def setUp(self):
        self._state = tempfile.TemporaryDirectory()
        self._keys = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            "os.environ",
            {"SHUTTLE_STATE_DIR": self._state.name, "SHUTTLE_KEY_DIR": self._keys.name},
        )
        self._env.start()
        self.posts: list[tuple[str, dict]] = []

        def fake_post(path, body):
            self.posts.append((path, body))
            if path == "/datasets" and body.get("action") == "show":
                return {"members": []}
            return {"ok": True}

        self._post = mock.patch.object(qc, "post", side_effect=fake_post)
        self._post.start()

    def tearDown(self):
        self._post.stop()
        self._env.stop()
        self._state.cleanup()
        self._keys.cleanup()

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()


class CliFlowTests(EnvIsolatedCase):
    def _define(self):
        p = Path(self._state.name) / "triage.json"
        p.write_text(json.dumps({
            "name": "triage", "initial": "open", "terminal": ["done"],
            "transitions": [
                {"step": "claim", "from": "open", "to": "claimed"},
                {"step": "finish", "from": "claimed", "to": "done"},
            ],
        }))
        return str(p)

    def test_the_full_flow_folds_signs_and_exports(self):
        code, _ = self.run_cli("define", self._define())
        self.assertEqual(0, code)
        code, _ = self.run_cli("start", "triage", "r1", "--agent", "agent-a",
                               "--at", "2026-08-01T00:00:00Z")
        self.assertEqual(0, code)
        code, out = self.run_cli("advance", "r1", "--step", "claim", "--agent", "agent-a")
        self.assertEqual(0, code)
        self.assertIn("open -[claim]-> claimed", out)
        code, out = self.run_cli("advance", "r1", "--step", "finish", "--agent", "agent-a")
        self.assertIn("(terminal)", out)
        code, out = self.run_cli("status", "r1")
        self.assertIn("done\tterminal", out)

        # An illegal step refuses, naming what was legal.
        code, _ = self.run_cli("advance", "r1", "--step", "claim", "--agent", "agent-a")
        self.assertEqual(2, code)

        # Export drains everything; the watermark advances to the log length.
        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]):
            code, out = self.run_cli("export")
        self.assertEqual(0, code)
        self.assertIn("exported 4 of 4", out)
        knots = [b for p, b in self.posts if p == "/knot"]
        self.assertEqual(4, len(knots))
        # Every episode targets the START month's window, so a frozen window
        # never splits a run.
        for kn in knots[1:]:
            self.assertIn("2026-08", kn["graph"])
        self.assertEqual(4, state.watermark(Path(self._state.name)))

        # A second export is a no-op — at-least-once, idempotent.
        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]):
            code, out = self.run_cli("export")
        self.assertIn("exported 0 of 4", out)

    def test_export_refuses_a_log_whose_signature_no_longer_verifies(self):
        self.run_cli("define", self._define())
        self.run_cli("start", "triage", "r1", "--agent", "agent-a",
                     "--at", "2026-08-01T00:00:00Z")
        self.run_cli("advance", "r1", "--step", "claim", "--agent", "agent-a")
        # Tamper: flip the to-state in the log without re-signing.
        log = Path(self._state.name) / "runs.jsonl"
        lines = log.read_text().splitlines()
        rec = json.loads(lines[-1])
        rec["to"] = "done"
        lines[-1] = json.dumps(rec)
        log.write_text("\n".join(lines) + "\n")
        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]):
            code, _ = self.run_cli("export")
        self.assertEqual(2, code, "a tampered log must refuse to export")

    def test_freeze_window_posts_the_freeze_and_updates_the_open_dataset(self):
        code, out = self.run_cli("freeze-window", "2026-07", "--agent", "op")
        self.assertEqual(0, code)
        paths = [p for p, _ in self.posts]
        self.assertIn("/graph/freeze", paths)
        self.assertIn("urn:quipu:dataset:frozen", out)


class WindowSchemeTests(unittest.TestCase):
    def test_the_iri_scheme_matches_the_camayoc_pin(self):
        """camayoc tests/test_planes_2d.py pins the same string — this is the
        other half of the no-drift contract."""
        self.assertEqual(
            "https://camayoc.local/window/shuttle/runs/2026-08",
            windows.window_iri("2026-08"),
        )

    def test_months_are_validated(self):
        for bad in ["2026-13", "202608", "aug"]:
            with self.assertRaises(ValueError):
                windows.window_iri(bad)


if __name__ == "__main__":
    unittest.main()
