"""Writeup: the locked, timed, paste-free screen (FR-WRITE-*, SRD 9.2).

While a writeup is open the app shows this screen and nothing else; the app
blocks navigation and quitting. The deadline lives on the server: closing
the app does not pause it, and reopening comes straight back here.
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath

from rich.markup import escape
from rich.syntax import Syntax
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Static, TabbedContent, TabPane, TextArea

from gcboard.client.api import ApiError
from gcboard.shared.schemas import WriteupResult, WriteupSession

from .common import ConfirmScreen, fmt_duration

FINAL_SAVE_SECONDS = 3  # last save attempt this long before the deadline


def count_words(text: str) -> int:
    """Same rule as the server: whitespace-separated tokens with a letter."""
    return sum(1 for t in text.split() if any(c.isalpha() for c in t))


class NoPasteTextArea(TextArea):
    """A TextArea that refuses pasted text (FR-WRITE-4): both terminal
    bracketed paste and the editor's own paste action."""

    class PasteBlocked(Message):
        pass

    async def _on_paste(self, event: events.Paste) -> None:
        event.prevent_default()  # skip TextArea's own paste handler
        event.stop()
        self.post_message(self.PasteBlocked())

    def action_paste(self) -> None:
        self.post_message(self.PasteBlocked())


class WriteupScreen(Screen):
    BINDINGS = [
        Binding("tab", "move(1)", "next answer", priority=True),
        Binding("shift+tab", "move(-1)", "previous answer", priority=True),
        Binding("ctrl+s", "submit", "submit writeup", priority=True),
        # ctrl+n / ctrl+p scroll the code; ctrl+down / ctrl+up also work where the OS
        # doesn't take them (macOS uses them for Mission Control). Plain key labels:
        # Textual would draw arrow glyphs, which SRD 9.1 rules out.
        Binding("ctrl+n", "scroll_snippet(3)", "scroll code", priority=True, key_display="^n/^p"),
        Binding("ctrl+p", "scroll_snippet(-3)", "scroll code up", priority=True, show=False),
        Binding("ctrl+down", "scroll_snippet(3)", "scroll code", priority=True, show=False),
        Binding("ctrl+up", "scroll_snippet(-3)", "scroll code up", priority=True, show=False),
    ]

    def __init__(self, session: WriteupSession) -> None:
        super().__init__()
        self.session = session
        self._saved = list(session.answers)  # last text the server accepted
        self._saved_at: float | None = None
        self._final_saved = False
        self._ended = False

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        s = self.session
        yield Static("", id="writeup-title", classes="title")
        with TabbedContent(id="answers"):
            for i, question in enumerate(s.questions):
                with TabPane(self._label(i, s.answers[i]), id=f"q{i + 1}"):
                    yield Static(escape(f"Q{i + 1}. {question}"), classes="question")
                    if i == 0:
                        with VerticalScroll(id="snippet"):
                            yield Static(self._syntax(), id="snippet-code")
                    yield NoPasteTextArea(
                        s.answers[i], id=f"a{i + 1}", soft_wrap=True, tab_behavior="focus",
                        max_checkpoints=50, classes="answer",
                    )
        yield Static("", id="status")
        yield Footer()

    def _syntax(self) -> Syntax:
        snip = self.session.snippet
        lexer = Syntax.guess_lexer(PurePosixPath(snip.path).name, code=snip.text)
        return Syntax(snip.text, lexer, line_numbers=True, start_line=snip.start, theme="ansi_dark", word_wrap=False)

    def _label(self, i: int, text: str) -> str:
        n, m = count_words(text), self.session.min_words[i]
        return f"Q{i + 1} {n}/{m}" + (" ok" if n >= m else "")

    def on_mount(self) -> None:
        self.query_one("#a1", TextArea).focus()
        self.set_interval(1, self._tick)
        self.set_interval(max(1, self.session.autosave_seconds), self._autosave)
        self._tick()

    # -- helpers -------------------------------------------------------------

    def answers(self) -> list[str]:
        return [self.query_one(f"#a{i + 1}", TextArea).text for i in range(4)]

    def _restore(self, answers: list[str]) -> None:
        for i, text in enumerate(answers):
            area = self.query_one(f"#a{i + 1}", TextArea)
            if area.text != text:
                area.load_text(text)
                area.move_cursor(area.document.end)
        self._saved = list(answers)

    def _status(self, text: str, level: str = "") -> None:
        color = {"error": "$error", "warning": "$warning"}.get(level)
        self.query_one("#status", Static).update(f"[{color}]{escape(text)}[/]" if color else escape(text))

    def remaining(self) -> float:
        return self.session.deadline - self.app.api.server_now()

    def _tick(self) -> None:
        if self._ended:
            return
        left = self.remaining()
        saved = f"saved {int(time.monotonic() - self._saved_at)}s ago" if self._saved_at else "not saved yet"
        warn = "$error" if left < 300 else "$accent"
        self.query_one("#writeup-title", Static).update(
            f"[b $accent]> writeup[/]   [b {warn}]{fmt_duration(left)}[/] left   [$text-muted]{saved} - no pasting[/]"
        )
        tabs = self.query_one(TabbedContent)
        for i, text in enumerate(self.answers()):
            tabs.get_tab(f"q{i + 1}").label = self._label(i, text)
        if left <= FINAL_SAVE_SECONDS and not self._final_saved:
            self._final_saved = True
            self.save()
        if left <= 0:
            self._ended = True
            for area in self.query(TextArea):
                area.read_only = True
            self._status("Time is up. Submitting your last saved text...")
            self.finish_after_deadline()

    # -- events --------------------------------------------------------------

    def on_no_paste_text_area_paste_blocked(self, _msg: NoPasteTextArea.PasteBlocked) -> None:
        self._status("Pasting is disabled in the writeup. Type your answer.", "warning")

    def action_move(self, step: int) -> None:
        tabs = self.query_one(TabbedContent)
        current = int((tabs.active or "q1")[1:])
        n = (current - 1 + step) % 4 + 1
        tabs.active = f"q{n}"
        self.query_one(f"#a{n}", TextArea).focus()

    def action_scroll_snippet(self, lines: int) -> None:
        """The snippet lives on Q1; small terminals show only part of it."""
        tabs = self.query_one(TabbedContent)
        if tabs.active != "q1":
            tabs.active = "q1"
            self.query_one("#a1", TextArea).focus()
        self.query_one("#snippet", VerticalScroll).scroll_relative(y=lines, animate=False)

    # -- saving --------------------------------------------------------------

    def _autosave(self) -> None:
        if not self._ended and self.answers() != self._saved:
            self.save()

    @work(exclusive=True, group="save")
    async def save(self) -> None:
        answers = self.answers()
        try:
            res = await self.app.api.save_draft(answers)
        except ApiError as exc:
            self._handle_save_error(exc)
            return
        self._saved = list(res.answers)
        self._saved_at = time.monotonic()
        self._status("")

    def _handle_save_error(self, exc: ApiError) -> None:
        if exc.error == "typing_too_fast":
            self._restore(exc.body.get("answers", self._saved))
            self._status(exc.message, "warning")
        elif exc.error == "deadline_passed":
            self.finish_after_deadline()
        elif exc.status == 0:
            self._status("Offline: your text is kept here and will be saved when the connection is back. "
                         "The deadline still applies.", "warning")
        else:
            self._status(exc.message, "error")

    # -- submitting ----------------------------------------------------------

    def action_submit(self) -> None:
        if self._ended:
            return
        short = [
            f"Q{i + 1} {count_words(a)}/{m}"
            for i, (a, m) in enumerate(zip(self.answers(), self.session.min_words))
            if count_words(a) < m
        ]
        if short:
            self._status("Not enough words yet: " + ", ".join(short), "warning")
            return

        def answered(yes: bool | None) -> None:
            if yes:
                self.submit()

        self.app.push_screen(ConfirmScreen("Submit your writeup? You can't change it afterwards."), answered)

    @work(exclusive=True, group="save")
    async def submit(self) -> None:
        try:
            result = await self.app.api.submit_writeup(self.answers())
        except ApiError as exc:
            self._handle_save_error(exc)
            return
        self._done(result)

    @work(exclusive=True, group="end")
    async def finish_after_deadline(self) -> None:
        self._ended = True
        try:
            result = await self.app.api.submit_writeup(None)
        except ApiError as exc:
            if exc.error == "no_writeup":  # already swept by the server
                self.app.writeup_ended(None)
            else:
                self._status(f"{exc.message} Retrying...", "warning")
                self.set_timer(5, self.finish_after_deadline)
            return
        self._done(result)

    def _done(self, result: WriteupResult) -> None:
        self._ended = True
        self.app.writeup_ended(result)
