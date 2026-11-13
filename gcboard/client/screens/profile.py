"""Profile: a member's stats (FR-PROF-1). Opened with `m` for yourself, or
with enter on a leaderboard row for anyone else."""

from __future__ import annotations

from datetime import datetime

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from gcboard.client.api import ApiError
from gcboard.shared.schemas import Profile

from .common import fmt_duration, row


def render_profile(p: Profile) -> str:
    rating = f"{p.cf_rating} ({escape(p.cf_rank or 'unrated')})" if p.cf_rating is not None else "unrated"
    avg = fmt_duration(p.avg_solve_seconds) if p.avg_solve_seconds is not None else "-"
    fastest = fmt_duration(p.fastest_solve_seconds) if p.fastest_solve_seconds is not None else "-"
    best_finish = f"#{p.best_weekly_finish}" if p.best_weekly_finish is not None else "-"
    lines = [
        row("rating", rating),
        row("joined", datetime.fromtimestamp(p.joined_at).strftime("%d %b %Y")),
        row("streak", f"[b]{p.current_streak}d[/] current · {p.best_streak}d best"),
        row("solved", f"[b]{p.solved_week}[/] this week · {p.solved_all} all time"),
        row("solve time", f"avg {avg} · fastest {fastest}"),
        row("weekly wins", f"[b]{p.weekly_wins}[/] · best finish {best_finish}"),
        "",
        "[b $accent]projects[/]",
    ]
    if not p.projects:
        lines.append("[$text-muted]  no projects yet[/]")
    else:
        lines.append(f"[$text-muted]  {'title':<28} {'rank':>5} {'writeup':>8} {'history':>8} {'pts':>6}[/]")
        for pr in p.projects:
            rank = f"#{pr.submit_rank}" if pr.submit_rank else "-"
            score = f"{pr.writeup_score:g}" if pr.writeup_score is not None else "-"
            hist = f"{pr.history_factor:.2f}" if pr.history_factor is not None else "-"
            pts = f"~{pr.points}" if pr.provisional else str(pr.points)
            lines.append(f"  {escape(pr.title[:28]):<28} {rank:>5} {score:>8} {hist:>8} {pts:>6}")
    return "\n".join(lines)


class ProfileScreen(Screen):
    BINDINGS = [
        Binding("r", "refresh", "refresh"),
        Binding("escape", "back", "back"),
    ]

    def __init__(self, handle: str | None = None) -> None:
        """With no handle, shows the logged-in member's own profile."""
        super().__init__()
        self.handle = handle

    def compose(self) -> ComposeResult:
        yield Static("", id="profile-title", classes="title")
        yield Static("Loading...", id="profile")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    def on_screen_resume(self) -> None:
        self.load()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "back":
            return self.handle is not None  # only when opened from the board
        return True

    def action_refresh(self) -> None:
        self.load()

    def action_back(self) -> None:
        self.dismiss()

    @work(exclusive=True, group="load")
    async def load(self) -> None:
        handle = self.handle or self.app.handle
        if handle is None:
            return
        self.query_one("#profile-title", Static).update(f"[b $accent]> profile {escape(handle)}[/]")
        try:
            p = await self.app.api.profile(handle)
        except ApiError as exc:
            self.query_one("#status", Static).update(f"[$error]{escape(exc.message)}[/]")
            return
        self.query_one("#status", Static).update("")
        self.query_one("#profile-title", Static).update(f"[b $accent]> profile {escape(p.cf_handle)}[/]")
        self.query_one("#profile", Static).update(render_profile(p))
