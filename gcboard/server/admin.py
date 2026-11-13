"""gcboard-admin: server-side admin operations (FR-ADMIN-*).

Every action that changes data is written to the audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlmodel import Session, func, select

from .codeforces import CodeforcesClient
from .config import Config, load_config
from .db import AuditLog, DailyTask, EntryStatus, Project, ProjectEntry, TaskAttempt, Token, User, make_engine
from .projects import overlapping
from .jobs import Jobs
from .timewin import current_window


def _audit(session: Session, config: Config, admin: str | None, action: str, target: str, reason: str) -> None:
    admin_handle = admin or (config.server.admins[0] if config.server.admins else None)
    admin_user = (
        session.exec(select(User).where(func.lower(User.cf_handle) == admin_handle.lower())).first()
        if admin_handle
        else None
    )
    session.add(
        AuditLog(at=int(time.time()), admin_id=admin_user.id if admin_user else None,
                 action=action, target=target, reason=reason)
    )


class ProjectFile(BaseModel):
    """A project definition file for project-create / project-edit (FR-PROJ-1)."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    spec_md: str | None = None  # inline Markdown, or:
    spec_file: str | None = None  # Markdown file, relative to the project file
    opens: str  # "YYYY-MM-DD HH:MM" in the group time zone
    length_days: int | None = Field(default=None, ge=1, le=60)
    change_question: str = Field(min_length=10)
    change_keyword_groups: list[list[str]] = Field(min_length=1)

    @field_validator("change_keyword_groups")
    @classmethod
    def _groups(cls, v: list[list[str]]) -> list[list[str]]:
        if any(not g or any(not t.strip() for t in g) for g in v):
            raise ValueError("every keyword group needs at least one non-empty term")
        return [[t.strip().lower() for t in g] for g in v]


