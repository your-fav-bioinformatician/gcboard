"""Shared widgets and formatting helpers for the client screens."""

from __future__ import annotations

import time
from datetime import datetime

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

MIN_WIDTH, MIN_HEIGHT = 80, 24


def fmt_duration(seconds: float) -> str:
    """H:MM:SS, or M:SS under an hour."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_local(epoch: float) -> str:
    """Server timestamp shown in the member's local time."""
    return datetime.fromtimestamp(epoch).strftime("%a %d %b %H:%M")


def row(label: str, value: str) -> str:
    return f"[$text-muted]{label:<12}[/] {value}"


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no prompt. Dismisses with True on y, False on n or escape."""

    BINDINGS = [
        Binding("y", "answer(True)", "yes"),
        Binding("n", "answer(False)", "no"),
        Binding("escape", "answer(False)", "cancel", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(escape(self.message), id="dialog-text")
            yield Static("[b]y[/] yes    [b]n[/] no", id="dialog-keys")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class TooSmallScreen(ModalScreen[None]):
    """Shown while the terminal is below 80x24 (NFR-COMPAT-1)."""

    def compose(self) -> ComposeResult:
        yield Static(
            f"Please enlarge your terminal to at least {MIN_WIDTH}x{MIN_HEIGHT}.", id="too-small"
        )


class Ticker:
    """Remembers when data was fetched, for 'updated Ns ago' labels."""

    def __init__(self) -> None:
        self.at: float | None = None

    def mark(self) -> None:
        self.at = time.monotonic()

    def label(self) -> str:
        if self.at is None:
            return ""
        return f"updated {int(time.monotonic() - self.at)}s ago"
