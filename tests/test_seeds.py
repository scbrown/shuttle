"""Contract C2 (aegis-w3k75d.4): seed steps, keyed creates, reconcile by outcome."""

import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from shuttle import cli, model, state

# A fake `sd` with seeds' A5 semantics: the id is derived from (run, step,
# visit), so a repeated create returns the same seed, open or closed.
FAKE_SD = textwrap.dedent(
    """\
    #!{python}
    import hashlib, json, sys
    db = {db!r}
    try:
        seeds = json.load(open(db))
    except FileNotFoundError:
        seeds = {{}}
    args = sys.argv[1:]
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    if args[0] == "create":
        key = "|".join([opt("--workflow-run"), opt("--step"), opt("--visit")])
        sid = "sd-w" + hashlib.sha256(key.encode()).hexdigest()[:8]
        seeds.setdefault(sid, {{"id": sid, "title": args[1], "status": "open",
                               "outcome": None, "closed_at": None, "key": key,
                               "labels": (opt("--labels") or "").split(",")}})
        out = seeds[sid]
    elif args[0] == "show":
        out = seeds[args[1]]
    else:
        sys.exit(2)
    json.dump(seeds, open(db, "w"))
    print(json.dumps(out))
    """
)

DEFN = {
    "name": "review",
    "initial": "open",
    "terminal": ["shipped", "dropped"],
    "transitions": [
        {"step": "review", "from": "open", "to": "reviewed",
         "seed": {"title": "Review the change", "labels": ["review"]},
         "on_abandon": "reject"},
        {"step": "reject", "from": "open", "to": "rejected"},
        {"step": "rework", "from": "rejected", "to": "open"},
        {"step": "drop", "from": "rejected", "to": "dropped"},
        {"step": "ship", "from": "reviewed", "to": "shipped",
         "seed": {"title": "Ship it"}},
    ],
}


class SeedReconcileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = tmp / "seeds.json"
        sd = tmp / "sd"
        sd.write_text(FAKE_SD.format(python=sys.executable, db=str(self.db)))
        sd.chmod(sd.stat().st_mode | stat.S_IEXEC)
        (tmp / "state").mkdir()
        (tmp / "keys").mkdir()
        env = {"SHUTTLE_STATE_DIR": str(tmp / "state"), "SHUTTLE_KEY_DIR": str(tmp / "keys"),
               "SHUTTLE_SD": str(sd)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        (tmp / "review.json").write_text(json.dumps(DEFN))
        self.cli("define", str(tmp / "review.json"))
        self.cli("start", "review", "r1", "--agent", "tester")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def seeds(self):
        return json.loads(self.db.read_text()) if self.db.exists() else {}

    def close(self, step, visit, outcome):
        db = self.seeds()
        [sid] = [k for k, v in db.items() if v["key"].endswith(f"|{step}|{visit}")]
        db[sid].update(status="closed", outcome=outcome, closed_at="2026-10-01T04:00:00Z")
        self.db.write_text(json.dumps(db))
        return sid

    def status(self):
        return self.cli("status", "r1")[1].split("\t")[2]

    def transitions(self):
        return [r for r in state.records() if r["type"] == "transition"]

    def test_entering_a_seed_step_creates_one_keyed_seed_and_a_repeat_does_not_duplicate(self):
        for _ in range(3):
            rc, _, _ = self.cli("reconcile", "--agent", "tester")
            self.assertEqual(rc, 0)
        db = self.seeds()
        self.assertEqual(len(db), 1, db)
        [seed] = db.values()
        self.assertEqual(seed["key"], "urn:shuttle:run:r1|review|1")
        self.assertEqual(seed["labels"], ["review"])
        self.assertEqual(self.status(), "open")  # open seed: no advance

    def test_a_done_close_advances_the_run_with_the_seed_as_evidence(self):
        self.cli("reconcile", "--agent", "tester")
        sid = self.close("review", 1, "done")
        rc, out, _ = self.cli("reconcile", "--agent", "tester")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.status(), "reviewed")
        [t] = self.transitions()
        self.assertEqual(t["step"], "review")
        self.assertEqual(t["evidence"], {"seed": sid, "outcome": "done",
                                         "closed_at": "2026-10-01T04:00:00Z"})
        # The new state's seed step is entered in the same tick.
        self.assertEqual(len(self.seeds()), 2)

    def test_an_abandoned_close_takes_the_declared_on_abandon_step(self):
        self.cli("reconcile", "--agent", "tester")
        self.close("review", 1, "abandoned")
        rc, _, _ = self.cli("reconcile", "--agent", "tester")
        self.assertEqual(rc, 0)
        self.assertEqual(self.status(), "rejected")
        self.assertEqual(self.transitions()[0]["step"], "reject")

    def test_reentering_a_state_mints_a_new_visit_not_the_closed_seed(self):
        self.cli("reconcile", "--agent", "tester")
        self.close("review", 1, "abandoned")
        self.cli("reconcile", "--agent", "tester")
        self.cli("advance", "r1", "--step", "rework", "--agent", "tester")
        self.cli("reconcile", "--agent", "tester")
        keys = sorted(v["key"] for v in self.seeds().values())
        self.assertEqual(keys, ["urn:shuttle:run:r1|review|1", "urn:shuttle:run:r1|review|2"])
        self.assertEqual(self.status(), "open")  # visit 2 is open, not the closed visit 1

    def test_an_abandonment_with_no_on_abandon_is_flagged_never_advanced(self):
        self.cli("reconcile", "--agent", "tester")
        self.close("review", 1, "done")
        self.cli("reconcile", "--agent", "tester")  # -> reviewed, ship seed created
        self.close("ship", 1, "failed")
        rc, _, err = self.cli("reconcile", "--agent", "tester")
        self.assertEqual(rc, 1)
        self.assertIn("FLAGGED", err)
        self.assertIn("declares no on_abandon", err)
        self.assertEqual(self.status(), "reviewed")

    def test_a_close_with_no_outcome_is_flagged(self):
        self.cli("reconcile", "--agent", "tester")
        self.close("review", 1, None)
        rc, _, err = self.cli("reconcile", "--agent", "tester")
        self.assertEqual(rc, 1)
        self.assertIn("no recognised outcome", err)
        self.assertEqual(self.status(), "open")

    def test_the_definition_record_keeps_seed_and_on_abandon(self):
        [rec] = [r for r in state.records() if r["type"] == "define"]
        defn = model.parse_definition(rec)
        review = next(t for t in defn.transitions if t.step == "review")
        self.assertEqual(review.seed, {"title": "Review the change", "labels": ["review"]})
        self.assertEqual(review.on_abandon, "reject")

    def test_a_missing_sd_is_a_loud_error(self):
        with mock.patch.dict(os.environ, {"SHUTTLE_SD": "/nonexistent/sd"}):
            rc, _, err = self.cli("reconcile", "--agent", "tester")
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)


class SeedDefinitionTest(unittest.TestCase):
    def defn(self, **review):
        raw = json.loads(json.dumps(DEFN))
        raw["transitions"][0].update(review)
        return raw

    def test_on_abandon_without_a_seed_is_refused(self):
        raw = self.defn()
        del raw["transitions"][0]["seed"]
        with self.assertRaisesRegex(model.WorkflowError, "no seed"):
            model.parse_definition(raw)

    def test_on_abandon_must_be_another_step_legal_from_the_same_state(self):
        for bad in ("ship", "review", "nonsense"):
            with self.assertRaisesRegex(model.WorkflowError, "not another step legal"):
                model.parse_definition(self.defn(on_abandon=bad))

    def test_a_seed_needs_a_title(self):
        with self.assertRaisesRegex(model.WorkflowError, "title"):
            model.parse_definition(self.defn(seed={"labels": ["x"]}))


if __name__ == "__main__":
    unittest.main()
