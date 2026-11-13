"""Projects: repo registration and verification, snapshots, and submission
(FR-PROJ-*). Submitting starts the writeup session (see writeups.py).

Projects open and close purely by their configured times (FR-PROJ-2), so
there is no open/close state to keep in sync.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from gcboard.shared.schemas import EntryInfo, ProjectInfo, ProjectResponse, SnapshotInfo, UpcomingProject

from . import scoring
from .config import Config
from .db import EntryStatus, Project, ProjectEntry, RepoSnapshot, User, Writeup
from .errors import ApiError
from .repos import RepoError, RepoFiles, RepoHost, RepoRef, RepoUnavailable, parse_repo_url
from .writeups import WriteupService, build_writeup

log = logging.getLogger(__name__)

# After a failed daily snapshot, wait this long before trying that entry again.
SNAPSHOT_RETRY_SECONDS = 30 * 60

ACTIVE = (EntryStatus.REGISTERED, EntryStatus.UNREACHABLE)


def open_project(session: Session, now: float) -> Project | None:
    return session.exec(select(Project).where(Project.opens_at <= now, Project.closes_at > now)).first()


def last_closed_project(session: Session, now: float) -> Project | None:
    return session.exec(
        select(Project).where(Project.closes_at <= now).order_by(Project.closes_at.desc())  # type: ignore[attr-defined]
    ).first()


def next_project(session: Session, now: float) -> Project | None:
    return session.exec(select(Project).where(Project.opens_at > now).order_by(Project.opens_at)).first()


def overlapping(session: Session, opens_at: int, closes_at: int, ignore_id: int | None = None) -> Project | None:
    """FR-PROJ-2: at most one project open at a time."""
    q = select(Project).where(Project.opens_at < closes_at, Project.closes_at > opens_at)
    if ignore_id is not None:
        q = q.where(Project.id != ignore_id)
    return session.exec(q).first()


def snapshots_of(session: Session, entry_id: int) -> list[RepoSnapshot]:
    return list(
        session.exec(
            select(RepoSnapshot).where(RepoSnapshot.entry_id == entry_id).order_by(RepoSnapshot.taken_at, RepoSnapshot.id)
        ).all()
    )


def history_of(snaps: list[RepoSnapshot], config: Config) -> float | None:
    if not snaps:
        return None
    return scoring.history_factor([scoring.Snapshot(s.head_sha, s.source_loc) for s in snaps], config.history.rules())


def _ref(entry: ProjectEntry) -> RepoRef:
    ref = parse_repo_url(entry.repo_url)
    assert ref is not None  # stored URLs are always normalized
    return ref


class ProjectService:
    def __init__(
        self,
        engine: Engine,
        config: Config,
        host: RepoHost,
        clock: Callable[[], float] = time.time,
        writeups: WriteupService | None = None,
    ):
        self.engine = engine
        self.config = config
        self.host = host
        self.clock = clock
        self.writeups = writeups or WriteupService(engine, config, clock)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._snapshot_failed_at: dict[int, float] = {}

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def response(self, session: Session, user: User, now: float) -> ProjectResponse:
        """The open project, or else the most recent closed one (so members
        can see their results), plus the next upcoming project."""
        project = open_project(session, now) or last_closed_project(session, now)
        upcoming = next_project(session, now)
        entry = None
        if project is not None:
            e = session.exec(
                select(ProjectEntry).where(ProjectEntry.project_id == project.id, ProjectEntry.user_id == user.id)
            ).first()
            if e is not None:
                entry = self.entry_info(session, e, project)
        return ProjectResponse(
            server_time=now,
            project=ProjectInfo(
                id=project.id, title=project.title, spec_md=project.spec_md,
                opens_at=project.opens_at, closes_at=project.closes_at, is_open=project.closes_at > now,
            ) if project else None,
            upcoming=UpcomingProject(
                id=upcoming.id, title=upcoming.title, opens_at=upcoming.opens_at, closes_at=upcoming.closes_at
            ) if upcoming else None,
            entry=entry,
            max_registration_loc=self.config.projects.max_registration_loc,
            writeup_minutes=self.config.writeup.time_limit_minutes,
        )

    def entry_info(self, session: Session, e: ProjectEntry, project: Project) -> EntryInfo:
        snaps = snapshots_of(session, e.id)
        w = session.exec(select(Writeup).where(Writeup.entry_id == e.id)).first()
        return EntryInfo(
            repo_url=e.repo_url,
            status=e.status,
            verify_code=e.verify_code if e.status == EntryStatus.PENDING else None,
            registered_at=e.registered_at,
            verified_at=e.verified_at,
            submitted_at=e.submitted_at,
            frozen_sha=e.frozen_sha,
            history_factor=e.history_factor if e.history_factor is not None else history_of(snaps, self.config),
            snapshots=[
                SnapshotInfo(taken_at=s.taken_at, kind=s.kind, head_sha=s.head_sha, source_loc=s.source_loc)
                for s in snaps
            ],
            writeup=self.writeups.result_view(w),
            submit_rank=e.submit_rank,
            points=e.points,
            provisional=project.finalized_at is None,
        )

    # ------------------------------------------------------------------
    # Member actions
    # ------------------------------------------------------------------

    def _open_project_or_fail(self, session: Session, project_id: int, now: float) -> Project:
        project = session.get(Project, project_id)
        if project is None:
            raise ApiError(404, "no_project", "No such project.")
        if not project.opens_at <= now < project.closes_at:
            raise ApiError(409, "project_closed", "This project is not open.")
        return project

    def _entry(self, session: Session, user: User, project_id: int) -> ProjectEntry | None:
        return session.exec(
            select(ProjectEntry).where(ProjectEntry.project_id == project_id, ProjectEntry.user_id == user.id)
        ).first()

    def _check_repo_free(self, session: Session, url: str, entry_id: int | None) -> None:
        """FR-PROJ-3: a repo can back only one verified entry, ever."""
        q = select(ProjectEntry).where(ProjectEntry.repo_url == url, ProjectEntry.verified_at != None)  # noqa: E711
        if entry_id is not None:
            q = q.where(ProjectEntry.id != entry_id)
        if session.exec(q).first():
            raise ApiError(409, "repo_taken", "That repository was already used for a gcboard project. Make a new one.")

    async def _head(self, ref: RepoRef) -> str:
        try:
            return await self.host.head_sha(ref)
        except RepoUnavailable as exc:
            raise ApiError(400, "repo_unavailable", f"Can't read {ref.url}: {exc}. Is it public?")
        except RepoError:
            raise ApiError(503, "github_unavailable", "GitHub is not responding. Try again in a minute.")

    async def _files(self, ref: RepoRef, sha: str) -> RepoFiles:
        try:
            return await self.host.files_at(ref, sha)
        except RepoUnavailable as exc:
            raise ApiError(400, "repo_unavailable", f"Can't read {ref.url}: {exc}.")
        except RepoError:
            raise ApiError(503, "github_unavailable", "GitHub is not responding. Try again in a minute.")

    async def register(self, user: User, project_id: int, repo_url: str) -> None:
        """FR-PROJ-3/4: record the repo and issue a verification code."""
        ref = parse_repo_url(repo_url)
        if ref is None:
            raise ApiError(422, "invalid_repo_url", "Use a GitHub URL like https://github.com/you/your-repo.")
        async with self._locks[user.id]:
            now = int(self.clock())
            with Session(self.engine) as s:
                self._open_project_or_fail(s, project_id, now)
                entry = self._entry(s, user, project_id)
                if entry is not None and entry.status != EntryStatus.PENDING:
                    raise ApiError(409, "already_registered", "Your repository for this project is already verified.")
                self._check_repo_free(s, ref.url, entry.id if entry else None)
            await self._head(ref)  # exists and is public
            with Session(self.engine) as s:
                entry = self._entry(s, user, project_id) or ProjectEntry(
                    project_id=project_id, user_id=user.id, repo_url=ref.url, verify_code="", registered_at=now
                )
                entry.repo_url = ref.url
                entry.verify_code = f"gcboard-{secrets.token_hex(8)}"
                entry.registered_at = now
                s.add(entry)
                s.commit()

    async def verify(self, user: User, project_id: int) -> None:
        """FR-PROJ-4/5: .gcboard at HEAD holds the code, and the repo is (nearly) empty."""
        async with self._locks[user.id]:
            now = int(self.clock())
            with Session(self.engine) as s:
                self._open_project_or_fail(s, project_id, now)
                entry = self._entry(s, user, project_id)
                if entry is None:
                    raise ApiError(404, "not_registered", "Register a repository first.")
                if entry.status != EntryStatus.PENDING:
                    raise ApiError(409, "already_verified", "Your repository is already verified.")
                self._check_repo_free(s, entry.repo_url, entry.id)
                ref, code, entry_id = _ref(entry), entry.verify_code, entry.id
            head = await self._head(ref)
            files = await self._files(ref, head)
            if (files.verify_file or "").strip() != code:
                raise ApiError(
                    400,
                    "code_mismatch",
                    f"The {'.gcboard file at HEAD does not contain' if files.verify_file else 'repo root has no .gcboard file with'}"
                    f" your code {code}. Commit and push it, then try again.",
                )
            limit = self.config.projects.max_registration_loc
            if files.source_loc > limit:
                raise ApiError(
                    400,
                    "repo_not_empty",
                    f"Start from an empty repo: it already has {files.source_loc} source lines (limit {limit}).",
                )
            with Session(self.engine) as s:
                entry = s.get(ProjectEntry, entry_id)
                s.add(RepoSnapshot(entry_id=entry_id, taken_at=now, kind="registration", head_sha=head,
                                   source_loc=files.source_loc))
                entry.verified_at = now
                entry.status = EntryStatus.REGISTERED
                s.add(entry)
                s.commit()

    async def submit(self, user: User, project_id: int) -> None:
        """FR-PROJ-7/8: freeze HEAD, take the final snapshot, and start the
        writeup, all at once. Identifiers and the snippet come from the
        frozen files captured right now (FR-PROJ-9). Once and only once."""
        async with self._locks[user.id]:
            now = int(self.clock())
            with Session(self.engine) as s:
                self._open_project_or_fail(s, project_id, now)
                entry = self._entry(s, user, project_id)
                if entry is None or entry.status == EntryStatus.PENDING:
                    raise ApiError(409, "not_verified", "Register and verify a repository first.")
                if entry.status == EntryStatus.SUBMITTED:
                    raise ApiError(409, "already_submitted", "You already submitted this project.")
                if entry.status == EntryStatus.VOID:
                    raise ApiError(403, "entry_void", "Your entry was voided by the admin.")
                ref, entry_id = _ref(entry), entry.id
            head = await self._head(ref)
            files = await self._files(ref, head)
            if not files.sources:
                raise ApiError(400, "no_source_files", "Your repository has no source files at HEAD.")
            with Session(self.engine) as s:
                entry = s.get(ProjectEntry, entry_id)
                s.add(RepoSnapshot(entry_id=entry_id, taken_at=now, kind="submission", head_sha=head,
                                   source_loc=files.source_loc))
                s.flush()
                entry.submitted_at = now
                entry.frozen_sha = head
                entry.history_factor = history_of(snapshots_of(s, entry_id), self.config)
                entry.status = EntryStatus.SUBMITTED
                s.add(entry)
                s.add(build_writeup(entry, s.get(Project, project_id), files, self.config, now))
                s.commit()
            log.info("entry %s submitted at %s; writeup started", entry_id, head)

    # ------------------------------------------------------------------
    # Daily snapshots (FR-PROJ-6)
    # ------------------------------------------------------------------

    async def snapshot_entry(self, entry_id: int, now: int) -> RepoSnapshot | None:
        """Record HEAD and source lines. If HEAD hasn't moved since the last
        snapshot, its line count is reused without downloading anything."""
        with Session(self.engine) as s:
            entry = s.get(ProjectEntry, entry_id)
            if entry is None or entry.status not in ACTIVE:
                return None  # submitted or voided since the job started
            ref = _ref(entry)
            snaps = snapshots_of(s, entry_id)
        try:
            head = await self.host.head_sha(ref)
            if snaps and snaps[-1].head_sha == head:
                loc = snaps[-1].source_loc
            else:
                loc = (await self.host.files_at(ref, head)).source_loc
        except RepoUnavailable as exc:
            log.warning("snapshot of entry %s: %s", entry_id, exc)
            self._snapshot_failed_at[entry_id] = now
            with Session(self.engine) as s:
                entry = s.get(ProjectEntry, entry_id)
                if entry.status == EntryStatus.REGISTERED:
                    entry.status = EntryStatus.UNREACHABLE  # section 11: can't submit until fixed
                    s.add(entry)
                    s.commit()
            return None
        except RepoError as exc:
            log.warning("snapshot of entry %s: %s", entry_id, exc)
            self._snapshot_failed_at[entry_id] = now
            return None
        self._snapshot_failed_at.pop(entry_id, None)
        with Session(self.engine) as s:
            snap = RepoSnapshot(entry_id=entry_id, taken_at=now, kind="daily", head_sha=head, source_loc=loc)
            s.add(snap)
            entry = s.get(ProjectEntry, entry_id)
            if entry.status == EntryStatus.UNREACHABLE:
                entry.status = EntryStatus.REGISTERED
                s.add(entry)
            s.commit()
            s.refresh(snap)
            return snap

    async def daily_snapshots(self) -> int:
        """Once a day after snapshot_time: snapshot every verified, unsubmitted
        entry of the open project that has no daily snapshot today."""
        now = int(self.clock())
        tz = self.config.general.tz
        local = datetime.fromtimestamp(now, tz)
        snap_time = datetime.strptime(self.config.projects.snapshot_time, "%H:%M").time()
        if local.time() < snap_time:
            return 0
        day_start = int(datetime.combine(local.date(), datetime.min.time(), tzinfo=tz).timestamp())
        with Session(self.engine) as s:
            project = open_project(s, now)
            if project is None:
                return 0
            entries = s.exec(
                select(ProjectEntry.id, ProjectEntry.user_id).where(
                    ProjectEntry.project_id == project.id,
                    ProjectEntry.status.in_(ACTIVE),  # type: ignore[attr-defined]
                )
            ).all()
            done_today = set(
                s.exec(
                    select(RepoSnapshot.entry_id).where(
                        RepoSnapshot.kind == "daily", RepoSnapshot.taken_at >= day_start
                    )
                ).all()
            )
        taken = 0
        for entry_id, user_id in entries:
            if entry_id in done_today:
                continue
            failed = self._snapshot_failed_at.get(entry_id)
            if failed is not None and now - failed < SNAPSHOT_RETRY_SECONDS:
                continue
            async with self._locks[user_id]:  # never interleave with that member's submit
                if await self.snapshot_entry(entry_id, now):
                    taken += 1
        return taken
