"""Leaderboard (home): week / all-time table (FR-BOARD-*)."""

from __future__ import annotations

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from gcboard.client.api import ApiError

REFRESH_SECONDS = 30


class LeaderboardScreen(Screen):
    BINDINGS = [
        Binding("w", "toggle_period", "week/all-time"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.period = "week"

    def compose(self) -> ComposeResult:
        yield Static("", id="board-title", classes="title")
        yield DataTable(id="board", cursor_type="row", zebra_stripes=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("#", width=4)
        table.add_column("handle", width=24)
        table.add_column("pts", width=8)
        table.add_column("solved", width=7)
        table.add_column("streak", width=7)
        self.load()
        self.set_interval(REFRESH_SECONDS, self.load)

    def on_screen_resume(self) -> None:
        self.load()

    def action_toggle_period(self) -> None:
        self.period = "all" if self.period == "week" else "week"
        self.load()

    def action_refresh(self) -> None:
        self.load()

    @work(exclusive=True, group="load")
    async def load(self) -> None:
        status = self.query_one("#status", Static)
        try:
            data = await self.app.api.leaderboard(self.period)
        except ApiError as exc:
            status.update(f"[$error]{escape(exc.message)}[/]")
            return
        flag = "--week" if data.period == "week" else "--all"
        self.query_one("#board-title", Static).update(
            f"[b $accent]> leaderboard {flag}[/]   [$text-muted]{escape(data.label)}[/]"
        )
        table = self.query_one(DataTable)
        table.clear()
        for r in data.rows:
            pts = f"~{r.points}" if r.provisional else str(r.points)
            table.add_row(str(r.rank), r.cf_handle, pts, str(r.solved), f"{r.streak}d", key=r.cf_handle)
        if data.rows:
            note = "~ = provisional (task still open)" if any(r.provisional for r in data.rows) else ""
        else:
            note = "Nobody has scored yet in this period."
        status.update(f"[$text-muted]{note}[/]")
