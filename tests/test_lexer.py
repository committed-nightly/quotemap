"""Unit tests for the scanner.

The convention here: ``states`` gives one symbol per character, so an
expected context map is written as a string the same length as the input and
lines up under it when you read the file. If one of these fails, print the
two strings one above the other and the cause is usually immediate.
"""

import pytest

from quotemap import lexer
from quotemap.lexer import UnterminatedError, scan

SYMBOL = {
    lexer.TOP: ".",
    lexer.DQ: '"',
    lexer.LOCALE: "L",
    lexer.SQ: "'",
    lexer.ANSI: "A",
    lexer.CMDSUB: "(",
    lexer.BACKTICK: "`",
    lexer.ARITH: "#",
    lexer.PARAM: "{",
    lexer.HEREDOC: "<",
    lexer.COMMENT: ";",
}


def states(source):
    return "".join(SYMBOL[c.kind] for c in scan(source, strict=False).chars)


def verdicts(source):
    return [(e.text, e.verdict) for e in scan(source, strict=False).expansions]


# -- the context map ---------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        # A delimiter carries the symbol of the region it bounds, so a
        # quoted span reads as one block from quote to quote.
        ('a "b" c', '.."""..'),
        ("a 'b' c", "..'''.."),
        # The whole point. The " before $X opens a new string inside the
        # substitution; counting quotes left to right says it closes one.
        # Read the ruler instead and the three regions are simply visible.
        ('x="$(e "$X")"', '.."((((""""("'),
        # Backticks nest the same way.
        ('x="`e "$X"`"', '.."```""""`"'),
        # $'...' is one construct, not a dollar and a quote.
        ("$'a'", "AAAA"),
        # ...but only where quoting can start. Inside "..." it is literal.
        ('"$\'a\'"', '""""""'),
        ("${x:-y}", "{{{{{{{"),
        ("$((1+2))", "########"),
        ("a # b", "..;;;"),
        # A # mid-word is not a comment.
        ("a#b", "..."),
        ("'#'", "'''"),
    ],
)
def test_context_map(source, expected):
    assert states(source) == expected, f"\n  {source}\n  {states(source)}\n  {expected}"


def test_nested_substitution_is_a_fresh_quoting_context():
    result = scan('"$(echo "$x")"')
    inner = [e for e in result.expansions if e.text == "$x"][0]
    assert inner.quoted  # by its own "..." , not the outer one
    outer = [e for e in result.expansions if e.form == "command"][0]
    assert outer.quoted


def test_unquoted_inside_quoted_substitution_is_split():
    (inner,) = [e for e in scan('"$(echo $x)"').expansions if e.text == "$x"]
    assert inner.verdict == "split"


# -- extents -----------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("$(a $(b) c)", "$(a $(b) c)"),
        ("${x:-$(y)}", "${x:-$(y)}"),
        ("$((1 + (2 * 3)))", "$((1 + (2 * 3)))"),
        ("`a`", "`a`"),
        ('$(echo ")")', '$(echo ")")'),
        ("$(echo ')')", "$(echo ')')"),
    ],
)
def test_expansion_extent(source, expected):
    assert scan(source).expansions[0].text == expected


def test_expansions_are_in_source_order():
    result = scan("$a ${b} $(c) `d` $((1+$e))")
    assert [e.text for e in result.expansions] == [
        "$a", "${b}", "$(c)", "`d`", "$((1+$e))", "$e",
    ]


