"""Deleting a member's data on request (NFR-PRIV-1)."""

from __future__ import annotations

import re

from sqlmodel import Session, func, select

from .db import AuditLog, AuthChallenge, ProjectEntry, RepoSnapshot, TaskAttempt, Token, User, Writeup


def member_data(session: Session, user: User) -> dict[str, list]:
    """Every stored row that belongs to the member, children first."""
    entries = session.exec(select(ProjectEntry).where(ProjectEntry.user_id == user.id)).all()
    entry_ids = [e.id for e in entries]
    by_entry = lambda model: (  # noqa: E731
        session.exec(select(model).where(model.entry_id.in_(entry_ids))).all() if entry_ids else []
    )
    return {
        "writeups": by_entry(Writeup),
        "repo snapshots": by_entry(RepoSnapshot),
        "project entries": list(entries),
        "task attempts": session.exec(select(TaskAttempt).where(TaskAttempt.user_id == user.id)).all(),
        "login tokens": session.exec(select(Token).where(Token.user_id == user.id)).all(),
        "login challenges": session.exec(
            select(AuthChallenge).where(func.lower(AuthChallenge.cf_handle) == user.cf_handle.lower())
        ).all(),
    }


def delete_member(session: Session, user: User) -> dict[str, int]:
    """Delete the member and everything stored about them. The audit log keeps
    its entries, but the member's handle is replaced with "deleted member #id"
    so the log no longer names them. The caller commits and rescores."""
    uid, handle = user.id, user.cf_handle
    counts = {}
    for name, rows in member_data(session, user).items():
        for row in rows:
            session.delete(row)
        session.flush()  # children before parents, so foreign keys hold
        counts[name] = len(rows)
    pattern = re.compile(re.escape(handle), re.I)
    for entry in session.exec(select(AuditLog)).all():
        if entry.admin_id == uid:
            entry.admin_id = None
        entry.target = pattern.sub(f"deleted member #{uid}", entry.target)
        entry.reason = pattern.sub(f"deleted member #{uid}", entry.reason)
        session.add(entry)
    session.flush()
    session.delete(user)
    session.flush()
    return counts
