"""Tests for the command line, including the exit codes CI would read."""

import pytest

from quotemap.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_map_and_table_by_default(capsys):
    code, out, err = run(capsys, "-c", 'x="$y"')
    assert code == 0
    assert 'x="$y"' in out
    assert '..""""' in out  # the ruler
    assert "quoted" in out


def test_ruler_lines_up_with_the_source(capsys):
    _, out, _ = run(capsys, "-c", 'a="$(b "$c")"')
    source, ruler = out.splitlines()[0], out.splitlines()[1]
    assert len(source) == len(ruler)
    assert source.split("| ", 1)[1] == 'a="$(b "$c")"'


def test_expansions_only(capsys):
    code, out, _ = run(capsys, "-c", "echo $x", "--expansions")
    assert code == 0
    assert "echo $x" not in out.splitlines()[0]
    assert "split" in out


def test_map_only(capsys):
    code, out, _ = run(capsys, "-c", "echo $x", "--map")
    assert code == 0
    assert "split" not in out


def test_depth_ruler(capsys):
    _, out, _ = run(capsys, "-c", '"$(a)"', "--depth", "--map", "--color", "never")
    source, context, depth = (line.split("| ", 1)[1] for line in out.splitlines())
    assert source == '"$(a)"'
    assert context == '"(((("'
    # The " is inside one construct, everything from $( inwards is inside two.
    assert depth == "122221"


def test_depth_ruler_is_absent_unless_asked_for(capsys):
    _, out, _ = run(capsys, "-c", '"$(a)"', "--map", "--color", "never")
    assert len(out.splitlines()) == 2


def test_line_offset_renumbers(capsys):
    _, out, _ = run(capsys, "-c", "echo $x", "--line", "40")
    assert out.startswith("40 |")
    assert "40:6" in out


def test_no_expansions_says_so(capsys):
    _, out, _ = run(capsys, "-c", "echo hello")
    assert "no expansions." in out


def test_unterminated_quote_exits_two_but_still_maps(capsys):
    code, out, err = run(capsys, "-c", 'echo "oops')
    assert code == 2
    assert 'echo "oops' in out  # the map is the thing you wanted
    assert "unterminated" in err
    assert "1:6" in err


def test_empty_input_is_an_error(capsys):
    code, _, err = run(capsys, "-c", "")
    assert code == 2
    assert "nothing to scan" in err


def test_missing_file_is_an_error(capsys):
    code, _, err = run(capsys, "/nonexistent/quotemap-test")
    assert code == 2
    assert "quotemap:" in err


def test_reads_a_file(tmp_path, capsys):
    script = tmp_path / "s.sh"
    script.write_text("echo '$x'\n")
    code, out, _ = run(capsys, str(script))
    assert code == 0
    assert "literal" in out
    assert "single-quoted" in out


def test_reads_stdin(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("echo $x\n"))
    code, out, _ = run(capsys, "-")
    assert code == 0
    assert "split" in out


def test_file_and_command_together_is_rejected(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["-c", "x", "file.sh"])
    assert caught.value.code == 2


def test_opposite_output_flags_are_rejected(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["-c", "x", "--expansions", "--map"])
    assert caught.value.code == 2


def test_colour_never_emits_no_escapes(capsys):
    _, out, _ = run(capsys, "-c", 'echo "$x"', "--color", "never")
    assert "\033[" not in out


def test_colour_always_emits_escapes(capsys):
    _, out, _ = run(capsys, "-c", 'echo "$x"', "--color", "always")
    assert "\033[" in out


def test_no_color_env_is_honoured(monkeypatch, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    _, out, _ = run(capsys, "-c", 'echo "$x"', "--color", "auto")
    assert "\033[" not in out


def test_colour_always_beats_no_color(monkeypatch, capsys):
    """--color=always is an explicit instruction; NO_COLOR is a default."""
    monkeypatch.setenv("NO_COLOR", "1")
    _, out, _ = run(capsys, "-c", 'echo "$x"', "--color", "always")
    assert "\033[" in out


def test_colour_never_leaves_the_text_intact(capsys):
    """Stripping the escapes must give back exactly the source line."""
    import re

    _, coloured, _ = run(capsys, "-c", 'a="$b"', "--color", "always", "--map")
    _, plain, _ = run(capsys, "-c", 'a="$b"', "--color", "never", "--map")
    assert re.sub(r"\033\[[0-9;]*m", "", coloured) == plain