# -- verdicts ----------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("echo $x", [("$x", "split")]),
        ('echo "$x"', [("$x", "quoted")]),
        ("echo '$x'", [("$x", "literal")]),
        ("echo \\$x", [("$x", "literal")]),
        ('echo "a\\$x"', [("$x", "literal")]),
        ("echo $'$x'", [("$x", "literal")]),
        # Not split: the shell does not split an assignment right-hand side.
        ("x=$y", [("$y", "bare")]),
        ("x+=$y", [("$y", "bare")]),
        ("x[0]=$y", [("$y", "bare")]),
        # ...but only in assignment position. `echo a=$y` really does split.
        ("echo a=$y", [("$y", "split")]),
        ("[[ $x = y ]]", [("$x", "bare")]),
        ("[[ $x ]]; echo $y", [("$x", "bare"), ("$y", "split")]),
    ],
)
def test_verdict(source, expected):
    assert verdicts(source) == expected


def test_inert_expansion_is_still_reported():
    """The row people actually need: this looks like $x and is not."""
    (exp,) = scan("echo '$HOME'").expansions
    assert exp.verdict == "literal"
    assert exp.reason == "single-quoted"
    assert exp.text == "$HOME"


def test_arithmetic_inner_expansion_is_not_split():
    (arith, inner) = scan("echo $((1 + $n))").expansions
    assert arith.verdict == "split"  # the $(( )) itself is a word
    assert inner.verdict == "bare"
    assert inner.reason == "evaluated arithmetically"


def test_quoted_expansion_has_no_redundant_reason():
    (exp,) = scan('x="$y"').expansions
    assert exp.verdict == "quoted"
    assert exp.reason == ""


# -- here-documents ----------------------------------------------------


def test_heredoc_with_quoted_delimiter_does_not_expand():
    (exp,) = scan("cat <<'EOF'\n$x\nEOF\n").expansions
    assert exp.verdict == "literal"
    assert "quoted delimiter" in exp.reason


def test_heredoc_with_bare_delimiter_expands_but_does_not_split():
    (exp,) = scan("cat <<EOF\n$x\nEOF\n").expansions
    assert exp.verdict == "quoted"


def test_heredoc_body_ends_at_the_delimiter():
    result = scan("cat <<EOF\ninside\nEOF\noutside $x\n")
    (exp,) = result.expansions
    assert exp.line == 4
    assert exp.verdict == "split"


def test_dash_heredoc_allows_an_indented_delimiter():
    result = scan("cat <<-EOF\n\tbody\n\tEOF\nafter\n")
    assert not result.unterminated


def test_plain_heredoc_does_not_end_on_an_indented_delimiter():
    result = scan("cat <<EOF\n\tEOF\n", strict=False)
    assert [k for k, _, _ in result.unterminated] == [lexer.HEREDOC]


def test_herestring_is_not_a_heredoc():
    result = scan("cat <<<$x\n")
    assert not result.unterminated
    assert [e.text for e in result.expansions] == ["$x"]


def test_two_heredocs_on_one_line():
    result = scan("cat <<A <<'B'\n$x\nA\n$y\nB\n")
    assert [e.verdict for e in result.expansions] == ["quoted", "literal"]


# A heredoc's frame opens *at* the first character of its body rather than at
# its own punctuation, so a substitution starting the body shares that index.
# Extents used to be keyed on the index alone, and the substitution's text ran
# to the end of the heredoc instead of to its own closing delimiter.


@pytest.mark.parametrize("expansion", ["$(echo hi)", "${x}", "$((1+1))", "`date`"])
def test_expansion_opening_a_heredoc_body_stops_at_its_own_close(expansion):
    (exp,) = scan(f"cat <<EOF\n{expansion}\nEOF\n").expansions
    assert exp.text == expansion
    assert exp.start == len("cat <<EOF\n")
    assert exp.end == exp.start + len(expansion)


def test_expansion_opening_a_heredoc_body_does_not_swallow_the_rest():
    (exp,) = scan("cat <<EOF\n$(date) and more\nEOF\n").expansions
    assert exp.text == "$(date)"


def test_both_expansions_are_right_when_one_opens_the_body():
    result = scan("cat <<EOF\n$(a) then $(b)\nEOF\n")
    assert [e.text for e in result.expansions] == ["$(a)", "$(b)"]


