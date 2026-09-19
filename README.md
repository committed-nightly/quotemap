# quotemap

Show the quoting state of every character in a shell line, and which expansions actually expand.

For anyone who has stared at a `run:` block, a Makefile recipe or a one-liner off a wiki
and had to count quotes left to right to work out which one closes which. The shell does
not read it left to right, which is where the bugs come from.

```console
$ quotemap -c 'rm -rf "$BUILD_DIR/"$NAME'
1 | rm -rf "$BUILD_DIR/"$NAME
  | .......""""""""""""".....

1:9   $BUILD_DIR  parameter  quoted
1:21  $NAME       parameter  split

  quoted   expanded, inside quotes, one word
  split    expanded, unquoted: word-split and globbed
```

The quote closes *before* `$NAME`. The ruler shows it, and the table says what that
means: `$NAME` is word-split and glob-expanded, and `rm -rf` is about to be handed
however many arguments that produces.

## Install

```
pip install git+https://github.com/committed-nightly/quotemap
```

Python 3.10 or newer. No dependencies.

## Usage

```
quotemap FILE              # scan a script
quotemap -c 'some line'    # scan a string
quotemap -                 # scan standard input
```

Useful flags: `-e/--expansions` for the table alone, `-m/--map` for the picture alone,
`-d/--depth` to add a nesting-depth ruler, `--line N` to number a fragment by its real
position in a larger file, `--color auto|always|never`.

An expansion that spans several lines is folded onto one row of the table, so the
columns stay aligned. The `line:column` on the left and the map above it both point at
where it really is.

The ruler symbols:

| | | | |
|---|---|---|---|
| `.` unquoted | `"` double quotes | `'` single quotes | `` ` `` backticks |
| `(` `$( )` | `{` `${ }` | `#` `$(( ))` | `<` here-document |
| `;` comment | | | |

A delimiter carries the symbol of the region it bounds, so a quoted span reads as one
block from its opening quote to its closing one.

## A real example

This is a line from [grafana/alerting](https://github.com/grafana/alerting)'s workflows,
lightly shortened:

```console
$ quotemap -c 'OUT="$(echo "$X" | sed -E "s/a/b/")"'
1 | OUT="$(echo "$X" | sed -E "s/a/b/")"
  | ...."(((((((""""((((((((((""""""""("

1:6   $(echo "$X" | sed -E "s/a/b/")  command    quoted
1:14  $X                              parameter  quoted

  quoted   expanded, inside quotes, one word
```

Count the double quotes left to right and you get the wrong answer at the second one.
The `"` before `$X` looks like it closes the string opened after `OUT=`; it does not,
because it is nested inside the `$( )` and opens a new one. The ruler shows the three
regions — outer string, substitution, inner string — and you can stop counting.

Here-documents work the same way, and the delimiter's own quoting decides the whole
body:

```console
$ quotemap examples/deploy.sh
1 | cat <<'EOF'
  | ...........
2 | deploy $APP to $ENV
  | <<<<<<<<<<<<<<<<<<<
3 | EOF
  | <<<
4 | cat <<EOF
  | .........
5 | built at $(date)
  | <<<<<<<<<(((((((
6 | EOF
  | <<<

2:8   $APP     parameter  literal  in a here-document with a quoted delimiter
2:16  $ENV     parameter  literal  in a here-document with a quoted delimiter
5:10  $(date)  command    quoted

  literal  not expanded at all
  quoted   expanded, inside quotes, one word
```

## The four verdicts

| verdict | what happens |
|---|---|
| `literal` | not expanded at all — single-quoted, backslash-escaped, `$'...'`, or in a here-document with a quoted delimiter |
| `quoted` | expanded, inside quotes, stays one word |
| `bare` | expanded, unquoted, but somewhere the shell does not split: an assignment right-hand side, inside `[[ ]]`, inside `$(( ))` |
| `split` | expanded, unquoted, subject to word splitting and filename expansion |

`split` is not a synonym for "bug" — it is often exactly what was meant. quotemap tells
you what the shell will do and leaves the judging to you. If you want a linter with
opinions, [ShellCheck](https://www.shellcheck.net/) is excellent; use it as well as
this, not instead of it.

## Exit codes

`0` scanned it. `2` could not — an unterminated quote or substitution, an unreadable
file, bad arguments. There is deliberately no exit code meaning "your script looks
wrong", because that is not a judgement this tool makes.

An unterminated construct still prints the map, because that is the output you wanted:
it shows where the quote opened and how much it swallowed.

```console
$ quotemap -c 'echo "oops'; echo "exit $?"
1 | echo "oops
  | ....."""""

no expansions.

1:6  unterminated double quote (")
exit 2
```

## How it is checked

The verdicts are tested against real bash rather than against a reading of the manual.
`tests/test_against_bash.py` runs each case through `bash -c` and derives two facts from
what actually happened — was it substituted, did it become more than one word — then
asserts quotemap says the same. Four behaviours are in the tests only because bash
contradicted the first guess:

- `$(( ))` really is word-split. `IFS=1; set -- $((11))` gives two positional parameters.
- Quotes inside `${x:-...}` are literal when the expansion is itself double-quoted.
  `echo "${x:-'a b'}"` prints the single quotes; unquoted, it does not.
- A quoted here-document delimiter stops expansion for the whole body, in all of
  `<<'EOF'`, `<<"EOF"` and `<<\EOF`.
- `$'...'` is ANSI-C quoting only where quoting can start. Inside `"..."` the `$'` is a
  literal dollar and a literal quote.

```
pip install -e ".[dev]"
python -m pytest
```

## What it does not do

It is a lexer, not a parser, and it is honest about the edge:

- **`case` patterns.** `case $x in` does not word-split, and quotemap reports that `$x`
  as `split`. `[[ ]]` and assignment right-hand sides are handled; `case` is not.
- **`${x//a/b}` internals.** The pattern and replacement have their own quoting rules,
  which quotemap treats as ordinary `${ }` content.
- **Expansions inside comments** are not listed, because a `#` comment is not shell. The
  map still shows the comment region.
- **Backslashes inside backticks**, which follow a different and worse rule than
  everywhere else.
- **`$(( ))` against `$( ( ) )`.** Genuinely ambiguous; quotemap reads it as arithmetic,
  as bash does.

## Licence

MIT.
