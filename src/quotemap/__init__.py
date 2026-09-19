"""quotemap — what the shell thinks your quoting means."""

from .lexer import (
    CharState,
    Ctx,
    Expansion,
    ScanResult,
    UnterminatedError,
    scan,
)

__all__ = [
    "CharState",
    "Ctx",
    "Expansion",
    "ScanResult",
    "UnterminatedError",
    "scan",
]
__version__ = "0.1.0"
