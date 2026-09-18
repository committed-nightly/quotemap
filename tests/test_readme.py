#!/usr/bin/env python3
"""Run every ```console block in the README and diff it against the file.

A README that has quietly stopped describing the tool is the failure this
repository is least entitled to, given what the tool is for. So the examples
are executable: each ``$ `` line is run with ``quotemap`` on PATH, and the
lines beneath it up to the next ``$ `` are the expected output.

Runs under pytest, and standalone as ``python tests/test_readme.py`` for
the CI job that installs the package without the dev extras.
"""

from __future__ import annotations

import difflib
import os
import pathlib
import shutil
import subprocess
import sys

try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover
    # The CI job that runs this standalone installs the package the way a
    # user would -- `pip install .`, no dev extras -- because checking the
    # README against a plain install is the whole point of running it that
    # way. pytest is not there, and is not needed to do the checking.
    pytest = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PROMPT = "$ "

#: A console script installed into a venv sits next to the interpreter
#: running us, so this finds `quotemap` under `.venv/bin/python -m pytest`
#: without anyone having to activate anything first.
PATH = os.pathsep.join([str(pathlib.Path(sys.executable).parent), os.environ["PATH"]])


def blocks(text: str) -> list[list[str]]:
    out, current, inside = [], [], False
    for line in text.splitlines():
        if line.strip() == "```console":
            inside, current = True, []
        elif inside and line.strip() == "```":
            inside = False
            out.append(current)
        elif inside:
            current.append(line)
    return out


def examples(block: list[str]) -> list[tuple[str, str]]:
    """Split a block into (command, expected output) pairs."""
    pairs: list[tuple[str, list[str]]] = []
    for line in block:
        if line.startswith(PROMPT):
            pairs.append((line[len(PROMPT):], []))
        elif pairs:
            pairs[-1][1].append(line)
    return [(cmd, "\n".join(body).rstrip("\n")) for cmd, body in pairs]


def check() -> int:
    failures = 0
    for block in blocks(README.read_text()):
        for command, expected in examples(block):
            result = subprocess.run(
                ["bash", "-c", command], cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env={"PATH": PATH, "NO_COLOR": "1"},
            )
            actual = result.stdout.rstrip("\n")
            if actual == expected:
                print(f"ok   {command}")
                continue
            failures += 1
            print(f"FAIL {command}")
            for line in difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                "README", "actual", lineterm="",
            ):
                print(f"     {line}")
    print(f"\n{failures} failing example(s)" if failures else "\nREADME matches.")
    return 1 if failures else 0


if pytest is not None:

    @pytest.mark.skipif(
        shutil.which("quotemap", path=PATH) is None,
        reason="needs the console script installed; pip install -e .",
    )
    def test_readme_examples_are_accurate():
        assert check() == 0, "the README no longer describes the tool"


if __name__ == "__main__":
    sys.exit(check())
