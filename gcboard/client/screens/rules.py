"""Rules: the scoring formulas and the server's current config values."""

from __future__ import annotations

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from gcboard.client.api import ApiError
from gcboard.shared.schemas import RulesResponse


def render_rules(r: RulesResponse) -> str:
    t, p, h, w = r.tasks, r.projects, r.history, r.writeup
    bonus = t["runtime_bonus"]
    days = "Mon Tue Wed Thu Fri Sat Sun".split()
    sched = "  ".join(f"{d} {x}" for d, x in zip(days, t["rating_by_weekday"]))
    return "\n".join(
        [
            "[b $accent]Daily task[/]",
            f"  Posted at {r.daily_post_time} ({escape(r.timezone)}); open until the next post.",
            f"  Rating: {sched}",
            "  Your timer starts when you press start. Your first AC on that exact problem counts.",
            "",
            "  solve_rank    = rank by solve time among solvers (fastest = 1)",
            f"  base          = max({t['base_min']}, {t['base_max']} - {t['base_step']} x (solve_rank - 1))",
            f"  runtime_bonus = {bonus[0]} / {bonus[1]} / {bonus[2]} for the 3 lowest AC runtimes",
            f"  penalty       = min({t['penalty_cap']}, {t['penalty_per_reject']} x rejected submissions)"
            "  (compile errors are free)",
            f"  points        = max({t['min_task_points']}, base + runtime_bonus - penalty)",
            "",
            "[b $accent]Project[/]",
            f"  Entries need a writeup score of at least {p['pass_threshold']:g} to qualify.",
            f"  speed_points   = max({p['speed_min']:g}, {p['speed_max']:g} x {p['speed_decay']:g}^(submit_rank - 1))",
            "  project_points = round(speed_points x writeup_score / 100 x history_factor)",
            "",
            "[b $accent]Commit history factor[/]",
            f"  factor = min(1.0, {h['base']:g} + {h['per_active_interval']:g} x active_intervals)",
            f"  x {h['dump_multiplier']:g} if one interval added more than {h['dump_threshold']:.0%} of the final code;"
            f" never below {h['floor']:g}",
            "",
            "[b $accent]Writeup[/]",
            f"  {w['time_limit_minutes']} minutes, locked screen, no pasting. Minimum words per answer:"
            f" {', '.join(map(str, w['min_words']))}.",
            "",
            "[b $accent]Leaderboard ties[/]",
            "  1. more tasks solved  2. lower total solve time  3. reached the total first",
        ]
    )


class RulesScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Static("[b $accent]> rules[/]", classes="title")
        with VerticalScroll():
            yield Static("Loading...", id="rules")
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    @work(exclusive=True, group="load")
    async def load(self) -> None:
        try:
            self.query_one("#rules", Static).update(render_rules(await self.app.api.rules()))
        except ApiError as exc:
            self.query_one("#rules", Static).update(f"[$error]{escape(exc.message)}[/]")
