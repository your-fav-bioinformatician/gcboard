"""Leaderboards, streaks, and profiles (FR-BOARD-*, FR-PROF-*), computed from
stored scores. Banned members and voided attempts never count."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlmodel import Session, select

from gcboard.shared.schemas import BoardRow, LeaderboardResponse, Profile

from . import scoring
from .config import Config
from .db import DailyTask, TaskAttempt, User
from .timewin import week_of


@dataclass
class _Data:
    """Everything the board and profiles need, loaded once per request."""

    tasks: dict[int, DailyTask]
    users: dict[int, User]  # not banned
    solved: list[TaskAttempt]  # accepted, not voided, by a non-banned member


def _load(session: Session) -> _Data:
    tasks = {t.id: t for t in session.exec(select(DailyTask)).all()}
    users = {u.id: u for u in session.exec(select(User).where(User.banned == False)).all()}  # noqa: E712
    solved = [
        a
        for a in session.exec(
            select(TaskAttempt).where(TaskAttempt.accepted_at != None, TaskAttempt.voided_at == None)  # noqa: E711
        ).all()
        if a.user_id in users and a.task_id in tasks
    ]
    return _Data(tasks, users, solved)


def _monday(task_date: str) -> date:
    d = date.fromisoformat(task_date)
    return d - timedelta(days=d.weekday())


@dataclass
class _Totals:
    points: int = 0
    solved: int = 0
    solve_seconds: int = 0
    reached_at: int = 0
    provisional: bool = False


def _rank(attempts: list[TaskAttempt]) -> tuple[list[tuple[int, scoring.BoardEntry]], dict[int, _Totals]]:
    """Rank members by their attempts in one period. Members without a solve
    in the period are left out (they appear once they score)."""
    totals: dict[int, _Totals] = defaultdict(_Totals)
    for a in attempts:
        t = totals[a.user_id]
        t.points += a.points
        t.solved += 1
        t.solve_seconds += a.solve_seconds or 0
        t.reached_at = max(t.reached_at, a.accepted_at or 0)
        t.provisional = t.provisional or a.provisional
    # Project points join here in M3/M4 (projects that closed in the period).
    entries = [
        scoring.BoardEntry(uid, t.points, t.solved, t.solve_seconds, t.reached_at) for uid, t in totals.items()
    ]
    return scoring.rank_board(entries), totals


def _streaks(data: _Data, now: float) -> dict[int, tuple[int, int]]:
    """(current, best) streak for every non-banned member."""
    ordered = sorted(data.tasks.values(), key=lambda t: t.task_date)
    solved: dict[int, set[int]] = defaultdict(set)
    for a in data.solved:
        solved[a.user_id].add(a.task_id)
    return {
        uid: scoring.streaks([(t.closes_at <= now, t.id in solved[uid]) for t in ordered]) for uid in data.users
    }


def leaderboard(session: Session, config: Config, period: Literal["week", "all"], now: float) -> LeaderboardResponse:
    data = _load(session)
    week = week_of(now, config.general.tz)
    if period == "week":
        attempts = [a for a in data.solved if _monday(data.tasks[a.task_id].task_date) == week.monday]
    else:
        attempts = data.solved
    ranked, totals = _rank(attempts)
    streaks = _streaks(data, now)
    rows = [
        BoardRow(
            rank=rank,
            cf_handle=data.users[e.key].cf_handle,
            points=e.points,
            provisional=totals[e.key].provisional,
            solved=e.solved,
            streak=streaks[e.key][0],
        )
        for rank, e in ranked
    ]
    if period == "week":
        return LeaderboardResponse(
            period=period, label=f"week {week.iso_week} · ends Sun 23:59", ends_at=week.ends_at, rows=rows
        )
    return LeaderboardResponse(period=period, label="all time", ends_at=None, rows=rows)


def weekly_finishes(data: _Data, current_monday: date) -> dict[int, list[int]]:
    """Each member's rank in every completed week they scored in."""
    by_week: dict[date, list[TaskAttempt]] = defaultdict(list)
    for a in data.solved:
        monday = _monday(data.tasks[a.task_id].task_date)
        if monday < current_monday:
            by_week[monday].append(a)
    finishes: dict[int, list[int]] = defaultdict(list)
    for attempts in by_week.values():
        for rank, e in _rank(attempts)[0]:
            finishes[e.key].append(rank)
    return finishes


def profile(session: Session, config: Config, user: User, now: float) -> Profile:
    """FR-PROF-1. Project history arrives with M3/M4."""
    data = _load(session)
    week = week_of(now, config.general.tz)
    mine = [a for a in data.solved if a.user_id == user.id]
    times = [a.solve_seconds for a in mine if a.solve_seconds is not None]
    current, best = _streaks(data, now).get(user.id, (0, 0))
    finishes = weekly_finishes(data, week.monday).get(user.id, [])
    return Profile(
        cf_handle=user.cf_handle,
        cf_rating=user.cf_rating,
        cf_rank=user.cf_rank,
        rating_synced_at=user.rating_synced_at,
        joined_at=user.joined_at,
        current_streak=current,
        best_streak=best,
        solved_week=sum(1 for a in mine if _monday(data.tasks[a.task_id].task_date) == week.monday),
        solved_all=len(mine),
        avg_solve_seconds=round(sum(times) / len(times)) if times else None,
        fastest_solve_seconds=min(times) if times else None,
        weekly_wins=sum(1 for r in finishes if r == 1),
        best_weekly_finish=min(finishes) if finishes else None,
        projects=[],
    )
