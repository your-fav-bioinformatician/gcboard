"""Leaderboards, streaks, and profiles (FR-BOARD-*, FR-PROF-*), computed from
stored scores. Banned members, voided attempts and voided entries never count.

Project points count in the period in which the project closed (FR-BOARD-2).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlmodel import Session, select

from gcboard.shared.schemas import BoardRow, LeaderboardResponse, Profile, ProjectHistoryRow

from . import scoring
from .config import Config
from .db import DailyTask, EntryStatus, Project, ProjectEntry, TaskAttempt, User, Writeup
from .timewin import week_of


@dataclass(frozen=True)
class _ProjectPoints:
    user_id: int
    points: int
    provisional: bool
    closes_at: int


@dataclass
class _Data:
    """Everything the board and profiles need, loaded once per request."""

    tasks: dict[int, DailyTask]
    users: dict[int, User]  # not banned
    solved: list[TaskAttempt]  # accepted, not voided, by a non-banned member
    projects: list[_ProjectPoints]  # submitted entries of closed projects, with points


def _load(session: Session, now: float) -> _Data:
    tasks = {t.id: t for t in session.exec(select(DailyTask)).all()}
    users = {u.id: u for u in session.exec(select(User).where(User.banned == False)).all()}  # noqa: E712
    solved = [
        a
        for a in session.exec(
            select(TaskAttempt).where(TaskAttempt.accepted_at != None, TaskAttempt.voided_at == None)  # noqa: E711
        ).all()
        if a.user_id in users and a.task_id in tasks
    ]
    projects = [
        _ProjectPoints(e.user_id, e.points, p.finalized_at is None, p.closes_at)
        for e, p in session.exec(
            select(ProjectEntry, Project)
            .join(Project, Project.id == ProjectEntry.project_id)
            .where(ProjectEntry.status == EntryStatus.SUBMITTED, ProjectEntry.points > 0, Project.closes_at <= now)
        ).all()
        if e.user_id in users
    ]
    return _Data(tasks, users, solved, projects)


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


def _rank(
    attempts: list[TaskAttempt], projects: list[_ProjectPoints] = ()
) -> tuple[list[tuple[int, scoring.BoardEntry]], dict[int, _Totals]]:
    """Rank members by their points in one period. Members who scored
    nothing in the period are left out (they appear once they score)."""
    totals: dict[int, _Totals] = defaultdict(_Totals)
    for a in attempts:
        t = totals[a.user_id]
        t.points += a.points
        t.solved += 1
        t.solve_seconds += a.solve_seconds or 0
        t.reached_at = max(t.reached_at, a.accepted_at or 0)
        t.provisional = t.provisional or a.provisional
    for p in projects:
        t = totals[p.user_id]
        t.points += p.points
        t.reached_at = max(t.reached_at, p.closes_at)  # points are awarded at close
        t.provisional = t.provisional or p.provisional
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
    data = _load(session, now)
    week = week_of(now, config.general.tz)
    if period == "week":
        attempts = [a for a in data.solved if _monday(data.tasks[a.task_id].task_date) == week.monday]
        projects = [p for p in data.projects if week.starts_at <= p.closes_at < week.ends_at]
    else:
        attempts, projects = data.solved, data.projects
    ranked, totals = _rank(attempts, projects)
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


def weekly_finishes(data: _Data, config: Config, current_monday: date) -> dict[int, list[int]]:
    """Each member's rank in every completed week they scored in."""
    tz = config.general.tz
    tasks_by_week: dict[date, list[TaskAttempt]] = defaultdict(list)
    projects_by_week: dict[date, list[_ProjectPoints]] = defaultdict(list)
    for a in data.solved:
        tasks_by_week[_monday(data.tasks[a.task_id].task_date)].append(a)
    for p in data.projects:
        projects_by_week[week_of(p.closes_at, tz).monday].append(p)
    finishes: dict[int, list[int]] = defaultdict(list)
    for monday in set(tasks_by_week) | set(projects_by_week):
        if monday >= current_monday:
            continue
        for rank, e in _rank(tasks_by_week[monday], projects_by_week[monday])[0]:
            finishes[e.key].append(rank)
    return finishes


def profile(session: Session, config: Config, user: User, now: float) -> Profile:
    """FR-PROF-1."""
    data = _load(session, now)
    week = week_of(now, config.general.tz)
    mine = [a for a in data.solved if a.user_id == user.id]
    times = [a.solve_seconds for a in mine if a.solve_seconds is not None]
    current, best = _streaks(data, now).get(user.id, (0, 0))
    finishes = weekly_finishes(data, config, week.monday).get(user.id, [])
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
        projects=_project_history(session, user),
    )


def _project_history(session: Session, user: User) -> list[ProjectHistoryRow]:
    """Submitted entries. The writeup score shows once the writeup has ended;
    the answers themselves are never part of a profile (FR-PROF-3)."""
    rows = session.exec(
        select(ProjectEntry, Project, Writeup)
        .join(Project, Project.id == ProjectEntry.project_id)
        .join(Writeup, Writeup.entry_id == ProjectEntry.id, isouter=True)
        .where(ProjectEntry.user_id == user.id, ProjectEntry.status == EntryStatus.SUBMITTED)
        .order_by(Project.opens_at)
    ).all()
    return [
        ProjectHistoryRow(
            title=p.title,
            submit_rank=e.submit_rank,
            writeup_score=w.score if w is not None and w.submitted_at is not None else None,
            history_factor=e.history_factor,
            points=e.points,
            provisional=p.finalized_at is None,
        )
        for e, p, w in rows
    ]
