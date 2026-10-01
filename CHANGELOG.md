# Changelog

All notable changes to shuttle. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and shuttle uses
[semantic versioning](https://semver.org/). Each release's notes are its
section here; CI refuses a version with no section (see
[Releasing](https://scbrown.github.io/shuttle/releasing.html)).

## [Unreleased]

### Added

- `CHANGELOG.md`, and a CI check that the version in `shuttle/__init__.py` has
  a section here. The release lane publishes that section as the release notes.
- An mdBook (`book.toml`, `docs/`) published to GitHub Pages.

## [0.3.0] - 2026-10-01

### Added

- Seed steps: a transition may declare `seed = {title, labels}` and
  `on_abandon`, so a [seeds](https://github.com/scbrown/seeds) work item
  performs the step.
- `shuttle reconcile`: creates each legal seed step's seed with a keyed,
  idempotent `sd create --workflow-run --step --visit`, and advances the run
  when the seed closes. Outcome `done` advances, signed, with the seed id,
  outcome and close time as evidence. Other outcomes take `on_abandon`. With no
  `on_abandon` the run is flagged (exit 1), never advanced silently. Reconcile
  holds an exclusive lock on the state dir.

## [0.2.0] - 2026-09-05

The first version that can be deployed unattended.

### Added

- `shuttle version` and `--version`, printing one parseable line
  (`shuttle <version>`). Deploy tooling reads it.
- A tag-triggered release lane: it refuses a tag that disagrees with the
  package, runs the suite, installs the built wheel clean and checks its
  version, and publishes the wheel, sdist and `checksums.txt`.

### Changed

- The version is single-sourced from `shuttle/__init__.py`.

### Fixed

- Export declares each window's workflows in that window, so runs in a month
  after their workflow was defined are no longer refused by SHACL, and one bad
  record is quarantined instead of stopping the whole drain.

## [0.1.0] - 2026-08-24

Never published as a release. The hand-installed first build: signed runs,
windowed export into quipu, and freezable history.

[Unreleased]: https://github.com/scbrown/shuttle/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/scbrown/shuttle/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/scbrown/shuttle/releases/tag/v0.2.0
