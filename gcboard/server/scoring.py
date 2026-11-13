"""Scoring rules from SRD section 5, as pure functions with no I/O.

Every function here is deterministic: the same inputs always give the same
outputs, so any score can be recomputed and audited (design principle 2).
Ranks are dense: equal values share a rank, and the next value gets rank + 1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Hashable, Sequence


def round_half_up(x: float) -> int:
    """Round to the nearest integer, halves away from zero (for x >= 0).

    Python's round() uses banker's rounding; scores use the rule people expect.
    """
    return math.floor(x + 0.5)


def dense_ranks(values: Sequence) -> list[int]:
    """Dense rank of each value, ascending (smallest value gets rank 1)."""
    order = {v: i + 1 for i, v in enumerate(sorted(set(values)))}
    return [order[v] for v in values]


# --------------------------------------------------------------------------
# 5.1 Daily task points
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskRules:
    base_max: int = 100
    base_step: int = 10
    base_min: int = 30
    runtime_bonus: tuple[int, ...] = (10, 6, 3)
    penalty_per_reject: int = 5
    penalty_cap: int = 20
    min_task_points: int = 10


@dataclass(frozen=True)
class SolvedAttempt:
    key: Hashable
    solve_seconds: int
    runtime_ms: int
    rejected: int


@dataclass(frozen=True)
class TaskScore:
    solve_rank: int
    runtime_rank: int
    base: int
    runtime_bonus: int
    penalty: int
    points: int


def score_task(
    attempts: Sequence[SolvedAttempt], rules: TaskRules = TaskRules()
) -> dict[Hashable, TaskScore]:
    """Points for every solved attempt on one task. Unsolved attempts score 0
    and should not be passed in."""
    solve_ranks = dense_ranks([a.solve_seconds for a in attempts])
    runtime_ranks = dense_ranks([a.runtime_ms for a in attempts])
    out: dict[Hashable, TaskScore] = {}
    for a, sr, rr in zip(attempts, solve_ranks, runtime_ranks):
        base = max(rules.base_min, rules.base_max - rules.base_step * (sr - 1))
        bonus = rules.runtime_bonus[rr - 1] if rr <= len(rules.runtime_bonus) else 0
        penalty = min(rules.penalty_cap, rules.penalty_per_reject * a.rejected)
        points = max(rules.min_task_points, base + bonus - penalty)
        out[a.key] = TaskScore(sr, rr, base, bonus, penalty, points)
    return out


# --------------------------------------------------------------------------
# 5.2 Project points
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectRules:
    speed_max: float = 300
    speed_decay: float = 0.85
    speed_min: float = 90
    pass_threshold: float = 40


@dataclass(frozen=True)
class EntryInput:
    key: Hashable
    submitted_at: float
    writeup_score: float  # 0..100
    history_factor: float  # 0.5..1.0


@dataclass(frozen=True)
class ProjectScore:
    qualified: bool
    submit_rank: int | None
    speed_points: float
    points: int


def speed_points(submit_rank: int, rules: ProjectRules = ProjectRules()) -> float:
    return max(rules.speed_min, rules.speed_max * rules.speed_decay ** (submit_rank - 1))


def score_project(
    entries: Sequence[EntryInput], rules: ProjectRules = ProjectRules()
) -> dict[Hashable, ProjectScore]:
    """Unqualified entries score 0 and are left out of the submission ranking,
    so they do not push anyone else down."""
    qualified = [e for e in entries if e.writeup_score >= rules.pass_threshold]
    ranks = dict(zip((e.key for e in qualified), dense_ranks([e.submitted_at for e in qualified])))
    out: dict[Hashable, ProjectScore] = {}
    for e in entries:
        if e.key not in ranks:
            out[e.key] = ProjectScore(False, None, 0.0, 0)
            continue
        sp = speed_points(ranks[e.key], rules)
        pts = round_half_up(sp * (e.writeup_score / 100) * e.history_factor)
        out[e.key] = ProjectScore(True, ranks[e.key], sp, pts)
    return out


# --------------------------------------------------------------------------
# 5.4 Commit history factor
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryRules:
    base: float = 0.4
    per_active_interval: float = 0.2
    dump_threshold: float = 0.70
    dump_multiplier: float = 0.75
    floor: float = 0.5


@dataclass(frozen=True)
class Snapshot:
    head_sha: str
    source_loc: int


def history_factor(snapshots: Sequence[Snapshot], rules: HistoryRules = HistoryRules()) -> float:
    """Snapshots in the order taken: registration, dailies, submission."""
    pairs = list(zip(snapshots, snapshots[1:]))
    active = sum(1 for a, b in pairs if a.head_sha != b.head_sha and a.source_loc != b.source_loc)
    factor = min(1.0, rules.base + rules.per_active_interval * active)
    final_loc = snapshots[-1].source_loc if snapshots else 0
    largest_increase = max((b.source_loc - a.source_loc for a, b in pairs), default=0)
    if final_loc > 0 and largest_increase > rules.dump_threshold * final_loc:
        factor *= rules.dump_multiplier
    return round(max(rules.floor, factor), 6)


# --------------------------------------------------------------------------
# 5.5 Leaderboard ranking and tiebreakers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardEntry:
    key: Hashable
    points: int
    solved: int
    total_solve_seconds: int
    reached_at: float  # when the member reached their current point total


def _board_sort_key(e: BoardEntry) -> tuple:
    return (-e.points, -e.solved, e.total_solve_seconds, e.reached_at)


def rank_board(entries: Sequence[BoardEntry]) -> list[tuple[int, BoardEntry]]:
    """Sort by points, then (5.5) more tasks solved, lower total solve
    duration, and earlier time of reaching the total. Members equal on every
    key share a rank."""
    ordered = sorted(entries, key=lambda e: (_board_sort_key(e), str(e.key)))
    ranks = dense_ranks([_board_sort_key(e) for e in ordered])
    return list(zip(ranks, ordered))


# --------------------------------------------------------------------------
# Streaks (FR-PROF-2)
# --------------------------------------------------------------------------


def streaks(task_states: Sequence[tuple[bool, bool]]) -> tuple[int, int]:
    """Current and best streak.

    `task_states` is one (closed, solved) pair per daily task, in date order.
    Days with no task (for example a Codeforces outage) have no entry, so they
    do not break a streak. The open task counts only if already solved.
    """
    best = run = 0
    for closed, solved in task_states:
        if solved:
            run += 1
            best = max(best, run)
        elif closed:
            run = 0
    current = 0
    for i, (closed, solved) in enumerate(reversed(task_states)):
        if solved:
            current += 1
        elif i == 0 and not closed:
            continue  # today's task, not solved yet: doesn't break the streak
        else:
            break
    return current, best
