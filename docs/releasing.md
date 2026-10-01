# Releasing

The version is single-sourced from `shuttle/__init__.py` (`__version__`).
`pyproject.toml` reads it, so a tag, the built wheel and `shuttle version`
cannot disagree.

1. Move the `## [Unreleased]` entries in `CHANGELOG.md` under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading.
2. Set `__version__ = "X.Y.Z"` in the same PR. CI refuses a version that has
   no matching changelog section (`scripts/check-changelog.py`).
3. Merge, then tag the merge commit `vX.Y.Z` and push the tag.

The `release` workflow then:

- refuses a tag that disagrees with `__version__`;
- runs the suite;
- builds the wheel and sdist, installs the wheel clean and checks it reports
  the tagged version;
- publishes the release with `checksums.txt`, using that version's changelog
  section as the release notes.

## After a release

- The unattended actuator on the fleet host installs the new release on its
  next tick (checksum-verified, command-tested, atomically promoted).
- caboodle installs only the version its member manifest pins. Move the pin
  with `caboodle bump-member members/shuttle.toml` in a caboodle PR.
