"""A character-level scanner for shell quoting state.

This is not a shell parser. It answers one question, for every character in
the input: what quoting context are you in, and is anything going to expand
you? That is a lexical question, which is why a lexer can answer it and why
the answer is worth having — the bugs this exists for are all cases where a
human counted quotes left to right and the shell did not.

The central object is the context stack. A frame is pushed when a quoting or
substitution construct opens and popped when it closes, so the state of any
character is the stack as it stood when that character was read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Frame kinds. These are the strings that show up in rendered output and in
# tests, so they are short and they do not change.
TOP = "top"
DQ = "dq"  # "..."
SQ = "sq"  # '...'
ANSI = "ansi"  # $'...'
LOCALE = "locale"  # $"..."
CMDSUB = "cmdsub"  # $(...)
BACKTICK = "backtick"  # `...`
ARITH = "arith"  # $((...))
PARAM = "param"  # ${...}
HEREDOC = "heredoc"  # body of a <<WORD
COMMENT = "comment"  # # to end of line

#: Kinds inside which nothing expands. ``$`` is an ordinary character here.
LITERAL_KINDS = frozenset({SQ, ANSI, COMMENT})

#: Kinds that are a "command context": a fresh start for quoting. A ``"``
#: inside ``$( )`` opens a new string even if the ``$( )`` itself sits inside
#: double quotes, and that is the single most misread thing in shell.
COMMAND_KINDS = frozenset({TOP, CMDSUB, BACKTICK})

#: Characters a backslash can escape inside double quotes. Everywhere else in
#: a double-quoted string a backslash is a literal backslash, which surprises
#: people who expect it to behave like C.
DQ_ESCAPABLE = frozenset('$`"\\\n')

#: Special parameters that are a complete expansion on their own: ``$?``,
#: ``$1``, ``$@`` and friends.
SPECIAL_PARAMS = frozenset("@*#?-$!0123456789")

DESCRIPTIONS = {
    TOP: "top level",
    DQ: 'double quote (")',
    SQ: "single quote (')",
    ANSI: "ANSI-C quote ($'...')",
    LOCALE: 'locale quote ($"...")',
    CMDSUB: "command substitution ($( ))",
    BACKTICK: "backtick substitution (` `)",
    ARITH: "arithmetic expansion ($(( )))",
    PARAM: "parameter expansion (${ })",
    HEREDOC: "here-document",
    COMMENT: "comment",
}


@dataclass(frozen=True)
class Ctx:
    """An immutable view of one open construct, as recorded against a char."""

    kind: str
    opened_at: int
    #: Only meaningful for HEREDOC: a ``<<'EOF'`` body does not expand.
    expand: bool = True

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        return f"Ctx({self.kind}@{self.opened_at})"


@dataclass
class _Frame:
    """The mutable scanner-side frame. ``depth`` tracks nested parens/braces."""

    kind: str
    opened_at: int
    expand: bool = True
    depth: int = 0
    #: For HEREDOC: the delimiter word, and whether leading tabs are stripped.
    delimiter: str = ""
    strip_tabs: bool = False
    #: For CMDSUB/BACKTICK: the word-tracking state to restore on close.
    saved_word: tuple | None = None
    #: For PARAM: whether ' and " inside the braces are quotes at all.
    quotes_live: bool = True

    def view(self) -> Ctx:
        return Ctx(self.kind, self.opened_at, self.expand)


def expands_in(stack: tuple[Ctx, ...]) -> bool:
    """True if a ``$`` in this context would be substituted.

    Single quotes and ANSI-C quotes suppress expansion no matter how deeply
    something is nested inside them, so this walks the whole stack. A literal
    here-document body does the same thing for its whole body.
    """
    for ctx in stack:
        if ctx.kind in LITERAL_KINDS:
            return False
        if ctx.kind == HEREDOC and not ctx.expand:
            return False
    return True


#: Why each kind stops expansion, phrased for a report line.
SUPPRESSORS = {
    SQ: "single-quoted",
    ANSI: "inside $'...'",
    COMMENT: "inside a comment",
}


def suppressor_of(stack: tuple[Ctx, ...]) -> str:
    """Name the outermost frame that stops expansion here, if any."""
    for ctx in stack:
        if ctx.kind in SUPPRESSORS:
            return SUPPRESSORS[ctx.kind]
        if ctx.kind == HEREDOC and not ctx.expand:
            return "in a here-document with a quoted delimiter"
    return ""


def quoted_in(stack: tuple[Ctx, ...]) -> bool:
    """True if this position is quoted, as the shell means it.

    Walks *down* from the innermost frame and stops at the first command
    context, because a ``$( )`` starts afresh: in ``"$(echo $x)"`` the ``$x``
    is unquoted, and the surrounding double quotes have nothing to do with it.
    """
    for ctx in reversed(stack):
        if ctx.kind in COMMAND_KINDS:
            return False
        if ctx.kind in (DQ, LOCALE, SQ, ANSI):
            return True
        if ctx.kind == HEREDOC:
            # A here-document body is not word-split, expanding or not.
            return True
        # ARITH is deliberately not here. `IFS=1; set -- $((11))` gives two
        # positional parameters, so an arithmetic expansion really is word
        # split; it just almost never matters, because the result is a
        # number. Expansions *inside* the arithmetic get a nosplit reason
        # instead, which is the accurate way to say it.
    return False


@dataclass(frozen=True)
class CharState:
    """What was true of one character of the input."""

    index: int
    char: str
    line: int  # 1-based
    column: int  # 1-based
    stack: tuple[Ctx, ...]
    #: True if this character was consumed by a preceding active backslash.
    escaped: bool = False
    #: True if this character is part of a delimiter (a quote, ``$(``, ``}``)
    #: rather than content. Delimiters vanish; content survives.
    syntax: bool = False

    @property
    def kind(self) -> str:
        """The innermost open construct."""
        return self.stack[-1].kind if self.stack else TOP

    @property
    def expands(self) -> bool:
        """Would a ``$`` here be expanded by the shell?"""
        return expands_in(self.stack)

    @property
    def quoted(self) -> bool:
        """Is this character inside quotes, for the nearest command context?"""
        return quoted_in(self.stack)


@dataclass(frozen=True)
class Expansion:
    """One thing the shell would (or pointedly would not) substitute."""

    start: int
    end: int  # exclusive
    text: str
    form: str  # 'parameter', 'braced', 'command', 'backtick', 'arithmetic'
    line: int
    column: int
    #: The stack as it stood at the opening ``$``, i.e. not counting itself.
    stack: tuple[Ctx, ...]
    #: Set when the expansion sits somewhere the shell does not word-split,
    #: even though it is unquoted. The string is the reason, for display.
    nosplit_reason: str = ""
    #: Set when this looks like an expansion and is not one, for a reason the
    #: stack alone does not show — currently only a preceding backslash.
    inert_reason: str = ""

    @property
    def expanded(self) -> bool:
        return expands_in(self.stack) and not self.inert_reason

    @property
    def quoted(self) -> bool:
        return quoted_in(self.stack)

    @property
    def split(self) -> bool:
        """Subject to word splitting and globbing — the classic shell bug."""
        return self.expanded and not self.quoted and not self.nosplit_reason

    @property
    def reason(self) -> str:
        """Why the verdict is what it is, when that is not self-evident.

        A ``quoted`` expansion explains itself, so a nosplit reason is not
        printed for one even when it applies: ``x="$y"`` is an assignment
        right-hand side *and* double-quoted, and saying both is noise.
        """
        if self.inert_reason:
            return self.inert_reason
        if not self.expanded:
            return suppressor_of(self.stack)
        if self.quoted:
            return ""
        return self.nosplit_reason

    @property
    def verdict(self) -> str:
        """One word for what happens here. See README for what each means."""
        if not self.expanded:
            return "literal"
        if self.quoted:
            return "quoted"
        if self.nosplit_reason:
            return "bare"
        return "split"


class UnterminatedError(Exception):
    """An opening construct that the input never closes."""

    def __init__(self, kind: str, line: int, column: int) -> None:
        self.kind = kind
        self.line = line
        self.column = column
        super().__init__(
            f"unterminated {DESCRIPTIONS[kind]} opened at line {line}, column {column}"
        )


@dataclass
class ScanResult:
    source: str
    chars: list[CharState]
    expansions: list[Expansion]
    #: Populated instead of raising when scan(strict=False).
    unterminated: list[tuple[str, int, int]] = field(default_factory=list)

    def line_states(self) -> list[list[CharState]]:
        """Group character states by source line, preserving empty lines."""
        lines: list[list[CharState]] = [[]]
        for state in self.chars:
            if state.char == "\n":
                lines.append([])
            else:
                lines[-1].append(state)
        return lines


def _is_name_start(ch: str) -> bool:
    return bool(ch) and (ch.isalpha() or ch == "_")


def _is_name_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == "_")


class _Scanner:
    def __init__(self, source: str) -> None:
        self.src = source
        self.i = 0
        self.n = len(source)
        self.stack: list[_Frame] = []
        self.chars: list[CharState] = []
        self.expansions: list[Expansion] = []
        self.unterminated: list[tuple[str, int, int]] = []
        # Expansions whose closing delimiter has not been seen yet.
        self.deferred: list[tuple[int, int]] = []
        self.line = 1
        self.col = 1
        # Here-documents announce themselves on one line and begin on the
        # next, so they queue up until the newline arrives.
        self.pending_heredocs: list[tuple[str, bool]] = []
        # Word-position tracking, for the split-exemption notes.
        self.at_command_start = True
        self.word_start = 0
        self.in_word = False
        self.assignment_eq = -1  # index of the '=' in a NAME= word
        self.in_double_bracket = False

    # -- position and emission -------------------------------------------

    def _pos(self, index: int) -> tuple[int, int]:
        """1-based (line, column) of an absolute index."""
        line = self.src.count("\n", 0, index) + 1
        last_nl = self.src.rfind("\n", 0, index)
        return line, index - last_nl

    def _stack_view(self) -> tuple[Ctx, ...]:
        return tuple(f.view() for f in self.stack)

    def _emit(self, count: int = 1, *, escaped: bool = False, syntax: bool = False) -> None:
        """Record ``count`` characters against the current stack and advance."""
        view = self._stack_view()
        for _ in range(count):
            ch = self.src[self.i]
            self.chars.append(
                CharState(self.i, ch, self.line, self.col, view, escaped=escaped, syntax=syntax)
            )
            self.i += 1
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1

    def _push(self, kind: str, opened_at: int, *, expand: bool = True,
              delimiter: str = "", strip_tabs: bool = False) -> _Frame:
        frame = _Frame(kind, opened_at, expand=expand, delimiter=delimiter,
                       strip_tabs=strip_tabs)
        self.stack.append(frame)
        return frame

    def _peek(self, offset: int = 0) -> str:
        j = self.i + offset
        return self.src[j] if j < self.n else ""

    @property
    def top(self) -> _Frame | None:
        return self.stack[-1] if self.stack else None

    @property
    def top_kind(self) -> str:
        return self.stack[-1].kind if self.stack else TOP

    # -- main loop --------------------------------------------------------

    def run(self, strict: bool) -> ScanResult:
        while self.i < self.n:
            kind = self.top_kind
            if kind == SQ:
                self._scan_single_quote()
            elif kind == ANSI:
                self._scan_ansi()
            elif kind == COMMENT:
                self._scan_comment()
            elif kind in (DQ, LOCALE):
                self._scan_double_quote()
            elif kind == HEREDOC:
                self._scan_heredoc_body()
            elif kind == PARAM:
                self._scan_param()
            elif kind == ARITH:
                self._scan_arith()
            else:  # TOP, CMDSUB, BACKTICK
                self._scan_command()

        for frame in self.stack:
            line, col = self._pos(frame.opened_at)
            if strict:
                raise UnterminatedError(frame.kind, line, col)
            self.unterminated.append((frame.kind, line, col))

        return ScanResult(self.src, self.chars, self.expansions, self.unterminated)

    # -- literal contexts -------------------------------------------------

    def _scan_single_quote(self) -> None:
        """No escapes exist inside single quotes. Not even for a quote."""
        ch = self._peek()
        if ch == "'":
            self._emit(syntax=True)
            self.stack.pop()
        else:
            if ch in "$`":
                self._note_inert(self.i)
            self._emit()

    def _scan_ansi(self) -> None:
        """$'...' — backslash escapes are honoured, ``$`` is not special."""
        ch = self._peek()
        if ch == "\\" and self.i + 1 < self.n:
            self._emit(syntax=True)
            self._emit(escaped=True)
        elif ch == "'":
            self._emit(syntax=True)
            self.stack.pop()
        else:
            if ch in "$`":
                self._note_inert(self.i)
            self._emit()

    def _scan_comment(self) -> None:
        if self._peek() == "\n":
            self.stack.pop()
            self._newline()
        else:
            self._emit()

    # -- double quotes ----------------------------------------------------

    def _scan_double_quote(self) -> None:
        ch = self._peek()
        if ch == "\\" and self._peek(1) in DQ_ESCAPABLE and self._peek(1) != "":
            # Only these are escapes. `\d` inside "..." stays a backslash and
            # a d, which is why "\d" survives the shell into a regex.
            self._note_escaped_dollar()
            self._emit(syntax=True)
            self._emit(escaped=True)
        elif ch == '"':
            self._emit(syntax=True)
            self.stack.pop()
        elif ch == "$" and self._try_dollar():
            return
        elif ch == "`":
            self._open_backtick()
        else:
            self._emit()

    # -- ${ } and $(( )) --------------------------------------------------

    def _scan_param(self) -> None:
        """${...}. Quotes are live inside the word part of ``${x:-'a b'}``."""
        ch = self._peek()
        frame = self.top
        assert frame is not None
        if ch == "\\" and self.i + 1 < self.n:
            self._emit(syntax=True)
            self._emit(escaped=True)
        elif ch == "{":
            frame.depth += 1
            self._emit()
        elif ch == "}":
            if frame.depth:
                frame.depth -= 1
                self._emit()
            else:
                self._emit(syntax=True)
                self.stack.pop()
        elif ch in "'\"" and frame.quotes_live:
            # Quotes inside ${x:-...} are only quotes when the expansion is
            # not itself double-quoted. `x=; echo "${x:-'a b'}"` prints the
            # single quotes; unquoted, the same line does not. Confirmed
            # against bash rather than assumed.
            self._push(SQ if ch == "'" else DQ, self.i)
            self._emit(syntax=True)
        elif ch == "$" and self._try_dollar():
            return
        elif ch == "`":
            self._open_backtick()
        elif ch == "\n":
            self._emit()
        else:
            self._emit()

    def _scan_arith(self) -> None:
        """$((...)). Quotes are not quotes here; parens must balance."""
        ch = self._peek()
        frame = self.top
        assert frame is not None
        if ch == "(":
            frame.depth += 1
            self._emit()
        elif ch == ")" and self._peek(1) == ")" and frame.depth == 0:
            self._emit(2, syntax=True)
            self.stack.pop()
        elif ch == ")":
            frame.depth -= 1
            self._emit()
        elif ch == "$" and self._try_dollar():
            return
        else:
            self._emit()

    # -- here-documents ---------------------------------------------------

    def _scan_heredoc_body(self) -> None:
        frame = self.top
        assert frame is not None
        if self.col == 1 and self._at_heredoc_end(frame):
            self.stack.pop()
            self._start_next_heredoc()
            return
        ch = self._peek()
        if ch == "\n":
            self._emit()
        elif not frame.expand:
            if ch in "$`":
                self._note_inert(self.i)
            self._emit()
        elif ch == "\\" and self._peek(1) in "$`\\\n" and self._peek(1) != "":
            self._note_escaped_dollar()
            self._emit(syntax=True)
            self._emit(escaped=True)
        elif ch == "$" and self._try_dollar():
            return
        elif ch == "`":
            self._open_backtick()
        else:
            self._emit()

    def _at_heredoc_end(self, frame: _Frame) -> bool:
        """Is the line starting at self.i exactly the closing delimiter?

        If so it is consumed here, as syntax: the delimiter line is not part
        of the document.
        """
        end = self.src.find("\n", self.i)
        line = self.src[self.i:] if end == -1 else self.src[self.i:end]
        candidate = line.lstrip("\t") if frame.strip_tabs else line
        if candidate != frame.delimiter:
            return False
        for _ in range(len(line)):
            self._emit(syntax=True)
        if end != -1:
            self._emit(syntax=True)  # the newline
        return True

    def _start_next_heredoc(self) -> None:
        """``cat <<A <<B`` stacks two bodies; start the next one now."""
        if self.pending_heredocs and self.top_kind != HEREDOC:
            delimiter, strip_tabs = self.pending_heredocs.pop(0)
            self._push(
                HEREDOC, self.i,
                expand=not _is_quoted_delimiter(delimiter),
                delimiter=_unquote(delimiter),
                strip_tabs=strip_tabs,
            )

    def _scan_heredoc_operator(self) -> None:
        self._end_word()
        self._emit(2, syntax=True)  # the <<
        strip_tabs = False
        if self._peek() == "-":
            strip_tabs = True
            self._emit(syntax=True)
        while self._peek() in " \t" and self._peek() != "":
            self._emit()
        # The delimiter word, quotes and all — the quotes are what decide
        # whether the body expands, which is the whole reason we read it.
        start = self.i
        while self.i < self.n and self.src[self.i] not in " \t\n;&|<>()":
            if self.src[self.i] in "'\"":
                close = self.src.find(self.src[self.i], self.i + 1)
                if close == -1:
                    break
                self._emit(close - self.i + 1)
                continue
            self._emit()
        delimiter = self.src[start:self.i]
        if delimiter:
            self.pending_heredocs.append((delimiter, strip_tabs))

    # -- command contexts -------------------------------------------------

    def _scan_command(self) -> None:
        """TOP, and the inside of $( ) and backticks: everything is live."""
        ch = self._peek()
        kind = self.top_kind

        if ch == "\n":
            self._newline()
            return
        if ch == "\\" and self.i + 1 < self.n:
            self._note_word_char()
            self._note_escaped_dollar()
            self._emit(syntax=True)
            self._emit(escaped=True)
            return
        if ch == "#" and not self.in_word:
            self._push(COMMENT, self.i)
            self._emit(syntax=True)
            return
        if ch in "'\"":
            self._note_word_char()
            self._push(SQ if ch == "'" else DQ, self.i)
            self._emit(syntax=True)
            return
        if ch == "`":
            if kind == BACKTICK:
                self._emit(syntax=True)
                self._close_command_frame()
            else:
                self._note_word_char()
                self._open_backtick()
            return
        if ch == "$":
            self._note_word_char()
            if not self._try_dollar():
                self._emit()
            return
        if ch == "<" and self._peek(1) == "<":
            if self._peek(2) == "<":
                self._end_word()
                self._emit(3)  # a here-string, which expands like any word
            else:
                self._scan_heredoc_operator()
            return
        if ch == "(":
            if kind == CMDSUB:
                frame = self.top
                assert frame is not None
                frame.depth += 1
            self._end_word()
            self._emit()
            self.at_command_start = True
            return
        if ch == ")":
            if kind == CMDSUB:
                frame = self.top
                assert frame is not None
                if frame.depth == 0:
                    self._end_word()
                    self._emit(syntax=True)
                    self._close_command_frame()
                    return
                frame.depth -= 1
            self._end_word()
            self._emit()
            self.at_command_start = True
            return
        if ch in ";&|":
            self._end_word()
            self._emit()
            self.at_command_start = True
            return
        if ch in " \t":
            self._end_word()
            self._emit()
            return
        self._note_word_char()
        self._emit()

    def _open_backtick(self) -> None:
        start = self.i
        stack = self._stack_view()
        reason = self._nosplit_reason()
        line, col = self.line, self.col
        frame = self._push(BACKTICK, start)
        self._emit(syntax=True)
        frame.saved_word = self._word_state()
        self._reset_word_state()
        self._defer(start, "backtick", line, col, stack, reason)

    def _close_command_frame(self) -> None:
        frame = self.stack.pop()
        if frame.saved_word is not None:
            self._restore_word_state(frame.saved_word)

    # -- word tracking ----------------------------------------------------
    #
    # This exists only to answer "is this bare expansion actually going to be
    # word-split?", because the honest answer is often no, and a tool that
    # cries wolf on `x=$y` is a tool people switch off.

    def _word_state(self) -> tuple:
        return (self.at_command_start, self.in_word, self.word_start,
                self.assignment_eq, self.in_double_bracket)

    def _reset_word_state(self) -> None:
        self.at_command_start = True
        self.in_word = False
        self.assignment_eq = -1
        self.in_double_bracket = False

    def _restore_word_state(self, saved: tuple) -> None:
        (self.at_command_start, self.in_word, self.word_start,
         self.assignment_eq, self.in_double_bracket) = saved

    def _note_word_char(self) -> None:
        if not self.in_word:
            self.in_word = True
            self.word_start = self.i
            if self.at_command_start:
                self._check_assignment()

    def _check_assignment(self) -> None:
        """Does the word starting here look like NAME=, NAME+= or NAME[i]=?

        If so, everything from the ``=`` to the end of the word is an
        assignment right-hand side, which the shell does not word-split.
        """
        j = self.i
        if not _is_name_start(self.src[j:j + 1]):
            return
        while j < self.n and _is_name_char(self.src[j]):
            j += 1
        if j < self.n and self.src[j] == "[":
            close = self.src.find("]", j)
            if close == -1:
                return
            j = close + 1
        if j < self.n and self.src[j] == "+":
            j += 1
        if j < self.n and self.src[j] == "=":
            self.assignment_eq = j

    def _end_word(self) -> None:
        if self.in_word:
            word = self.src[self.word_start:self.i]
            if self.assignment_eq >= 0:
                pass  # assignments precede the command name; stay at start
            elif self.at_command_start:
                if word == "[[":
                    self.in_double_bracket = True
                    self.at_command_start = False
                elif word not in ("!", "time"):
                    self.at_command_start = False
            elif word == "]]":
                self.in_double_bracket = False
        self.in_word = False
        self.assignment_eq = -1

    def _newline(self) -> None:
        self._end_word()
        self.at_command_start = True
        self.in_double_bracket = False
        self._emit()
        self._start_next_heredoc()

    def _nosplit_reason(self) -> str:
        if any(f.kind == ARITH for f in self.stack):
            return "evaluated arithmetically"
        if self.assignment_eq >= 0 and self.i > self.assignment_eq:
            return "assignment right-hand side"
        if self.in_double_bracket:
            return "inside [[ ]]"
        return ""

    # -- dollar -----------------------------------------------------------

    def _try_dollar(self) -> bool:
        """Handle a ``$`` at self.i. Returns False if it is a literal ``$``."""
        nxt = self._peek(1)
        start = self.i
        line, col = self.line, self.col
        stack = self._stack_view()
        reason = self._nosplit_reason()
        in_command_ctx = self.top_kind in COMMAND_KINDS

        if nxt == "(" and self._peek(2) == "(":
            self._push(ARITH, start)
            self._emit(3, syntax=True)
            self._defer(start, "arithmetic", line, col, stack, reason)
            return True
        if nxt == "(":
            frame = self._push(CMDSUB, start)
            self._emit(2, syntax=True)
            frame.saved_word = self._word_state()
            self._reset_word_state()
            self._defer(start, "command", line, col, stack, reason)
            return True
        if nxt == "{":
            frame = self._push(PARAM, start)
            frame.quotes_live = not quoted_in(stack)
            self._emit(2, syntax=True)
            self._defer(start, "braced", line, col, stack, reason)
            return True
        if nxt == "'" and in_command_ctx:
            # $'...' is ANSI-C quoting, but only where quoting can start.
            # Inside "..." the sequence $' is a literal dollar and a quote.
            self._push(ANSI, start)
            self._emit(2, syntax=True)
            return True
        if nxt == '"' and in_command_ctx:
            self._push(LOCALE, start)
            self._emit(2, syntax=True)
            return True
        if _is_name_start(nxt):
            self._emit(syntax=True)
            while self.i < self.n and _is_name_char(self.src[self.i]):
                self._emit()
            self._complete(start, "parameter", line, col, stack, reason)
            return True
        if nxt and nxt in SPECIAL_PARAMS:
            self._emit(syntax=True)
            self._emit()
            self._complete(start, "parameter", line, col, stack, reason)
            return True
        return False

    # -- inert dollars ----------------------------------------------------
    #
    # A `$x` that will not expand is the single most useful row in the
    # report — it is the one people are surprised by — so somewhere that
    # cannot expand still has to notice the shape and record it.

    def _measure_inert(self, start: int) -> tuple[int, str] | None:
        """Extent and form of the expansion ``start`` would have been.

        Naive matching, because nothing here is really shell: inside single
        quotes ``'${a} $(b)'`` is just text, and the only job is to quote
        back the span the reader thinks is an expansion.
        """
        src, n = self.src, self.n
        if src[start] == "`":
            close = src.find("`", start + 1)
            return (close + 1, "backtick") if close != -1 else None
        nxt = src[start + 1] if start + 1 < n else ""
        if nxt == "{":
            end = self._match_delim(start + 1, "{", "}")
            return (end, "braced") if end else None
        if nxt == "(":
            arith = start + 2 < n and src[start + 2] == "("
            end = self._match_delim(start + 1, "(", ")")
            return (end, "arithmetic" if arith else "command") if end else None
        if _is_name_start(nxt):
            j = start + 1
            while j < n and _is_name_char(src[j]):
                j += 1
            return j, "parameter"
        if nxt and nxt in SPECIAL_PARAMS:
            return start + 2, "parameter"
        return None

    def _match_delim(self, start: int, open_ch: str, close_ch: str) -> int | None:
        depth = 0
        for j in range(start, self.n):
            if self.src[j] == open_ch:
                depth += 1
            elif self.src[j] == close_ch:
                depth -= 1
                if depth == 0:
                    return j + 1
        return None

    def _note_escaped_dollar(self) -> None:
        """Called sitting on a backslash: record what it just defused."""
        if self._peek(1) in "$`":
            self._note_inert(self.i + 1, inert_reason="escaped by a backslash")

    def _note_inert(self, start: int, *, inert_reason: str = "") -> None:
        """Record a would-be expansion at ``start`` without consuming it."""
        measured = self._measure_inert(start)
        if measured is None:
            return
        end, form = measured
        line, col = self._pos(start)
        self.expansions.append(
            Expansion(start, end, self.src[start:end], form, line, col,
                      self._stack_view(), "", inert_reason)
        )

    def _complete(self, start: int, form: str, line: int, col: int,
                  stack: tuple[Ctx, ...], reason: str) -> None:
        self.expansions.append(
            Expansion(start, self.i, self.src[start:self.i], form, line, col,
                      stack, reason)
        )

    def _defer(self, start: int, form: str, line: int, col: int,
               stack: tuple[Ctx, ...], reason: str) -> None:
        """Register an expansion whose extent is not known yet.

        The closing delimiter has not been seen, so ``end`` and ``text`` get
        patched in after the scan. Keeping a placeholder in the list now
        preserves source order, which is what a reader wants.
        """
        self.deferred.append((len(self.expansions), start))
        self.expansions.append(
            Expansion(start, start, "", form, line, col, stack, reason)
        )


def _is_quoted_delimiter(word: str) -> bool:
    """``<<'EOF'``, ``<<"EOF"`` and ``<<\\EOF`` all stop the body expanding."""
    return "'" in word or '"' in word or "\\" in word


def _unquote(word: str) -> str:
    out = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch in "'\"":
            close = word.find(ch, i + 1)
            if close == -1:
                out.append(word[i + 1:])
                break
            out.append(word[i + 1:close])
            i = close + 1
        elif ch == "\\" and i + 1 < len(word):
            out.append(word[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def scan(source: str, *, strict: bool = True) -> ScanResult:
    """Scan ``source``, returning per-character state and every expansion.

    With ``strict``, an unclosed quote or substitution raises
    :class:`UnterminatedError`. Without it the same thing is reported on
    :attr:`ScanResult.unterminated` and scanning continues to the end.
    """
    scanner = _Scanner(source)
    result = scanner.run(strict)
    _patch_extents(scanner, result)
    return result


def _patch_extents(scanner: _Scanner, result: ScanResult) -> None:
    """Fill in end/text for the expansions that open a frame.

    The scan knows where each ``$(``/``${``/``$((``/`` ` `` opened; the
    matching close is found here by asking which characters ended up inside
    that frame, since every character already carries the stack it was read
    under.
    """
    last_index: dict[int, int] = {}
    for state in result.chars:
        for ctx in state.stack:
            last_index[ctx.opened_at] = state.index

    for slot, start in scanner.deferred:
        old = result.expansions[slot]
        end = last_index.get(start, start) + 1
        result.expansions[slot] = Expansion(
            start, end, scanner.src[start:end], old.form, old.line,
            old.column, old.stack, old.nosplit_reason,
        )
