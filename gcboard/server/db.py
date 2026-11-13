"""Database tables (SRD section 6). SQLite in WAL mode via SQLModel.

Timestamps are integer Unix epoch seconds (UTC) throughout. Tables for
projects, entries, snapshots, and writeups arrive with milestones M3 and M4.
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None, primary_key=True)
    cf_handle: str = Field(unique=True, index=True)  # canonical case from Codeforces
    cf_rating: int | None = None
    cf_rank: str | None = None
    is_admin: bool = False
    banned: bool = False
    joined_at: int
    rating_synced_at: int | None = None


class Token(SQLModel, table=True):
    __tablename__ = "tokens"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_at: int
    revoked_at: int | None = None


class AuthChallenge(SQLModel, table=True):
    __tablename__ = "auth_challenges"
    id: int | None = Field(default=None, primary_key=True)
    cf_handle: str = Field(index=True)
    contest_id: int
    problem_index: str
    issued_at: int
    expires_at: int
    used_at: int | None = None


class DailyTask(SQLModel, table=True):
    __tablename__ = "daily_tasks"
    id: int | None = Field(default=None, primary_key=True)
    task_date: str = Field(unique=True, index=True)  # ISO date in the group time zone
    contest_id: int
    problem_index: str
    name: str
    rating: int
    posted_at: int
    closes_at: int
    finalized_at: int | None = None


class TaskAttempt(SQLModel, table=True):
    __tablename__ = "task_attempts"
    __table_args__ = (UniqueConstraint("user_id", "task_id"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    task_id: int = Field(foreign_key="daily_tasks.id", index=True)
    started_at: int
    accepted_at: int | None = None
    submission_id: int | None = None
    runtime_ms: int | None = None
    rejected_count: int | None = None
    solve_seconds: int | None = None
    points: int = 0
    provisional: bool = True
    voided_at: int | None = None
    void_reason: str | None = None


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"
    id: int | None = Field(default=None, primary_key=True)
    at: int
    admin_id: int | None = Field(default=None, foreign_key="users.id")
    action: str
    target: str
    reason: str


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    if type(dbapi_conn).__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def make_engine(path: str) -> Engine:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


__all__ = [
    "AuditLog",
    "AuthChallenge",
    "DailyTask",
    "Session",
    "TaskAttempt",
    "Token",
    "User",
    "make_engine",
]
