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
            "[b $accent]Writeup[/] (scored by plain text rules, no AI)",
            f"  {w['time_limit_minutes']} minutes from submitting, locked screen, no pasting, autosaved every"
            f" {w['autosave_seconds']}s.",
            f"  Saves growing faster than {w['max_chars_per_second']} characters/second"
            f" (+{w['burst_allowance_chars']}) are rejected.",
            f"  Minimum words: Q1 {w['min_words'][0]}, Q2 {w['min_words'][1]}, Q3 {w['min_words'][2]},"
            f" Q4 {w['min_words'][3]}.",
            f"  Score 0 if any answer has under {w['gate_min_words']} words, the total is under"
            f" {w['gate_min_total']}, or two answers share over {w['gate_max_jaccard']:.0%} of their words.",
            "  completeness       20  x average of min(1, words / minimum)",
            f"  grounding          30  15 for naming {w['snippet_ids_target']} identifiers from the snippet in Q1,"
            f" 15 for {w['other_ids_target']} other repo identifiers in Q2-Q4",
            "  change relevance   25  x share of the project's keyword groups used in Q3",
            f"  variety            10  x min(1, type-token ratio of each answer's first {w['ttr_window']} words"
            f" / {w['ttr_target']:g})",
            f"  identifier density 10  full at <= {w['density_full']:.0%} code-like words, 0 at"
            f" >= {w['density_zero']:.0%}",
            f"  sentences           5  average {w['sentence_min']}-{w['sentence_max']} words,"
            f" none over {w['sentence_hard_max']}",
            f"  Entries need {p['pass_threshold']:g}+ to qualify for project points.",
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
