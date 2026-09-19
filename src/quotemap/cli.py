"""The command line.

Exit codes: 0 if the input was scanned, 2 if it could not be (unterminated
quote, unreadable file, bad arguments). There is deliberately no exit code
for "your script has a bug in it" — quotemap shows you what the shell sees
and leaves the judging to you. ShellCheck is the linter and it is a good one.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import lexer, render
from .lexer import UnterminatedError, scan

PROG = "quotemap"

EPILOG = """\
examples:
  quotemap -c 'OUT="$(echo "$X" | sed s/a/b/)"'
  quotemap script.sh --expansions
  pbpaste | quotemap -

the ruler:
  .  unquoted      "  double quotes   '  single quotes
  (  $( )          `  backticks       {  ${ }
  #  $(( ))        <  here-document   ;  comment
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Show the quoting state of every character in a shell "
                    "line, and which expansions actually expand.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "file", nargs="?",
        help="shell script to read; '-' or omitted reads standard input",
    )
    parser.add_argument(
        "-c", "--command", metavar="TEXT",
        help="scan TEXT instead of a file",
    )
    parser.add_argument(
        "-e", "--expansions", action="store_true",
        help="print only the expansion table, without the map",
    )
    parser.add_argument(
        "-m", "--map", dest="map_only", action="store_true",
        help="print only the map, without the expansion table",
    )
    parser.add_argument(
        "-d", "--depth", action="store_true",
        help="add a nesting-depth ruler under the context ruler",
    )
    parser.add_argument(
        "--line", type=int, default=1, metavar="N",
        help="number the first line N, for a fragment lifted out of a file",
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="colour the output (default: auto, meaning only to a terminal)",
    )
    return parser


def _want_colour(choice: str, stream) -> bool:
    if choice == "never":
        return False
    if choice == "always":
        return True
    # NO_COLOR is honoured for the same reason anyone honours it: someone
    # piping this into a file does not want escape codes in their file, and
    # they should not have to say so twice.
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _read_source(args: argparse.Namespace) -> str:
    if args.command is not None:
        return args.command
    if args.file in (None, "-"):
        return sys.stdin.read()
    with open(args.file, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is not None and args.file is not None:
        parser.error("give a file or -c, not both")
    if args.expansions and args.map_only:
        parser.error("--expansions and --map ask for opposite things")

    try:
        source = _read_source(args)
    except OSError as exc:
        print(f"{PROG}: {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 2

    if not source:
        print(f"{PROG}: nothing to scan", file=sys.stderr)
        return 2

    # Scan non-strict so an unterminated quote still produces a map. The map
    # is exactly what you want in that case: it shows where the quote opened
    # and what it swallowed, which is the question you were asking.
    result = scan(source, strict=False)
    colour = _want_colour(args.color, sys.stdout)

    if not args.expansions:
        print(render.render_map(result, color=colour, depth=args.depth,
                                first_line=args.line))
    if not args.map_only:
        if not args.expansions:
            print()
        print(render.render_expansions(result.expansions, color=colour,
                                       first_line=args.line))

    if result.unterminated:
        # Flush first: the map has just gone to stdout and the complaint is
        # about to go to stderr, and a reader merging the two wants them in
        # the order they were written.
        sys.stdout.flush()
        print(file=sys.stderr)
        print(render.render_unterminated(result, first_line=args.line),
              file=sys.stderr)
        return 2
    return 0


def run() -> None:  # pragma: no cover - console_scripts entry point
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
