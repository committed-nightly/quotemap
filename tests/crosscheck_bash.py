#!/usr/bin/env python3
"""Cross-check the scanner against ``bash -n`` over real shell scripts.

The argument: if quotemap's context stack is wrong anywhere in a file — a
quote opened that should not have been, a ``$( )`` closed one character
early — the stack will almost always end up unbalanced, and quotemap will
report an unterminated construct. So a file that ``bash -n`` accepts and
quotemap calls unterminated is a scanner bug, and this looks for one across
every script it can find.

It is a smoke test with a large surface, not a proof: a mistake that happens
to rebalance is invisible here. The unit tests are where the semantics are
pinned down, and ``test_against_bash.py`` is where the verdicts are.

Usage: crosscheck_bash.py [DIR ...]
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from quotemap import scan  # noqa: E402

DEFAULT_DIRS = ["/usr/share", "/etc", "/usr/bin", "/lib"]
SUFFIXES = {".sh", ".bash"}
LIMIT = 500


def candidates(roots: list[str]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for root in roots:
        base = pathlib.Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if len(found) >= LIMIT:
                return found
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix in SUFFIXES or "bash-completion" in path.parts:
                found.append(path)
    return found


def main(argv: list[str]) -> int:
    roots = argv[1:] or DEFAULT_DIRS
    files = candidates(roots)
    if not files:
        print(f"no shell scripts found under {roots}", file=sys.stderr)
        return 2

    checked = crashes = disagreements = 0
    for path in files:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not source.strip():
            continue

        accepted = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True
        ).returncode == 0

        try:
            result = scan(source, strict=False)
        except Exception as exc:  # noqa: BLE001 - a crash is the finding
            crashes += 1
            print(f"CRASH {path}: {type(exc).__name__}: {exc}")
            continue

        checked += 1
        if len(result.chars) != len(source):
            crashes += 1
            print(f"LOST CHARACTERS {path}: {len(result.chars)} of {len(source)}")
            continue

        if accepted and result.unterminated:
            disagreements += 1
            kind, line, col = result.unterminated[0]
            print(f"DISAGREE {path}: bash -n accepts it, quotemap says "
                  f"unterminated {kind} at {line}:{col}")

    print(f"\nchecked {checked} scripts: {crashes} crashes, "
          f"{disagreements} disagreements with bash -n")
    return 1 if (crashes or disagreements) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
