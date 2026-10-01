# Seed steps

A transition can be performed by a [seeds](https://github.com/scbrown/seeds)
work item instead of by hand:

```json
{"step": "review", "from": "open", "to": "reviewed",
 "seed": {"title": "Review the change", "labels": ["review"]},
 "on_abandon": "reject"}
```

`shuttle reconcile --agent <you>` (on a timer, or by hand) does two things for
every open run:

- For each seed step legal from the run's state, it runs `sd create
  --workflow-run <run> --step <step> --visit <n>`. seeds derives the id from
  that key, so repeating the create returns the same seed. A run that re-enters
  the state gets a new visit and a new seed.
- It reads the seed with `sd show --json`. Outcome `done` advances the run
  through the step, signed, with the seed id, outcome and close time recorded
  as evidence. Any other outcome takes `on_abandon`. With no `on_abandon` the
  run is FLAGGED (exit 1) and never advanced silently.

seeds knows nothing about workflows beyond the link field. A missed tick costs
one tick, not a stuck run. `SHUTTLE_SD` names the `sd` binary; where seeds
stores its work is sd's own configuration.