def load_project_file(path: str, config: Config) -> tuple[ProjectFile, str, int, int]:
    """Returns (definition, spec markdown, opens_at, closes_at)."""
    file = Path(path)
    try:
        pf = ProjectFile.model_validate(tomllib.loads(file.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        sys.exit(f"invalid project file {path}: {exc}")
    if (pf.spec_md is None) == (pf.spec_file is None):
        sys.exit("give exactly one of spec_md or spec_file")
    spec = pf.spec_md if pf.spec_md is not None else (file.parent / pf.spec_file).read_text(encoding="utf-8")
    try:
        opens_local = datetime.strptime(pf.opens, "%Y-%m-%d %H:%M").replace(tzinfo=config.general.tz)
    except ValueError:
        sys.exit(f"opens must look like 2026-11-16 00:00 (got {pf.opens!r})")
    days = pf.length_days or config.projects.length_days
    closes_local = (opens_local.replace(tzinfo=None) + timedelta(days=days)).replace(tzinfo=config.general.tz)
    return pf, spec, int(opens_local.timestamp()), int(closes_local.timestamp())


def _fmt(epoch: int, config: Config) -> str:
    return datetime.fromtimestamp(epoch, config.general.tz).strftime("%Y-%m-%d %H:%M")


def _user(session: Session, handle: str) -> User:
    u = session.exec(select(User).where(func.lower(User.cf_handle) == handle.lower())).first()
    if u is None:
        sys.exit(f"no member with handle {handle!r}")
    return u


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="gcboard-admin", description="gcboard server administration")
    p.add_argument("--config", default=os.environ.get("GCBOARD_CONFIG", "config.toml"))
    p.add_argument("--admin", help="admin handle for the audit log (default: first admin in config)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate-config", help="check config.toml and print the effective values")
    sub.add_parser("recompute", help="rebuild every score from stored raw data")
    sub.add_parser("users", help="list members")
    sub.add_parser("sync-ratings", help="refresh every member's Codeforces rating now")
    sub.add_parser("projects", help="list projects")
    pc = sub.add_parser("project-create", help="create a project from a TOML file")
    pc.add_argument("file")
    pe = sub.add_parser("project-edit", help="replace a project's definition from a TOML file")
    pe.add_argument("project_id", type=int)
    pe.add_argument("file")
    pe.add_argument("--reason", required=True)
    ve = sub.add_parser("void-entry", help="void a member's project entry")
    ve.add_argument("handle")
    ve.add_argument("project_id", type=int)
    ve.add_argument("--reason", required=True)
    a = sub.add_parser("audit", help="show the audit log, newest first")
    a.add_argument("--limit", type=int, default=50)
    r = sub.add_parser("reroll", help="replace today's task (only if nobody has started it)")
    r.add_argument("--reason", required=True)
    for name in ("ban", "unban", "revoke-tokens"):
        c = sub.add_parser(name)
        c.add_argument("handle")
        c.add_argument("--reason", required=True)
    v = sub.add_parser("void-attempt", help="void a member's attempt on a task")
    v.add_argument("handle")
    v.add_argument("task_date", help="YYYY-MM-DD")
    v.add_argument("--reason", required=True)
    args = p.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValidationError) as exc:
        sys.exit(f"invalid config {args.config}: {exc}")
    if args.cmd == "validate-config":
        print(config.model_dump_json(indent=2))
        print("config OK")
        return

    engine = make_engine(config.server.database)
    jobs = Jobs(engine, config, CodeforcesClient(min_interval=config.server.codeforces_min_interval))

    if args.cmd == "users":
        with Session(engine) as s:
            for u in s.exec(select(User).order_by(User.cf_handle)).all():
                flags = " ".join(f for f, on in (("admin", u.is_admin), ("banned", u.banned)) if on)
                print(f"{u.cf_handle:24} {u.cf_rating or '-':>6}  {flags}")
        return

    if args.cmd == "audit":
        with Session(engine) as s:
            rows = s.exec(
                select(AuditLog, User.cf_handle)
                .join(User, User.id == AuditLog.admin_id, isouter=True)
                .order_by(AuditLog.at.desc(), AuditLog.id.desc())  # type: ignore[attr-defined]
                .limit(args.limit)
            ).all()
            for entry, handle in rows:
                when = datetime.fromtimestamp(entry.at, config.general.tz).strftime("%Y-%m-%d %H:%M")
                print(f"{when}  {handle or '-':16} {entry.action:14} {entry.target:24} {entry.reason}")
        return

    if args.cmd == "projects":
        now = time.time()
        with Session(engine) as s:
            for p in s.exec(select(Project).order_by(Project.opens_at)).all():
                state = "open" if p.opens_at <= now < p.closes_at else ("upcoming" if now < p.opens_at else "closed")
                n = len(s.exec(select(ProjectEntry.id).where(ProjectEntry.project_id == p.id)).all())
                print(f"{p.id:>3}  {_fmt(p.opens_at, config)} -> {_fmt(p.closes_at, config)}  {state:8} {n:>3} entries  {p.title}")
        return

    if args.cmd in ("project-create", "project-edit"):
        pf, spec, opens_at, closes_at = load_project_file(args.file, config)
        with Session(engine) as s:
            ignore = args.project_id if args.cmd == "project-edit" else None
            if clash := overlapping(s, opens_at, closes_at, ignore_id=ignore):
                sys.exit(f"overlaps project {clash.id} ({clash.title}); only one project may be open at a time")
            if args.cmd == "project-edit":
                p = s.get(Project, args.project_id)
                if p is None:
                    sys.exit(f"no project {args.project_id}")
                if p.closes_at <= time.time():
                    sys.exit("cannot edit a project that has closed")
                reason = args.reason
            else:
                p = Project(title="", spec_md="", opens_at=0, closes_at=0, change_question="")
                reason = "created"
            p.title, p.spec_md, p.opens_at, p.closes_at = pf.title, spec, opens_at, closes_at
            p.change_question, p.change_keyword_groups = pf.change_question, pf.change_keyword_groups
            s.add(p)
            s.flush()
            _audit(s, config, args.admin, args.cmd, f"project {p.id}", reason)
            s.commit()
            print(f"project {p.id}: {p.title}  {_fmt(opens_at, config)} -> {_fmt(closes_at, config)}")
        return

    if args.cmd == "sync-ratings":
        n = asyncio.run(jobs.sync_ratings(force=True))
        print(f"synced {n} members")
        return

    if args.cmd == "recompute":
        n = jobs.recompute_all()
        with Session(engine) as s:
            _audit(s, config, args.admin, "recompute", "all", "manual recompute")
            s.commit()
        print(f"recomputed {n} tasks")
        return

    if args.cmd == "reroll":
        now = time.time()
        date_s = current_window(now, config.general.tz, config.general.post_time).task_date.isoformat()
        with Session(engine) as s:
            task = s.exec(select(DailyTask).where(DailyTask.task_date == date_s)).first()
            if task is None:
                sys.exit(f"no task posted for {date_s}")
            if s.exec(select(TaskAttempt).where(TaskAttempt.task_id == task.id)).first():
                sys.exit("cannot reroll: a member has already started today's task")
            old = (task.contest_id, task.problem_index)
            s.delete(task)
            _audit(s, config, args.admin, "reroll", f"{date_s} {old[0]}{old[1]}", args.reason)
            s.commit()
        new = asyncio.run(jobs.post_daily_task(force=True, exclude={old}))
        if new is None:
            sys.exit("reroll failed to pick a new problem; the scheduler will retry posting")
        print(f"rerolled {date_s}: {new.contest_id}{new.problem_index} {new.name} ({new.rating})")
        return

    with Session(engine) as s:
        u = _user(s, args.handle)
        if args.cmd in ("ban", "unban"):
            u.banned = args.cmd == "ban"
            s.add(u)
        elif args.cmd == "revoke-tokens":
            for t in s.exec(select(Token).where(Token.user_id == u.id, Token.revoked_at == None)).all():  # noqa: E711
                t.revoked_at = int(time.time())
                s.add(t)
        elif args.cmd == "void-entry":
            e = s.exec(
                select(ProjectEntry).where(ProjectEntry.project_id == args.project_id, ProjectEntry.user_id == u.id)
            ).first()
            if e is None:
                sys.exit(f"{u.cf_handle} has no entry in project {args.project_id}")
            e.status, e.void_reason = EntryStatus.VOID, args.reason
            s.add(e)
        elif args.cmd == "void-attempt":
            task = s.exec(select(DailyTask).where(DailyTask.task_date == args.task_date)).first()
            a = task and s.exec(
                select(TaskAttempt).where(TaskAttempt.task_id == task.id, TaskAttempt.user_id == u.id)
            ).first()
            if not a:
                sys.exit(f"{u.cf_handle} has no attempt on {args.task_date}")
            a.voided_at, a.void_reason = int(time.time()), args.reason
            s.add(a)
        _audit(s, config, args.admin, args.cmd, u.cf_handle, args.reason)
        s.commit()
    if args.cmd in ("ban", "unban", "void-attempt"):
        jobs.recompute_all()  # banned and voided attempts no longer push others down
    print(f"{args.cmd} {args.handle}: done")


if __name__ == "__main__":
    main()
