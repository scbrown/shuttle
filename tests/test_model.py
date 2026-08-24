"""The state machine: refusals first, then the fold."""

import unittest

from shuttle import model

TRIAGE = {
    "name": "triage",
    "initial": "open",
    "terminal": ["done", "abandoned"],
    "transitions": [
        {"step": "claim", "from": "open", "to": "claimed"},
        {"step": "work", "from": "claimed", "to": "in-progress"},
        {"step": "finish", "from": "in-progress", "to": "done"},
        {"step": "abandon", "from": "claimed", "to": "abandoned"},
        {"step": "abandon", "from": "in-progress", "to": "abandoned"},
    ],
}


def ev(step, frm, to, at="2026-08-01T00:00:00Z"):
    return model.Event(run="r1", step=step, from_state=frm, to_state=to, at=at, agent="a")


class DefinitionTests(unittest.TestCase):
    def test_a_valid_definition_parses(self):
        d = model.parse_definition(TRIAGE)
        self.assertEqual("triage", d.name)
        self.assertEqual({"open", "claimed", "in-progress", "done", "abandoned"}, d.states())

    def test_two_transitions_sharing_step_and_from_are_refused(self):
        """The engine never tiebreaks silently — the quipu dataset-rank rule
        applied to workflow edges."""
        bad = dict(TRIAGE, transitions=TRIAGE["transitions"] + [
            {"step": "claim", "from": "open", "to": "abandoned"}])
        with self.assertRaises(model.WorkflowError) as c:
            model.parse_definition(bad)
        self.assertIn("never tiebreaks", str(c.exception))

    def test_an_edge_out_of_a_terminal_state_is_refused(self):
        bad = dict(TRIAGE, transitions=TRIAGE["transitions"] + [
            {"step": "reopen", "from": "done", "to": "open"}])
        with self.assertRaises(model.WorkflowError) as c:
            model.parse_definition(bad)
        self.assertIn("not terminal", str(c.exception))

    def test_missing_fields_are_refused_with_the_field_named(self):
        for broken, needle in [
            ({}, "name"),
            ({"name": "x"}, "initial"),
            ({"name": "x", "initial": "a"}, "terminal"),
            ({"name": "x", "initial": "a", "terminal": ["b"]}, "transitions"),
        ]:
            with self.assertRaises(model.WorkflowError) as c:
                model.parse_definition(broken)
            self.assertIn(needle, str(c.exception))


class AdvanceTests(unittest.TestCase):
    def setUp(self):
        self.d = model.parse_definition(TRIAGE)

    def test_a_legal_step_yields_its_to_state(self):
        self.assertEqual("claimed", model.validate_advance(self.d, "open", "claim"))

    def test_an_illegal_step_names_what_was_legal(self):
        with self.assertRaises(model.WorkflowError) as c:
            model.validate_advance(self.d, "open", "finish")
        self.assertIn("['claim']", str(c.exception))

    def test_a_terminal_run_refuses_every_step(self):
        with self.assertRaises(model.WorkflowError) as c:
            model.validate_advance(self.d, "done", "claim")
        self.assertIn("terminal", str(c.exception))


class FoldTests(unittest.TestCase):
    def setUp(self):
        self.d = model.parse_definition(TRIAGE)

    def test_the_fold_replays_to_the_final_state(self):
        events = [ev("claim", "open", "claimed"), ev("work", "claimed", "in-progress"),
                  ev("finish", "in-progress", "done")]
        self.assertEqual("done", model.fold_transitions(self.d, events))

    def test_a_reordered_log_is_loud_not_quietly_wrong(self):
        events = [ev("work", "claimed", "in-progress"), ev("claim", "open", "claimed")]
        with self.assertRaises(model.WorkflowError) as c:
            model.fold_transitions(self.d, events)
        self.assertIn("corrupt", str(c.exception))

    def test_an_event_claiming_the_wrong_to_state_is_loud(self):
        events = [ev("claim", "open", "abandoned")]
        with self.assertRaises(model.WorkflowError) as c:
            model.fold_transitions(self.d, events)
        self.assertIn("corrupt", str(c.exception))


if __name__ == "__main__":
    unittest.main()
