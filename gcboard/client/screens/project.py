"""Project: spec, window, repo registration and verification, snapshots, and
submission (FR-PROJ-*, SRD 9.2)."""

from __future__ import annotations

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, Markdown, Static

from gcboard.client.api import ApiError
from gcboard.shared.schemas import EntryInfo, ProjectResponse

from .common import ConfirmScreen, fmt_countdown, fmt_local, row

REFRESH_SECONDS = 30
SUBMIT_WARNING = (
    "Submit your project now? This freezes your repository at its current commit. "
    "You can only submit once."
)


def render_entry(e: EntryInfo | None, max_loc: int) -> list[str]:
    if e is None:
        return [row("status", "not registered"), "",
                f"Press [b]e[/] and enter the URL of a new, empty public GitHub repo (at most {max_loc} source lines)."]
    lines = [row("repo", escape(e.repo_url))]
    if e.status == "pending":
        lines += [
            row("status", "[$warning]waiting for verification[/]"),
            "",
            "Prove the repo is yours: add a file named [b].gcboard[/] to the repo root containing exactly",
            "",
            f"    [b]{escape(e.verify_code or '')}[/]",
            "",
            "Commit and push it, then press [b]v[/]. Press [b]e[/] to use a different repo.",
        ]
        return lines
    factor = f"{e.history_factor:.2f}" if e.history_factor is not None else "-"
    if e.status == "registered":
        lines.append(row("status", "[$success]verified[/] - snapshots are taken daily; press [b]s[/] to submit"))
        lines.append(row("history", f"{factor} if you submitted now (1.00 = steady work over 3+ days)"))
    elif e.status == "unreachable":
        lines.append(row("status", "[$error]unreachable[/] - the last snapshot failed. Make the repo public again."))
        lines.append(row("history", f"{factor} so far"))
    elif e.status == "submitted":
        sha = (e.frozen_sha or "")[:7]
        lines.append(row("status", f"[$success]submitted[/] {fmt_local(e.submitted_at or 0)} at [b]{sha}[/]"))
        lines.append(row("history", f"[b]{factor}[/]"))
        lines.append("[$text-muted]Writeups and project points arrive in the next gcboard update.[/]")
    else:
        lines.append(row("status", "[$error]voided by the admin[/]"))
    if e.snapshots:
        lines += ["", "[b $accent]snapshots[/]"]
        for s in e.snapshots:
            unit = "line" if s.source_loc == 1 else "lines"
            lines.append(f"  {fmt_local(s.taken_at)}  {s.kind:<12} {s.head_sha[:7]}  {s.source_loc:>6} {unit}")
    return lines


def render_project(d: ProjectResponse, now: float) -> tuple[str, str]:
    """(title, info block)."""
    if d.project is None:
        info = ["No project is open right now."]
        if d.upcoming:
            u = d.upcoming
            info.append(
                f"Next: [b]{escape(u.title)}[/] opens {fmt_local(u.opens_at)} (in {fmt_countdown(u.opens_at - now)})."
            )
        return "> project", "\n".join(info)
    p = d.project
    lines = [
        row("window", f"{fmt_local(p.opens_at)} -> {fmt_local(p.closes_at)}"),
        row("closes in", f"[b]{fmt_countdown(p.closes_at - now)}[/]"),
        *render_entry(d.entry, d.max_registration_loc),
    ]
    return f"> project {escape(p.title)}", "\n".join(lines)


class ProjectScreen(Screen):
    BINDINGS = [
        Binding("e", "edit_repo", "register repo"),
        Binding("v", "verify", "verify"),
        Binding("s", "submit", "submit"),
        Binding("r", "refresh", "refresh"),
        Binding("escape", "cancel_edit", "cancel", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.data: ProjectResponse | None = None
        self._spec_shown: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b $accent]> project[/]", id="project-title", classes="title")
        with VerticalScroll(id="project-scroll"):
            yield Static("Loading...", id="project-info")
            yield Input(placeholder="https://github.com/you/your-repo", id="repo-url", max_length=200)
            yield Markdown("", id="project-spec")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#repo-url", Input).display = False
        self.load()
        self.set_interval(1, self.render_project)
        self.set_interval(REFRESH_SECONDS, self.load)

    def on_screen_resume(self) -> None:
        self.load()

    # -- state -------------------------------------------------------------

    @property
    def _entry(self) -> EntryInfo | None:
        return self.data.entry if self.data else None

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        is_open = bool(self.data and self.data.project)
        status = self._entry.status if self._entry else None
        if action == "edit_repo":
            return is_open and status in (None, "pending")
        if action == "verify":
            return is_open and status == "pending"
        if action == "submit":
            return is_open and status in ("registered", "unreachable")
        return True

    def _status(self, text: str, error: bool = False) -> None:
        self.query_one("#status", Static).update(f"[$error]{escape(text)}[/]" if error else escape(text))

    def _apply(self, data: ProjectResponse) -> None:
        self.data = data
        self.render_project()
        spec = data.project.spec_md if data.project else ""
        if spec != self._spec_shown:
            self._spec_shown = spec
            self.query_one("#project-spec", Markdown).update(spec)
        self.refresh_bindings()

    def render_project(self) -> None:
        if self.data is None:
            return
        title, info = render_project(self.data, self.app.api.server_now())
        self.query_one("#project-title", Static).update(f"[b $accent]{title}[/]")
        self.query_one("#project-info", Static).update(info)

    # -- actions -----------------------------------------------------------

    @work(exclusive=True, group="load")
    async def load(self) -> None:
        try:
            self._apply(await self.app.api.current_project())
        except ApiError as exc:
            self._status(exc.message, error=True)

    def action_refresh(self) -> None:
        self.load()

    def action_edit_repo(self) -> None:
        inp = self.query_one("#repo-url", Input)
        inp.display = True
        inp.value = self._entry.repo_url if self._entry else ""
        inp.focus()

    def action_cancel_edit(self) -> None:
        inp = self.query_one("#repo-url", Input)
        inp.display = False
        self.query_one("#project-scroll").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if url and self.data and self.data.project:
            self._status("Checking the repository...")
            self.register(self.data.project.id, url)

    @work(exclusive=True, group="action")
    async def register(self, project_id: int, url: str) -> None:
        try:
            data = await self.app.api.register_repo(project_id, url)
        except ApiError as exc:
            self._status(exc.message, error=True)
            return
        self.action_cancel_edit()
        self._status("")
        self._apply(data)

    def action_verify(self) -> None:
        if self.data and self.data.project:
            self._status("Reading your repository...")
            self.verify(self.data.project.id)

    @work(exclusive=True, group="action")
    async def verify(self, project_id: int) -> None:
        try:
            data = await self.app.api.verify_repo(project_id)
        except ApiError as exc:
            self._status(exc.message, error=True)
            return
        self._status("Verified. Snapshots of your repo are now taken every day.")
        self._apply(data)

    def action_submit(self) -> None:
        if not (self.data and self.data.project):
            return
        project_id = self.data.project.id

        def answered(yes: bool | None) -> None:
            if yes:
                self._status("Freezing your repository...")
                self.submit(project_id)

        self.app.push_screen(ConfirmScreen(SUBMIT_WARNING), answered)

    @work(exclusive=True, group="action")
    async def submit(self, project_id: int) -> None:
        try:
            data = await self.app.api.submit_project(project_id)
        except ApiError as exc:
            self._status(exc.message, error=True)
            return
        self._status("Submitted.")
        self._apply(data)
