"""Login: handle -> compile-error challenge with a countdown -> verify (FR-AUTH-*)."""

from __future__ import annotations

import webbrowser

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from gcboard.client.api import ApiError, save_token
from gcboard.shared.schemas import ChallengeResponse

from .common import fmt_duration


class LoginScreen(Screen):
    BINDINGS = [
        Binding("o", "open_problem", "open problem"),
        Binding("v", "verify", "verify"),
        Binding("escape", "restart", "change handle"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.challenge: ChallengeResponse | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b $accent]> gcboard login[/]", classes="title")
        yield Static("Enter your Codeforces handle.", id="intro")
        yield Input(placeholder="codeforces handle", id="handle", max_length=24)
        yield Static("", id="challenge")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1, self._tick)
        self.query_one("#handle", Input).focus()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in ("open_problem", "verify", "restart"):
            return self.challenge is not None
        return True

    def _status(self, text: str, error: bool = False) -> None:
        self.query_one("#status", Static).update(f"[$error]{escape(text)}[/]" if error else escape(text))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        handle = event.value.strip()
        if handle:
            self._status("Asking Codeforces...")
            self.request_challenge(handle)

    @work(exclusive=True, group="request_challenge")
    async def request_challenge(self, handle: str) -> None:
        try:
            self.challenge = await self.app.api.challenge(handle)
        except ApiError as exc:
            self._status(exc.message, error=True)
            return
        inp = self.query_one("#handle", Input)
        inp.value = self.challenge.cf_handle
        inp.disabled = True
        self._status("")
        self._render_challenge()
        self.refresh_bindings()

    def _render_challenge(self) -> None:
        c = self.challenge
        if c is None:
            self.query_one("#challenge", Static).update("")
            return
        left = c.expires_at - self.app.api.server_now()
        timer = f"[b]{fmt_duration(left)}[/] left" if left > 0 else "[$error]time is up - press escape for a new challenge[/]"
        self.query_one("#challenge", Static).update(
            f"Prove you own [b]{escape(c.cf_handle)}[/]:\n\n"
            f"  1. Open problem [b]{c.contest_id}{c.index}[/] {escape(c.name)}\n"
            f"     {c.url}   (press [b]o[/] to open it)\n"
            f"  2. Submit any code that fails with [b]COMPILATION_ERROR[/].\n"
            f"  3. Press [b]v[/] to verify.\n\n"
            f"  {timer}"
        )

    def _tick(self) -> None:
        if self.challenge is not None:
            self._render_challenge()

    def action_open_problem(self) -> None:
        if self.challenge:
            webbrowser.open(self.challenge.url)

    def action_restart(self) -> None:
        self.challenge = None
        inp = self.query_one("#handle", Input)
        inp.disabled = False
        inp.focus()
        self._render_challenge()
        self._status("")
        self.refresh_bindings()

    def action_verify(self) -> None:
        if self.challenge:
            self._status("Checking your Codeforces submissions...")
            self.verify(self.challenge.cf_handle)

    @work(exclusive=True, group="verify")
    async def verify(self, handle: str) -> None:
        try:
            res = await self.app.api.verify(handle)
        except ApiError as exc:
            self._status(exc.message, error=True)
            return
        save_token(res.token)
        self.app.api.token = res.token
        self.app.logged_in(res.cf_handle)
