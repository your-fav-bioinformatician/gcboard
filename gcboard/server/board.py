"""Leaderboards and streaks (FR-BOARD-*, FR-PROF-2), computed from stored scores."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from sqlmodel import Session, select

from gcboard.shared.schemas import BoardRow, LeaderboardResponse

from . import scoring
from .config import Config
from .db import DailyTask, TaskAttempt, User
from .timewin import week_of


def all_streaks(session: Session, now: float) -> dict[int, tuple[int, int]]:
    """(current, best) streak for every user."""
    tasks = session.exec(select(DailyTask).order_by(DailyTask.task_date)).all()
    solved: dict[int, set[int]] = defaultdict(set)
    for a in session.exec(
        select(TaskAttempt).where(TaskAttempt.accepted_at != None, TaskAttempt.voided_at == None)  # noqa: E711
    ).all():
        solved[a.user_id].add(a.task_id)
    users = session.exec(select(User.id)).all()
    return {
        uid: scoring.streaks([(t.closes_at <= now, t.id in solved[uid]) for t in tasks])
        for uid in users
    }


def leaderboard(session: Session, config: Config, period: Literal["week", "all"], now: float) -> LeaderboardResponse:
    week = week_of(now, config.general.tz)
    task_q = select(DailyTask)
    if period == "week":
        task_q = task_q.where(
            DailyTask.task_date >= week.monday.isoformat(), DailyTask.task_date <= week.sunday.isoformat()
        )
    tasks = {t.id: t for t in session.exec(task_q).all()}
    users = {u.id: u for u in session.exec(select(User).where(User.banned == False)).all()}  # noqa: E712

    totals: dict[int, dict] = {}
    if tasks:
        attempts = session.exec(
            select(TaskAttempt).where(
                TaskAttempt.task_id.in_(list(tasks)),  # type: ignore[attr-defined]
                TaskAttempt.accepted_at != None,  # noqa: E711
                TaskAttempt.voided_at == None,  # noqa: E711
            )
        ).all()
        for a in attempts:
            if a.user_id not in users:
                continue
            t = totals.setdefault(
                a.user_id, {"points": 0, "solved": 0, "solve": 0, "reached": 0, "provisional": False}
            )
            t["points"] += a.points
            t["solved"] += 1
            t["solve"] += a.solve_seconds or 0
            t["reached"] = max(t["reached"], a.accepted_at or 0)
            t["provisional"] = t["provisional"] or a.provisional
    # Project points join here in M3/M4 (projects that closed in the period).

    entries = [
        scoring.BoardEntry(uid, t["points"], t["solved"], t["solve"], t["reached"]) for uid, t in totals.items()
    ]
    streaks = all_streaks(session, now)
    rows = [
        BoardRow(
            rank=rank,
            cf_handle=users[e.key].cf_handle,
            points=e.points,
            provisional=totals[e.key]["provisional"],
            solved=e.solved,
            streak=streaks.get(e.key, (0, 0))[0],
        )
        for rank, e in scoring.rank_board(entries)
    ]
    if period == "week":
        label = f"week {week.iso_week} · ends Sun 23:59"
        ends_at: int | None = week.ends_at
    else:
        label = "all time"
        ends_at = None
    return LeaderboardResponse(period=period, label=label, ends_at=ends_at, rows=rows)

