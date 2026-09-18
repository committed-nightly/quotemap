"""Check quotemap's verdicts against the only authority that matters.

Every row here runs a real bash program and derives two facts from what bash
actually did — was the expansion substituted, and did it become more than one
word — then asserts quotemap says the same. Nothing in this file encodes my
opinion about shell semantics, which is the point: I got two of these wrong
on the first attempt and bash is why they are right now.

The probe is::

    x='a b'
    show() { printf '%s|' "$#"; printf '<%s>' "$@"; echo; }

so a line of output reads ``2|<a><b>`` when the expansion split and
``1|<a b>`` when it did not. If the literal text ``$x`` comes back, nothing
expanded at all.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from quotemap import scan

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="these tests are a bash oracle"
)

PRELUDE = """\
x='a b'
show() { printf '%s|' "$#"; printf '<%s>' "$@"; echo; }
"""

#: (program, the expansion to check)
#:
#: For the nested rows the inner ``show`` does not print: its output is
#: captured by the substitution and nested inside the outer one, so
#: ``show "$(show $x)"`` prints ``1|<2|<a><b>>``. The innermost count is the
#: last one in the line, and it is the one that measures the inner ``$x``.
ROWS = [
    # -- the basics ----------------------------------------------------
    ("show $x", "$x"),
    ('show "$x"', "$x"),
    ("show '$x'", "$x"),
    ("show ${x}", "${x}"),
    ('show "${x}"', "${x}"),
    ("show '${x}'", "${x}"),
    # -- escaping ------------------------------------------------------
    ("show \\$x", "$x"),
    ('show "a\\$x"', "$x"),
    ("show $'$x'", "$x"),
    # -- a substitution is a fresh quoting context ---------------------
    # The inner $x is bare even though the whole $( ) is double-quoted.
    ('show "$(show $x)"', "$x"),
    ('show "$(show "$x")"', "$x"),
    ("show $(show '$x')", "$x"),
    ("show `show $x`", "$x"),
    # -- places the shell does not split -------------------------------
    ("y=$x; show \"$y\"", "$x"),
    ("y[0]=$x; show \"${y[0]}\"", "$x"),
    ("show a=$x", "$x"),
    # -- arithmetic ----------------------------------------------------
    ("n=3; show $((1 + $n))", "$n"),
    # -- braced word parts ---------------------------------------------
    ("unset u; show ${u:-$x}", "$x"),
    ('unset u; show "${u:-$x}"', "$x"),
]


def _run(program: str) -> list[str]:
    result = subprocess.run(
        ["bash", "-c", PRELUDE + program],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr}"
    return result.stdout.splitlines()


def _observe(output: str, target: str) -> tuple[bool, bool]:
    """(expanded, split), as bash just demonstrated them.

    The last ``N|`` in the output is the innermost ``show``, which is the
    one being measured; the target text surviving anywhere in the output
    means nothing expanded it.
    """
    counts = re.findall(r"(\d+)\|", output)
    assert counts, f"probe printed nothing measurable: {output!r}"
    return target not in output, int(counts[-1]) > 1


@pytest.mark.parametrize("program,target", ROWS, ids=[r[0] for r in ROWS])
def test_matches_bash(program, target):
    observed = "\n".join(_run(program))
    expanded, split = _observe(observed, target)

    matches = [e for e in scan(PRELUDE + program).expansions if e.text == target]
    assert len(matches) == 1, f"expected one {target}, got {[e.text for e in matches]}"
    exp = matches[0]

    assert exp.expanded is expanded, (
        f"quotemap says expanded={exp.expanded}, bash printed {observed!r}"
    )
    assert exp.split is split, (
        f"quotemap says split={exp.split}, bash printed {observed!r}"
    )


# -- here-documents ----------------------------------------------------
#
# A here-document body is never word-split, so the only question bash can
# answer is whether the delimiter's quoting stopped the expansion.

HEREDOC_ROWS = [
    ("cat <<EOF\n$x\nEOF\n", True),
    ("cat <<'EOF'\n$x\nEOF\n", False),
    ('cat <<"EOF"\n$x\nEOF\n', False),
    ("cat <<\\EOF\n$x\nEOF\n", False),
    ("cat <<-EOF\n\t$x\n\tEOF\n", True),
    ("cat <<-'EOF'\n\t$x\n\tEOF\n", False),
]


@pytest.mark.parametrize("program,expected", HEREDOC_ROWS, ids=[repr(r[0]) for r in HEREDOC_ROWS])
def test_heredoc_expansion_matches_bash(program, expected):
    body = _run(program)
    expanded = "$x" not in "\n".join(body)
    assert expanded is expected, f"the probe itself is wrong: bash printed {body!r}"

    (exp,) = [e for e in scan(PRELUDE + program).expansions if e.text == "$x"]
    assert exp.expanded is expanded
    assert exp.split is False  # never, in a here-document


# -- the claim the whole tool exists to make ---------------------------


def test_the_inner_quote_opens_rather_than_closes():
    """The grafana line, reduced.

    Counting double quotes left to right says the one before ``$x`` closes
    the string that opened after ``=``. It does not: it is nested inside the
    substitution and opens a new one. bash agrees with the second reading.
    """
    program = 'OUT="$(show "$x")"; printf "%s\\n" "$OUT"'
    first = _run(program)[0]
    assert first == "1|<a b>"  # one word, so that " was an opening quote

    (inner,) = [e for e in scan(PRELUDE + program).expansions if e.text == "$x"]
    assert inner.quoted is True
    assert inner.split is False
