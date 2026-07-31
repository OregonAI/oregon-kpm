#!/usr/bin/env python3
"""Every non-catch-all CODEOWNERS rule must match at least one tracked path.

WHY. GitHub silently ignores a CODEOWNERS rule whose PATH matches nothing, exactly as it
silently ignores one whose OWNER does not resolve. The file then reads as per-area review
coverage and enforces none of it — worse than having no CODEOWNERS, because you believe
you are protected.

Not hypothetical. A code review of OregonAI/executive-regulatory-frameworks found
`/agencies/das/` in its CODEOWNERS, a directory that does not exist there (its slug is
the full agency name). The DAS tree was unowned and eight other agencies fell through to
`*` while appearing covered. Nothing anywhere reported it.

Matching is delegated to `git ls-files -i -c -X`, i.e. git's own gitignore engine, rather
than reimplemented with fnmatch. CODEOWNERS uses gitignore pattern syntax, and the
differences are the ones that bite: `/src/` is anchored and `src/` is not, a trailing
slash means directory-only, `docs/*` matches direct children only. A hand-rolled matcher
that gets one of those wrong produces a guard that passes on a rule GitHub is ignoring —
the precise failure this script exists to end.

Runnable by a contributor, not only by CI, deliberately:

    python3 .github/scripts/check-codeowners-paths.py

Exit 0 = every rule matches something. Exit 1 = at least one rule is dead.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

CODEOWNERS_CANDIDATES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("ERROR: not inside a git repository")
    return Path(out.stdout.strip())


def find_codeowners(root: Path) -> Path:
    for rel in CODEOWNERS_CANDIDATES:
        p = root / rel
        if p.is_file():
            return p
    sys.exit(f"ERROR: no CODEOWNERS file found (looked in {', '.join(CODEOWNERS_CANDIDATES)})")


def rules(path: Path) -> list[tuple[int, str, list[str]]]:
    """(line number, pattern, owners) for each rule line."""
    out = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        out.append((n, parts[0], parts[1:]))
    return out


def matches_anything(root: Path, pattern: str) -> bool:
    """True if `pattern` selects at least one TRACKED file.

    Tracked, not on-disk: a rule pointing at a build output or a gitignored directory
    owns nothing reviewable, and a PR can never touch it."""
    with tempfile.NamedTemporaryFile("w", suffix=".exclude", delete=False) as fh:
        fh.write(pattern + "\n")
        exclude = fh.name
    try:
        got = subprocess.run(["git", "ls-files", "-i", "-c", "-X", exclude],
                             cwd=root, capture_output=True, text=True)
        return bool(got.stdout.strip())
    finally:
        Path(exclude).unlink(missing_ok=True)


def main() -> int:
    root = repo_root()
    path = find_codeowners(root)
    print(f"checking path rules in {path.relative_to(root)}")

    checked = dead = 0
    for n, pattern, owners in rules(path):
        if not owners:
            print(f"::error file={path.relative_to(root)},line={n}::rule "
                  f"{pattern!r} has no owner — GitHub treats this as UNSETTING ownership "
                  f"for that path, which is almost never what was meant")
            dead += 1
            continue
        # `*` is the catch-all and matches by definition; checking it proves nothing.
        if pattern == "*":
            print(f"  ok   line {n}: * (catch-all)")
            continue
        checked += 1
        if matches_anything(root, pattern):
            print(f"  ok   line {n}: {pattern}")
        else:
            print(f"::error file={path.relative_to(root)},line={n}::CODEOWNERS rule "
                  f"{pattern!r} matches no tracked file in this repository. GitHub "
                  f"IGNORES it silently, so the paths it claims to cover fall through "
                  f"to the catch-all while appearing owned. Fix the path or delete the "
                  f"rule.")
            dead += 1

    if dead:
        print(f"\nFAILED: {dead} CODEOWNERS rule(s) enforce nothing.")
        return 1
    # Say the count out loud. A check that silently examined zero rules and exited 0 is
    # indistinguishable in a CI log from one that examined twenty and passed.
    print(f"\nOK: {checked} path rule(s) checked, all match tracked files."
          if checked else
          "\nOK: no path-scoped rules to check — this CODEOWNERS is catch-all only. "
          "Nothing was verified beyond the owners themselves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
