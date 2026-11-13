"""gcboard terminal client (entry point: `gcboard`)."""

from __future__ import annotations

from textual import events, work
from textual.app import App
from textual.binding import Binding
from textual.geometry import Size
from textual.theme import Theme

from gcboard import __version__

from .api import Api, ApiError, clear_token, load_token, server_url, version_tuple
from .screens.common import MIN_HEIGHT, MIN_WIDTH, TooSmallScreen
from .screens.leaderboard import LeaderboardScreen
from .screens.login import LoginScreen
from .screens.profile import ProfileScreen
from .screens.project import ProjectScreen
from .screens.rules import RulesScreen
from .screens.today import TodayScreen

GCBOARD_THEME = Theme(
    name="gcboard",
    primary="#3fb950",
    secondary="#8b949e",
    accent="#3fb950",
    foreground="#d0d7de",
    background="#0d1117",
    surface="#0d1117",
    panel="#161b22",
    success="#3fb950",
    warning="#d29922",
    error="#f85149",
    dark=True,
)

CSS = """
Screen { padding: 0 1; }
.title { height: 1; margin: 1 0 1 0; }
#status { height: auto; margin-top: 1; }
#board { height: 1fr; }
#handle { width: 40; margin-bottom: 1; }
#today, #challenge, #rules, #profile, #project-info { height: auto; }
#repo-url { width: 70; margin: 1 0; }
#project-spec { margin-top: 1; }
ConfirmScreen, TooSmallScreen { align: center middle; }
#dialog { width: 60; height: auto; border: round $accent; padding: 1 2; background: $panel; }
#dialog-keys { margin-top: 1; color: $text-muted; }
#too-small { width: auto; height: auto; padding: 1 2; border: round $warning; }
"""

class GcboardApp(App):
    TITLE = "gcboard"
    CSS = CSS
    ENABLE_COMMAND_PALETTE = False  # its footer hint uses an icon glyph (SRD 9.1)
    MODES = {
        "login": LoginScreen,
        "board": LeaderboardScreen,
        "today": TodayScreen,
        "project": ProjectScreen,
        "me": ProfileScreen,
        "rules": RulesScreen,
    }
    BINDINGS = [
        Binding("l", "goto('board')", "board"),
        Binding("t", "goto('today')", "today"),
        Binding("p", "goto('project')", "project"),
        Binding("m", "goto('me')", "me"),
        Binding("question_mark", "goto('rules')", "rules"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.api = Api(server_url(), load_token())
        self.handle: str | None = None

    def on_mount(self) -> None:
        self.register_theme(GCBOARD_THEME)
        self.theme = "gcboard"
        self.startup()

    @work(exclusive=True)
    async def startup(self) -> None:
        try:
            info = await self.api.sync_clock()
        except ApiError as exc:
            self.exit(return_code=1, message=f"gcboard: {exc.message} ({self.api.base_url})")
            return
        if version_tuple(__version__) < version_tuple(info.min_client_version):
            self.exit(
                return_code=1,
                message=(
                    f"gcboard {__version__} is too old; the server needs {info.min_client_version} or newer.\n"
                    "Update with: uv tool upgrade gcboard   (or: pipx upgrade gcboard)"
                ),
            )
            return
        if self.api.token:
            try:
                me = await self.api.me()
                self.logged_in(me.cf_handle)
                return
            except ApiError as exc:
                if exc.status in (401, 403):
                    clear_token()
                    self.api.token = None
                    if exc.status == 403:
                        self.notify(exc.message, severity="error")
                else:
                    self.exit(return_code=1, message=f"gcboard: {exc.message}")
                    return
        self.go("login")

    def go(self, mode: str) -> None:
        """Switch screens; each mode has its own stack, so re-check the size."""
        self.switch_mode(mode)
        self.call_after_refresh(self.check_size)

    def logged_in(self, handle: str) -> None:
        self.handle = handle
        self.sub_title = handle
        self.go("board")
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "goto":
            return self.handle is not None
        return True

    def action_goto(self, mode: str) -> None:
        if self.current_mode != mode:
            self.go(mode)
        else:  # e.g. `l` while a profile is open on top of the board
            while len(self.screen_stack) > 1:
                self.pop_screen()
            self.call_after_refresh(self.check_size)

    def on_resize(self, event: events.Resize) -> None:
        self.check_size(event.size)  # self.size is not updated yet here

    def check_size(self, size: Size | None = None) -> None:
        size = size or self.size
        small = size.width < MIN_WIDTH or size.height < MIN_HEIGHT
        showing = any(isinstance(s, TooSmallScreen) for s in self.screen_stack)
        if small and not showing:
            self.push_screen(TooSmallScreen())
        elif not small and showing:
            while isinstance(self.screen, TooSmallScreen):
                self.pop_screen()

    async def on_unmount(self) -> None:
        await self.api.aclose()


def main() -> None:
    GcboardApp().run()


if __name__ == "__main__":
    main()
