"""Writeup sessions (FR-WRITE-*) and project scoring and finalization
(FR-PROJ-10, SRD 5.2).

A writeup starts the moment a project is submitted, lasts the configured time
limit from server time, and ends when the member submits it or when the
deadline passes, whichever is first. Nothing the client does can pause it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from gcboard.shared.schemas import SnippetInfo, WriteupResult, WriteupSession

from . import scoring, textrules
from .config import Config
from .db import EntryStatus, Project, ProjectEntry, User, Writeup
from .errors import ApiError
from .repos import RepoFiles

log = logging.getLogger(__name__)

Q1 = (
    "This is {path}, lines {a}-{b} of your submission. Explain what it does, why you wrote it this way, "
    "and how it connects to the rest of the project."
)
Q2 = "Explain how you designed the project: its structure, the main parts, and the algorithms you used."
Q4 = "Describe a bug or design problem you hit while building this, and how you found and fixed it."


def build_writeup(entry: ProjectEntry, project: Project, files: RepoFiles, config: Config, now: int) -> Writeup:
    """Everything the writeup needs, captured from the frozen commit (FR-PROJ-9)."""
    identifiers = textrules.extract_identifiers(files.sources)
    snip = textrules.select_snippet(files.sources, identifiers, seed=f"{entry.id}:{files.sha}")
    return Writeup(
        entry_id=entry.id,
        snippet_path=snip.path,
        snippet_start=snip.start,
        snippet_end=snip.end,
        snippet_text=snip.text,
        snippet_identifiers=snip.identifiers,
        repo_identifiers=identifiers,
        questions=[Q1.format(path=snip.path, a=snip.start, b=snip.end), Q2, project.change_question, Q4],
        answers=["", "", "", ""],
        started_at=now,
        deadline=now + config.writeup.time_limit_minutes * 60,
        last_saved_at=now,
    )


class WriteupService:
    def __init__(self, engine: Engine, config: Config, clock: Callable[[], float] = time.time) -> None:
        self.engine = engine
        self.config = config
        self.clock = clock

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def session_view(self, w: Writeup, now: float) -> WriteupSession:
        wc = self.config.writeup
        return WriteupSession(
            server_time=now,
            questions=w.questions,
            snippet=SnippetInfo(path=w.snippet_path, start=w.snippet_start, end=w.snippet_end, text=w.snippet_text),
            started_at=w.started_at,
            deadline=w.deadline,
            answers=w.answers,
            min_words=wc.min_words,
            autosave_seconds=wc.autosave_seconds,
            max_answer_chars=wc.max_answer_chars,
        )

    def result_view(self, w: Writeup | None) -> WriteupResult | None:
        if w is None or w.submitted_at is None or w.score is None:
            return None
        return WriteupResult(
            score=w.score,
            passed=w.score >= self.config.projects.pass_threshold,
            auto_submitted=w.auto_submitted,
            submitted_at=w.submitted_at,
            breakdown=w.breakdown or {},
        )

    def _open_for(self, session: Session, user: User) -> Writeup | None:
        return session.exec(
            select(Writeup)
            .join(ProjectEntry, ProjectEntry.id == Writeup.entry_id)
            .where(ProjectEntry.user_id == user.id, Writeup.submitted_at == None)  # noqa: E711
        ).first()

    def current(self, user: User) -> WriteupSession | None:
        """The member's open session, if any. A session found past its
        deadline is auto-submitted on the spot instead."""
        now = int(self.clock())
        with Session(self.engine) as s:
            w = self._open_for(s, user)
            if w is None:
                return None
            if now >= w.deadline:
                self._finish(s, w, now, auto=True)
                s.commit()
                return None
            return self.session_view(w, now)

    # ------------------------------------------------------------------
    # Saving and submitting
    # ------------------------------------------------------------------

    def _apply_save(self, w: Writeup, answers: list[str], now: int) -> None:
        """FR-WRITE-6: reject text that grew faster than a person can type."""
        wc = self.config.writeup
        if any(len(a) > wc.max_answer_chars for a in answers):
            raise ApiError(422, "answer_too_long", f"Each answer is limited to {wc.max_answer_chars} characters.")
        growth = sum(map(len, answers)) - sum(map(len, w.answers))
        allowed = wc.max_chars_per_second * max(0, now - w.last_saved_at) + wc.burst_allowance_chars
        if growth > allowed:
            raise ApiError(
                409,
                "typing_too_fast",
                "That text arrived faster than anyone can type, so it was not saved. "
                "Your last saved text has been restored.",
                answers=w.answers,
            )
        w.answers = list(answers)
        w.last_saved_at = now

    def save(self, user: User, answers: list[str]) -> WriteupSession:
        now = int(self.clock())
        with Session(self.engine) as s:
            w = self._open_for(s, user)
            if w is None:
                raise ApiError(404, "no_writeup", "You have no open writeup.")
            if now >= w.deadline:
                self._finish(s, w, now, auto=True)
                s.commit()
                raise ApiError(409, "deadline_passed", "Time is up. Your last saved text was submitted.")
            self._apply_save(w, answers, now)
            s.add(w)
            s.commit()
            return self.session_view(w, now)

    def submit(self, user: User, answers: list[str] | None) -> WriteupResult:
        """FR-WRITE-8/10: early submit once minimums are met, graded at once.
        After the deadline this returns the auto-submitted result instead."""
        now = int(self.clock())
        with Session(self.engine) as s:
            w = self._open_for(s, user)
            if w is None:
                raise ApiError(404, "no_writeup", "You have no open writeup.")
            if now >= w.deadline:
                self._finish(s, w, now, auto=True)
                s.commit()
                return self.result_view(w)
            if answers is not None:
                self._apply_save(w, answers, now)
            short = [
                f"Q{i + 1} {len(textrules.words(a))}/{m}"
                for i, (a, m) in enumerate(zip(w.answers, self.config.writeup.min_words))
                if len(textrules.words(a)) < m
            ]
            if short:
                s.commit()  # keep the accepted save
                raise ApiError(400, "below_minimum", "Not enough words yet: " + ", ".join(short) + ".")
            self._finish(s, w, now, auto=False)
            s.commit()
            return self.result_view(w)

    def _finish(self, session: Session, w: Writeup, now: int, auto: bool) -> None:
        w.submitted_at = min(now, w.deadline) if auto else now
        w.auto_submitted = auto
        self._grade(session, w)
        session.add(w)
        entry = session.get(ProjectEntry, w.entry_id)
        self.rescore_project(session, session.get(Project, entry.project_id))
        log.info("writeup %s %s: %s", w.id, "auto-submitted" if auto else "submitted", w.score)

    def _grade(self, session: Session, w: Writeup) -> None:
        entry = session.get(ProjectEntry, w.entry_id)
        project = session.get(Project, entry.project_id)
        b = textrules.grade(
            w.answers, w.snippet_identifiers, w.repo_identifiers, project.change_keyword_groups,
            self.config.writeup.rules(),
        )
        w.score = b.total
        w.breakdown = b.to_dict()

    # ------------------------------------------------------------------
    # Project scoring (5.2) and finalization
    # ------------------------------------------------------------------

    def rescore_project(self, session: Session, project: Project) -> None:
        """Recompute ranks and points for every entry from stored data.
        Entries whose writeup isn't graded yet are left out until it is."""
        rows = session.exec(
            select(ProjectEntry, Writeup, User)
            .join(User, User.id == ProjectEntry.user_id)
            .join(Writeup, Writeup.entry_id == ProjectEntry.id, isouter=True)
            .where(ProjectEntry.project_id == project.id)
        ).all()
        eligible = [
            scoring.EntryInput(e.id, e.submitted_at, w.score, e.history_factor or 0.0)
            for e, w, u in rows
            if e.status == EntryStatus.SUBMITTED and not u.banned and w is not None and w.score is not None
        ]
        scores = scoring.score_project(eligible, self.config.projects.rules())
        for e, _w, _u in rows:
            sc = scores.get(e.id)
            e.submit_rank = sc.submit_rank if sc else None
            e.speed_points = sc.speed_points if sc else None
            e.points = sc.points if sc else 0
            session.add(e)

    async def sweep(self) -> int:
        """Every minute: auto-submit sessions past their deadline (FR-WRITE-9)."""
        now = int(self.clock())
        with Session(self.engine) as s:
            due = s.exec(
                select(Writeup).where(Writeup.submitted_at == None, Writeup.deadline <= now)  # noqa: E711
            ).all()
            for w in due:
                self._finish(s, w, now, auto=True)
            s.commit()
            return len(due)

    async def finalize_projects(self) -> int:
        """After close, once every writeup has ended: final ranks and points."""
        now = int(self.clock())
        done = 0
        with Session(self.engine) as s:
            projects = s.exec(
                select(Project).where(Project.closes_at <= now, Project.finalized_at == None)  # noqa: E711
            ).all()
            for p in projects:
                still_open = s.exec(
                    select(Writeup.id)
                    .join(ProjectEntry, ProjectEntry.id == Writeup.entry_id)
                    .where(ProjectEntry.project_id == p.id, Writeup.submitted_at == None)  # noqa: E711
                ).first()
                if still_open is not None:
                    continue
                self.rescore_project(s, p)
                p.finalized_at = now
                s.add(p)
                done += 1
                log.info("finalized project %s", p.id)
            s.commit()
        return done

    def regrade_all(self) -> int:
        """FR-ADMIN-3: regrade every submitted writeup and rescore every
        project from stored data. Finalized projects stay finalized."""
        with Session(self.engine) as s:
            graded = s.exec(select(Writeup).where(Writeup.submitted_at != None)).all()  # noqa: E711
            for w in graded:
                self._grade(s, w)
                s.add(w)
            for p in s.exec(select(Project)).all():
                self.rescore_project(s, p)
            s.commit()
            return len(graded)