def test_nested_expansion_at_the_start_of_a_heredoc_body():
    """Three frames open at the same index here: heredoc, outer, inner."""
    result = scan("cat <<EOF\n$($(x))\nEOF\n")
    assert [e.text for e in result.expansions] == ["$($(x))", "$(x)"]


def test_frames_sharing_an_index_get_distinct_serials():
    """The invariant the extent patching relies on."""
    result = scan("cat <<EOF\n$(x)\nEOF\n")
    dollar = next(c for c in result.chars if c.index == len("cat <<EOF\n"))
    heredoc, cmdsub = dollar.stack
    assert heredoc.kind == lexer.HEREDOC and cmdsub.kind == lexer.CMDSUB
    assert heredoc.opened_at == cmdsub.opened_at
    assert heredoc.serial != cmdsub.serial


def test_serial_does_not_affect_how_contexts_compare():
    """Two views of the same construct still compare by what they mean."""
    assert lexer.Ctx(lexer.DQ, 3, serial=0) == lexer.Ctx(lexer.DQ, 3, serial=7)
    assert len({lexer.Ctx(lexer.DQ, 3, serial=0), lexer.Ctx(lexer.DQ, 3, serial=7)}) == 1


# -- brace expansions --------------------------------------------------


def test_quotes_are_literal_inside_a_double_quoted_braced_expansion():
    """Checked against bash: "${x:-'a b'}" prints the single quotes."""
    result = scan("""echo "${x:-'a b'}\"""")
    assert lexer.SQ not in {c.kind for c in result.chars}


def test_quotes_are_live_inside_an_unquoted_braced_expansion():
    result = scan("echo ${x:-'a b'}")
    assert lexer.SQ in {c.kind for c in result.chars}


# -- escaping ----------------------------------------------------------


def test_backslash_in_double_quotes_only_escapes_some_characters():
    # "\d" stays a backslash and a d, which is how a regex survives a shell.
    result = scan(r'"\d\$"')
    escaped = [c.char for c in result.chars if c.escaped]
    assert escaped == ["$"]


def test_backslash_escapes_anything_unquoted():
    result = scan(r"a\ b")
    assert [c.char for c in result.chars if c.escaped] == [" "]


def test_no_escapes_inside_single_quotes():
    result = scan(r"'a\'")
    assert not [c for c in result.chars if c.escaped]
    assert not result.unterminated  # the \ did not escape the closing quote


# -- unterminated ------------------------------------------------------


@pytest.mark.parametrize(
    "source,kind",
    [
        ('a "b', lexer.DQ),
        ("a 'b", lexer.SQ),
        ("$(a", lexer.CMDSUB),
        ("${a", lexer.PARAM),
        ("$((a", lexer.ARITH),
        ("`a", lexer.BACKTICK),
        ("cat <<EOF\nx\n", lexer.HEREDOC),
    ],
)
def test_unterminated_is_reported(source, kind):
    with pytest.raises(UnterminatedError) as caught:
        scan(source)
    assert caught.value.kind == kind

    lenient = scan(source, strict=False)
    assert [k for k, _, _ in lenient.unterminated] == [kind]
    # Lenient scanning still covers the whole input, so the map is complete.
    assert len(lenient.chars) == len(source)


def test_unterminated_points_at_the_opening_character():
    with pytest.raises(UnterminatedError) as caught:
        scan('ok\nthen "oops\n')
    assert (caught.value.line, caught.value.column) == (2, 6)


# -- positions ---------------------------------------------------------


def test_line_and_column_are_one_based():
    (exp,) = scan("a\nbb $x\n").expansions
    assert (exp.line, exp.column) == (2, 4)


def test_every_character_is_accounted_for():
    source = "x=1 # c\ncat <<'E'\n$a\nE\necho \"$b\" '$c' `d`\n"
    result = scan(source)
    assert "".join(c.char for c in result.chars) == source
