"""Turning scan results into something a person can read at 2am.

Two views. The map puts a ruler under the source, one symbol per character,
so the shape of the quoting is visible at a glance. The report is a table of
every expansion with a one-word verdict, for when you want the answer rather
than the picture.
"""

from __future__ import annotations

from . import lexer
from .lexer import CharState, Expansion, ScanResult

#: One symbol per context kind, for the ruler. Chosen so the symbol looks
#: like the thing it marks wherever that is possible.
SYMBOLS = {
    lexer.TOP: ".",
    lexer.DQ: '"',
    lexer.LOCALE: '"',
    lexer.SQ: "'",
    lexer.ANSI: "'",
    lexer.CMDSUB: "(",
    lexer.BACKTICK: "`",
    lexer.ARITH: "#",
    lexer.PARAM: "{",
    lexer.HEREDOC: "<",
    lexer.COMMENT: ";",
}

#: ANSI colours, by context kind. Backgrounds would be louder but they wreck
#: copy-paste into a terminal that has its own ideas about contrast.
COLOURS = {
    lexer.TOP: "",
    lexer.DQ: "\033[36m",
    lexer.LOCALE: "\033[36m",
    lexer.SQ: "\033[32m",
    lexer.ANSI: "\033[32m",
    lexer.CMDSUB: "\033[33m",
    lexer.BACKTICK: "\033[33m",
    lexer.ARITH: "\033[35m",
    lexer.PARAM: "\033[34m",
    lexer.HEREDOC: "\033[35m",
    lexer.COMMENT: "\033[90m",
}
RESET = "\033[0m"

VERDICT_COLOURS = {
    "split": "\033[31m",
    "bare": "\033[33m",
    "quoted": "\033[32m",
    "literal": "\033[90m",
}

#: What each verdict means, printed under the report so nobody has to guess.
VERDICT_HELP = {
    "literal": "not expanded at all",
    "quoted": "expanded, inside quotes, one word",
    "bare": "expanded, unquoted, but not split here",
    "split": "expanded, unquoted: word-split and globbed",
}


def _symbol(state: CharState) -> str:
    return SYMBOLS.get(state.kind, "?")


def _depth_symbol(state: CharState) -> str:
    depth = len(state.stack)
    return str(depth) if depth < 10 else "+"


def render_map(result: ScanResult, *, color: bool = False, depth: bool = False,
               first_line: int = 1) -> str:
    """The source with a context ruler under each line.

    Lines are numbered from ``first_line`` so a fragment lifted out of a
    bigger file can still be talked about by its real line numbers.
    """
    lines = result.line_states()
    source_lines = result.source.split("\n")
    # A trailing newline produces a final empty group; it is not a line.
    if lines and not lines[-1] and result.source.endswith("\n"):
        lines = lines[:-1]
        source_lines = source_lines[:-1]

    width = len(str(first_line + len(lines) - 1))
    out = []
    for offset, states in enumerate(lines):
        number = str(first_line + offset).rjust(width)
        text = source_lines[offset] if offset < len(source_lines) else ""
        out.append(f"{number} | {_paint(states, text, color)}")
        out.append(f"{' ' * width} | {''.join(_symbol(s) for s in states)}")
        if depth:
            out.append(f"{' ' * width} | {''.join(_depth_symbol(s) for s in states)}")
    return "\n".join(out)


def _paint(states: list[CharState], fallback: str, color: bool) -> str:
    if not color:
        return fallback
    out = []
    current = None
    for state in states:
        colour = COLOURS.get(state.kind, "")
        if colour != current:
            out.append(RESET)
            out.append(colour)
            current = colour
        out.append(state.char)
    out.append(RESET)
    return "".join(out)


def _one_line(text: str) -> str:
    """Fold a multi-line expansion onto its single row in the table.

    A ``$( )`` can legitimately span lines, and its raw text would put a
    newline in the middle of a column and knock every row below it out of
    alignment. The map above already shows the real layout; this column only
    has to say which expansion the row is about.
    """
    if "\n" not in text:
        return text
    return " ".join(text.split())


def render_expansions(expansions: list[Expansion], *, color: bool = False,
                      first_line: int = 1) -> str:
    """The table: one row per expansion, in source order."""
    if not expansions:
        return "no expansions."

    rows = []
    for exp in expansions:
        line = exp.line + first_line - 1
        rows.append((f"{line}:{exp.column}", _one_line(exp.text), exp.form,
                     exp.verdict, exp.reason))

    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    out = []
    for where, text, form, verdict, reason in rows:
        painted = verdict.ljust(widths[3])
        if color:
            painted = f"{VERDICT_COLOURS.get(verdict, '')}{painted}{RESET}"
        cells = f"{where.ljust(widths[0])}  {text.ljust(widths[1])}  {form.ljust(widths[2])}  {painted}"
        out.append(f"{cells}  {reason}".rstrip())

    seen = {exp.verdict for exp in expansions}
    out.append("")
    for verdict in ("literal", "quoted", "bare", "split"):
        if verdict in seen:
            out.append(f"  {verdict:8} {VERDICT_HELP[verdict]}")
    return "\n".join(out)


def render_unterminated(result: ScanResult, *, first_line: int = 1) -> str:
    return "\n".join(
        f"{line + first_line - 1}:{col}  unterminated {lexer.DESCRIPTIONS[kind]}"
        for kind, line, col in result.unterminated
    )
