"""gcboard-admin: server-side admin operations (FR-ADMIN-*).

Every action that changes data is written to the audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

from pydantic import ValidationError
from sqlmodel import Session, func, select

from .codeforces import CodeforcesClient
from .config import Config, load_config
from .db import AuditLog, DailyTask, TaskAttempt, Token, User, make_engine
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
