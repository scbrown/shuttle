#!/usr/bin/env python3
"""The changelog has a section for the package version, and can print it.

    check-changelog.py            exit 0 if CHANGELOG.md has `## [<version>]`
    check-changelog.py --notes V  print version V's section (release notes)

The version is read from shuttle/__init__.py, the single source the release
lane and `shuttle version` also use. A bump without a section fails CI, so a
release can never ship with notes that do not describe it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^## \[(?P<v>[^\]]+)\]", re.MULTILINE)


def package_version() -> str:
    text = (ROOT / "shuttle" / "__init__.py").read_text()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        sys.exit("shuttle/__init__.py has no __version__")
    return m.group(1)


def section(changelog: str, version: str) -> str | None:
    """The body under `## [version]`, up to the next `## [` or link refs."""
    heads = list(HEADING.finditer(changelog))
    for i, h in enumerate(heads):
        if h.group("v") != version:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(changelog)
        body = changelog[h.end():end]
        body = body.split("\n", 1)[1] if "\n" in body else ""
        # Drop the trailing link-reference block if this is the last section.
        body = re.split(r"^\[[^\]]+\]: ", body, maxsplit=1, flags=re.MULTILINE)[0]
        return body.strip()
    return None


def main(argv: list[str]) -> int:
    changelog = (ROOT / "CHANGELOG.md").read_text()
    if argv[:1] == ["--notes"] and len(argv) == 2:
        body = section(changelog, argv[1].removeprefix("v"))
        if not body:
            print(f"CHANGELOG.md has no non-empty section for {argv[1]}", file=sys.stderr)
            return 1
        print(body)
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    version = package_version()
    # The same test --notes applies at tag time: an EMPTY section fails here,
    # on the PR, instead of after the version is already on main.
    if not section(changelog, version):
        print(
            f"CHANGELOG.md has no non-empty '## [{version}]' section for "
            f"shuttle/__init__.py's version. Move the Unreleased entries under it.",
            file=sys.stderr,
        )
        return 1
    print(f"changelog: section [{version}] present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
