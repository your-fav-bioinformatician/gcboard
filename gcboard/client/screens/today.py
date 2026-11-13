"""Today's task: hidden until started, then a live timer anchored to the
server's started_at (FR-TASK-5, -6, -10, -11)."""

from __future__ import annotations

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from gcboard.client.api import ApiError
from gcboard.client.browser import open_url
from gcboard.shared.schemas import TodayResponse

from .common import ConfirmScreen, fmt_duration, fmt_local, row

REFRESH_SECONDS = 30


class TodayScreen(Screen):
    BINDINGS = [
        Binding("s", "start", "start task"),
        Binding("o", "open_problem", "open in browser"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.data: TodayResponse | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b $accent]> today[/]", id="today-title", classes="title")
        yield Static("Loading...", id="today")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.load()
        self.set_interval(1, self.render_task)
        self.set_interval(REFRESH_SECONDS, self.load)

    def on_screen_resume(self) -> None:
        self.load()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        task = self.data.task if self.data else None
        if action == "start":
            return task is not None and task.attempt is None
        if action == "open_problem":
            return task is not None and task.problem is not None
        return True

    @work(exclusive=True, group="load")
    async def load(self) -> None:
        try:
            self.data = await self.app.api.today()
        except ApiError as exc:
            self.query_one("#status", Static).update(f"[$error]{escape(exc.message)}[/]")
            return
        self.query_one("#status", Static).update("")
        self.render_task()
        self.refresh_bindings()

    def render_task(self) -> None:
        body = self.query_one("#today", Static)
        if self.data is None:
            return
        task = self.data.task
        if task is None:
            body.update(escape(self.data.message or "No task right now."))
            return
        now = self.app.api.server_now()
        self.query_one("#today-title", Static).update(f"[b $accent]> today --task {task.task_date}[/]")
        lines = [
            row("rating", str(task.rating)),
            row("closes", f"{fmt_local(task.closes_at)}  (in {fmt_duration(task.closes_at - now)})"),
        ]
        a, p = task.attempt, task.problem
        if a is None or p is None:
            lines += [
                row("problem", "[b]hidden[/]"),
                "",
                "Press [b]s[/] to reveal the problem. The timer starts immediately",
                "and cannot be paused or restarted.",
            ]
        else:
            lines.append(row("problem", f"[b]{p.contest_id}{p.index}[/]  {escape(p.name)}"))
            lines.append(row("url", f"{p.url}   (press [b]o[/] to open)"))
            if a.solved and a.solve_seconds is not None:
                pts = f"~{a.points}" if a.provisional else str(a.points)
                lines.append(row("solve time", f"[b]{fmt_duration(a.solve_seconds)}[/]"))
                lines.append(
                    row("status", f"[$success]solved[/] · {a.runtime_ms} ms · {a.rejected_count} rejected · "
                                  f"[b]{pts}[/] pts")
                )
                if a.provisional:
                    lines.append("[$text-muted]~ provisional: ranks can change until the task closes[/]")
            else:
                lines.append(row("elapsed", f"[b]{fmt_duration(now - a.started_at)}[/]"))
                lines.append(row("status", "waiting for AC (checked every 2 minutes)"))
        body.update("\n".join(lines))

    def action_refresh(self) -> None:
        self.load()

    def action_open_problem(self) -> None:
        if self.data and self.data.task and self.data.task.problem:
            open_url(self.data.task.problem.url)

    def action_start(self) -> None:
        def answered(yes: bool | None) -> None:
            if yes:
                self.start()

        self.app.push_screen(
            ConfirmScreen("Start today's task? The timer starts now and can't be paused or restarted."),
            answered,
        )

    @work(exclusive=True, group="start")
    async def start(self) -> None:
        try:
            self.data = await self.app.api.start_task()
        except ApiError as exc:
            self.query_one("#status", Static).update(f"[$error]{escape(exc.message)}[/]")
            return
        self.render_task()
        self.refresh_bindings()
