"""Scheduled jobs (SRD section 8).

Every job is idempotent: running it twice, or late, has the same effect as
running it once on time. A one-minute `tick` posts the day's task when due and
closes tasks past their close time, so missed runs catch up on restart.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from . import scoring
from .codeforces import (
    PENDING,
    CodeforcesAPI,
    CodeforcesError,
    CodeforcesNotFound,
    ProblemKey,
    evaluate_attempt,
    pick_problem,
    problem_key,
    submission_key,
    user_status_since,
)
from .config import Config
from .db import DailyTask, TaskAttempt, User
from .timewin import current_window

if TYPE_CHECKING:
    from .backup import BackupJob
    from .projects import ProjectService

log = logging.getLogger(__name__)

# Ratings older than this are refreshed by sync_ratings (section 8: daily).
RATING_MAX_AGE_SECONDS = 24 * 3600
# Handles per user.info call.
RATING_BATCH = 100

# How long past close a submission stuck "in queue" may hold up finalization.
PENDING_GRACE_SECONDS = 3600


@dataclass
class VerifyOutcome:
    errors: int = 0
    pending: int = 0
    newly_solved: int = 0


class Jobs:
    def __init__(
        self,
        engine: Engine,
        config: Config,
        cf: CodeforcesAPI,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        projects: ProjectService | None = None,
        backup: BackupJob | None = None,
    ) -> None:
        self.engine = engine
        self.config = config
        self.cf = cf
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        self.projects = projects
        self.backup = backup
        self._post_lock = asyncio.Lock()
        self._verify_lock = asyncio.Lock()
        self._first_post_failure: dict[str, float] = {}
        self._last_post_failure: dict[str, float] = {}
        self._skipped_dates: set[str] = set()

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    async def post_daily_task(self, *, force: bool = False, exclude: set[ProblemKey] | None = None) -> DailyTask | None:
        """Create today's task if it does not exist yet (FR-TASK-1..4).

        On Codeforces failure, retries at most every `post_retry_minutes` for
        `post_retry_hours`, then gives up on the day (section 11).
        """
        async with self._post_lock:
            now = self.clock()
            g, srv = self.config.general, self.config.server
            window = current_window(now, g.tz, g.post_time)
            date_s = window.task_date.isoformat()
            with Session(self.engine) as s:
                if s.exec(select(DailyTask).where(DailyTask.task_date == date_s)).first():
                    return None
            if not force:
                if date_s in self._skipped_dates:
                    return None
                first = self._first_post_failure.get(date_s)
                if first is not None and now - first > srv.post_retry_hours * 3600:
                    log.error("giving up on daily task for %s after %sh of failures", date_s, srv.post_retry_hours)
                    self._skipped_dates.add(date_s)
                    return None
                last = self._last_post_failure.get(date_s)
                if last is not None and now - last < srv.post_retry_minutes * 60:
                    return None
            try:
                task = await self._pick_and_create(date_s, window.task_date.weekday(), window.closes_at, exclude or set())
            except CodeforcesError as exc:
                log.warning("posting task for %s failed: %s", date_s, exc)
                task = None
            if task is None:
                self._first_post_failure.setdefault(date_s, now)
                self._last_post_failure[date_s] = now
            return task

    async def _pick_and_create(
        self, date_s: str, weekday: int, closes_at: int, exclude: set[ProblemKey]
    ) -> DailyTask | None:
        tc = self.config.tasks
        target = tc.rating_by_weekday[weekday]
        with Session(self.engine) as s:
            excluded = {(t.contest_id, t.problem_index) for t in s.exec(select(DailyTask)).all()}
            handles = [u.cf_handle for u in s.exec(select(User).where(User.banned == False)).all()]  # noqa: E712
        excluded |= exclude
        for handle in handles:  # no member may have submitted to it before (FR-TASK-3)
            for sub in await self.cf.user_status(handle):
                key = submission_key(sub)
                if key:
                    excluded.add(key)
        problems = await self.cf.problemset_problems()
        picked = pick_problem(problems, target, tc.rating_fallback_steps, tc.excluded_tags, excluded, self.rng)
        if picked is None:
            log.error("no eligible problem for %s near rating %s", date_s, target)
            return None
        if picked["rating"] != target:
            log.info("task %s: no problem at %s, used rating %s", date_s, target, picked["rating"])
        contest_id, index = problem_key(picked)
        task = DailyTask(
            task_date=date_s,
            contest_id=contest_id,
            problem_index=index,
            name=picked.get("name", f"{contest_id}{index}"),
            rating=picked["rating"],
            posted_at=int(self.clock()),
            closes_at=closes_at,
        )
        with Session(self.engine) as s:
            s.add(task)
            s.commit()
            s.refresh(task)
        log.info("posted task %s: %s%s (%s)", date_s, contest_id, index, task.rating)
        return task

    # ------------------------------------------------------------------
    # Verification and scoring
    # ------------------------------------------------------------------

    async def verify_task(self, task_id: int) -> VerifyOutcome:
        """Check every started, unsolved attempt on a task against Codeforces."""
        out = VerifyOutcome()
        with Session(self.engine) as s:
            task = s.get(DailyTask, task_id)
            if task is None:
                return out
            rows = s.exec(
                select(TaskAttempt.id, TaskAttempt.started_at, User.cf_handle)
                .join(User, User.id == TaskAttempt.user_id)
                .where(
                    TaskAttempt.task_id == task_id,
                    TaskAttempt.accepted_at == None,  # noqa: E711
                    TaskAttempt.voided_at == None,  # noqa: E711
                )
            ).all()
            contest_id, index, closes_at = task.contest_id, task.problem_index, task.closes_at
        for attempt_id, started_at, handle in rows:
            try:
                subs = await user_status_since(self.cf, handle, started_at)
            except CodeforcesError as exc:
                log.warning("verify %s on task %s failed: %s", handle, task_id, exc)
                out.errors += 1
                continue
            result = evaluate_attempt(subs, contest_id, index, started_at, closes_at)
            if result == PENDING:
                out.pending += 1
            elif result is not None:
                with Session(self.engine) as s:
                    a = s.get(TaskAttempt, attempt_id)
                    a.accepted_at = result.accepted_at
                    a.submission_id = result.submission_id
                    a.runtime_ms = result.runtime_ms
                    a.rejected_count = result.rejected_count
                    a.solve_seconds = result.accepted_at - a.started_at
                    s.add(a)
                    s.commit()
                out.newly_solved += 1
        if out.newly_solved:
            with Session(self.engine) as s:
                self.rescore_task(s, s.get(DailyTask, task_id))
                s.commit()
        return out

    def rescore_task(self, session: Session, task: DailyTask) -> None:
        """Recompute points for every attempt on a task from stored raw data."""
        rows = session.exec(
            select(TaskAttempt, User).join(User, User.id == TaskAttempt.user_id).where(TaskAttempt.task_id == task.id)
        ).all()
        eligible = [
            scoring.SolvedAttempt(a.id, a.solve_seconds, a.runtime_ms, a.rejected_count)
            for a, u in rows
            if a.accepted_at is not None and a.voided_at is None and not u.banned
        ]
        scores = scoring.score_task(eligible, self.config.tasks.rules())
        for a, _u in rows:
            score = scores.get(a.id)
            a.points = score.points if score else 0
            a.provisional = task.finalized_at is None
            session.add(a)

    def recompute_all(self) -> int:
        """Rebuild every task score (FR-ADMIN-3). Returns the number of tasks."""
        with Session(self.engine) as s:
            tasks = s.exec(select(DailyTask)).all()
            for t in tasks:
                self.rescore_task(s, t)
            s.commit()
            return len(tasks)

    async def verify_attempts(self) -> None:
        """Every 2 minutes: verify attempts on tasks that are not finalized."""
        async with self._verify_lock:
            with Session(self.engine) as s:
                ids = s.exec(select(DailyTask.id).where(DailyTask.finalized_at == None)).all()  # noqa: E711
            for task_id in ids:
                await self.verify_task(task_id)

    async def close_due_tasks(self) -> None:
        """Finalize tasks past close after one successful verification pass."""
        now = self.clock()
        async with self._verify_lock:
            with Session(self.engine) as s:
                due = s.exec(
                    select(DailyTask.id, DailyTask.closes_at).where(
                        DailyTask.finalized_at == None, DailyTask.closes_at <= now  # noqa: E711
                    )
                ).all()
            for task_id, closes_at in due:
                outcome = await self.verify_task(task_id)
                if outcome.errors:
                    continue  # Codeforces down: try again next tick (section 11)
                if outcome.pending and now < closes_at + PENDING_GRACE_SECONDS:
                    continue
                with Session(self.engine) as s:
                    task = s.get(DailyTask, task_id)
                    task.finalized_at = int(now)
                    s.add(task)
                    self.rescore_task(s, task)
                    s.commit()
                log.info("finalized task %s", task_id)

    async def sync_ratings(self, *, force: bool = False) -> int:
        """Refresh Codeforces rating and rank for members whose data is over a
        day old (or everyone, with force). Returns how many were updated."""
        now = int(self.clock())
        with Session(self.engine) as s:
            q = select(User.cf_handle).where(User.banned == False)  # noqa: E712
            if not force:
                q = q.where(
                    (User.rating_synced_at == None)  # noqa: E711
                    | (User.rating_synced_at < now - RATING_MAX_AGE_SECONDS)
                )
            handles = list(s.exec(q).all())
        infos: dict[str, dict] = {}
        for i in range(0, len(handles), RATING_BATCH):
            chunk = handles[i : i + RATING_BATCH]
            try:
                found = await self.cf.user_info(chunk)
            except CodeforcesNotFound:
                # One unknown handle fails the whole batch; fall back to one by one.
                found = []
                for h in chunk:
                    try:
                        found += await self.cf.user_info([h])
                    except CodeforcesNotFound:
                        log.warning("rating sync: Codeforces no longer knows handle %s", h)
            for info in found:
                infos[info["handle"].lower()] = info
        with Session(self.engine) as s:
            for u in s.exec(select(User).where(User.cf_handle.in_(handles))).all():  # type: ignore[attr-defined]
                info = infos.get(u.cf_handle.lower())
                if info is not None:
                    u.cf_rating, u.cf_rank = info.get("rating"), info.get("rank")
                u.rating_synced_at = now  # also for unknown handles, so they aren't retried every hour
                s.add(u)
            s.commit()
        if infos:
            log.info("synced ratings for %d members", len(infos))
        return len(infos)

    async def tick(self) -> None:
        """Every minute: post today's task when due, close finished tasks,
        take the day's repo snapshots once snapshot_time has passed,
        auto-submit expired writeups, finalize finished projects, and back up
        the database once a day."""
        jobs = [self.post_daily_task, self.close_due_tasks]
        if self.projects is not None:
            jobs += [
                self.projects.daily_snapshots,
                self.projects.writeups.sweep,  # sweep_writeups (section 8)
                self.projects.writeups.finalize_projects,  # finalize_project
            ]
        if self.backup is not None:
            jobs.append(self.backup.run)  # backup_db
        for job in jobs:
            try:
                await job()
            except Exception:  # keep the scheduler alive; the next tick retries
                log.exception("job %s failed", getattr(job, "__qualname__", job))

    async def safe_verify(self) -> None:
        await _safely(self.verify_attempts)

    async def safe_sync_ratings(self) -> None:
        await _safely(self.sync_ratings)


async def _safely(job) -> None:
    """Run a scheduled job, logging instead of raising so the scheduler lives on."""
    try:
        await job()
    except Exception:
        log.exception("job %s failed", job.__name__)


def start_scheduler(jobs: Jobs):
    """Start APScheduler on the running event loop and return it."""
    from datetime import datetime, timezone

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    sched = AsyncIOScheduler(timezone=timezone.utc)
    common = {"max_instances": 1, "coalesce": True, "next_run_time": datetime.now(timezone.utc)}
    sched.add_job(jobs.tick, "interval", seconds=60, id="tick", **common)
    sched.add_job(jobs.safe_verify, "interval", seconds=120, id="verify_attempts", **common)
    sched.add_job(jobs.safe_sync_ratings, "interval", seconds=3600, id="sync_ratings", **common)
    sched.start()
    return sched
