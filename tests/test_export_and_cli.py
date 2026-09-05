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
        # Five knots for four records: the run started in a different month
        # from the one the workflow was defined in, so the run's window gets
        # the definition re-asserted into it. Without that the window does not
        # declare the workflow its runs point at, and SHACL refuses the lot.
        self.assertEqual(5, len(knots))
        # Every episode targets the START month's window, so a frozen window
        # never splits a run.
        for kn in knots[1:]:
            self.assertIn("2026-08", kn["graph"])
        # The property that matters, asserted directly rather than via a count:
        # the run's window declares the workflow the run is `runOf`.
        run_window = [kn for kn in knots if "2026-08" in kn["graph"]]
        self.assertTrue(
            any(
                "a aegis:WorkflowDefinition" in kn["turtle"]
                and "urn:shuttle:workflow:triage" in kn["turtle"]
                for kn in run_window
            ),
            "the run's window must declare its workflow",
        )
        self.assertEqual(4, state.watermark(Path(self._state.name)))

        # A second export is a no-op — at-least-once, idempotent.
        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]):
            code, out = self.run_cli("export")
        self.assertIn("exported 0 of 4", out)

    def test_a_new_month_declares_the_workflow_it_inherited(self):
        """THE aegis-pll3fx REGRESSION.

        A workflow is defined once and run for months. The definition exports
        into the window of the month it was RECORDED; runs export into the
        window of the month they RAN. Those coincide exactly once — the month
        the workflow is introduced. Every month after, a run asserted
        `runOf` against a workflow the new window had never declared, SHACL's
        class constraint refused it, and one refusal stopped the whole drain.

        Measured consequence: every shuttle definition was recorded in
        2026-08, so at 00:00 on 2026-09-01 every lane stopped at once and the
        shared record of ~78 apply-lane runs a month went empty while the local
        log kept accepting advances.
        """
        self.run_cli("define", self._define())
        # Two runs of the SAME workflow in two different later months.
        for run, at in (("r-aug", "2026-08-15T00:00:00Z"), ("r-sep", "2026-09-01T00:00:00Z")):
            self.run_cli("start", "triage", run, "--agent", "agent-a", "--at", at)
        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]):
            code, _ = self.run_cli("export")
        self.assertEqual(0, code)

        knots = [b for p, b in self.posts if p == "/knot"]
        for month in ("2026-08", "2026-09"):
            declared = [
                kn for kn in knots
                if month in kn["graph"] and "a aegis:WorkflowDefinition" in kn["turtle"]
            ]
            self.assertTrue(
                declared,
                f"the {month} window must declare the workflow its runs point at",
            )
            # Exactly once per window: the definition is re-asserted where it
            # is needed, not once per record.
            self.assertEqual(1, len(declared), f"{month} re-declared per record")

    def test_a_record_that_cannot_post_is_quarantined_not_left_blocking(self):
        """One bad record must not cost the window.

        The watermark is linear, so before the quarantine a record that could
        not be posted stopped the drain with every LATER record — including
        every healthy one — stuck behind it. That is how one node from
        2026-09-01 held five days of every lane.
        """
        self.run_cli("define", self._define())
        for run, at in (("r1", "2026-08-01T00:00:00Z"), ("r2", "2026-08-02T00:00:00Z")):
            self.run_cli("start", "triage", run, "--agent", "agent-a", "--at", at)

        real_post = qc.post.side_effect

        def refuse_r1(path, body):
            if path == "/knot" and "run:r1" in body.get("turtle", ""):
                raise RuntimeError("/knot REFUSED by validation: 1 violation(s)")
            return real_post(path, body)

        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]), \
                mock.patch.object(qc, "post", side_effect=refuse_r1):
            code, out = self.run_cli("export")

        # Nonzero, because a skip nobody is told about is silent loss.
        self.assertEqual(1, code)
        # The healthy record behind it still landed.
        self.assertIn("exported 2 of 3", out)
        quarantined = state.quarantine(Path(self._state.name))
        self.assertEqual(1, len(quarantined))
        self.assertEqual("r1", quarantined[0]["run"])
        self.assertIn("REFUSED", quarantined[0]["error"])
        # The watermark cleared the whole log, so the next drain is not stuck.
        self.assertEqual(3, state.watermark(Path(self._state.name)))

    def test_strict_restores_stop_on_first_failure(self):
        self.run_cli("define", self._define())
        self.run_cli("start", "triage", "r1", "--agent", "agent-a",
                     "--at", "2026-08-01T00:00:00Z")
        real_post = qc.post.side_effect

        def refuse_all_runs(path, body):
            if path == "/knot" and "WorkflowRun" in body.get("turtle", ""):
                raise RuntimeError("nope")
            return real_post(path, body)

        with mock.patch.object(qc, "probe_graph_kinds", return_value=[]), \
                mock.patch.object(qc, "post", side_effect=refuse_all_runs):
            with self.assertRaises(RuntimeError):
                export.export(Path(self._state.name), strict=True)

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


class VersionContractTests(unittest.TestCase):
    """`shuttle version` is a deploy interface, not a convenience.

    scripts/deploy-shuttle-cli.sh (in the operator repo) resolves both the
    running build and the candidate build by parsing field 2 of this line, and
    refuses to promote a candidate whose reported version disagrees with the
    release it was downloaded from. So the OUTPUT SHAPE is load-bearing:
    changing it silently disarms the promotion gate rather than breaking it
    loudly. These tests exist to make that change loud.
    """

    def test_version_prints_one_parseable_line(self):
        import shuttle

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["version"])
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip()
        self.assertEqual(line, f"shuttle {shuttle.__version__}")
        # Field 2 is what the actuator reads; keep it a bare version.
        self.assertEqual(line.split()[1], shuttle.__version__)
        self.assertEqual(len(line.splitlines()), 1)

    # There is deliberately NO test here cross-checking shuttle.__version__
    # against importlib.metadata. One was written and removed the same hour:
    # in a source tree it resolves a stale `shuttle.egg-info/`, so it compares
    # the working tree against whenever the tree was last built and fails on a
    # version bump for a reason that has nothing to do with the bump. It passed
    # in CI only because `pip install -e .` regenerates that metadata first --
    # green where the invariant is unnecessary, red where it is merely stale.
    #
    # The invariant is real, so it is asserted where it can be asserted
    # honestly: the release lane builds the wheel, installs it into a clean
    # venv, and asks it its version. That is the artifact anyone actually
    # deploys, and a mismatch there blocks the release.



if __name__ == "__main__":
    unittest.main()
