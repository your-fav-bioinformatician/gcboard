"""Database tables (SRD section 6). SQLite in WAL mode via SQLModel.

Timestamps are integer Unix epoch seconds (UTC) throughout. The writeups
table arrives with milestone M4.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, UniqueConstraint, event
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


class Project(SQLModel, table=True):
    __tablename__ = "projects"
    id: int | None = Field(default=None, primary_key=True)
    title: str
    spec_md: str
    opens_at: int = Field(index=True)
    closes_at: int = Field(index=True)
    change_question: str
    change_keyword_groups: list[list[str]] = Field(default_factory=list, sa_column=Column(JSON))
    finalized_at: int | None = None


class EntryStatus:
    PENDING = "pending"  # registered, .gcboard not verified yet
    REGISTERED = "registered"  # verified; snapshots running
    UNREACHABLE = "unreachable"  # last snapshot failed (repo private or deleted)
    SUBMITTED = "submitted"  # frozen; writeup comes next (M4)
    VOID = "void"  # voided by the admin


class ProjectEntry(SQLModel, table=True):
    """A member's participation in one project.

    repo_url is not a database-level UNIQUE as in SRD section 6: an unverified
    registration must not lock a repo away from its real owner. The server
    instead refuses any URL already on a verified entry, in any project.
    """

    __tablename__ = "project_entries"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="projects.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    repo_url: str = Field(index=True)  # normalized https://github.com/owner/repo, lowercase
    verify_code: str
    registered_at: int
    verified_at: int | None = None
    submitted_at: int | None = None
    frozen_sha: str | None = None
    history_factor: float | None = None
    submit_rank: int | None = None
    speed_points: float | None = None
    points: int = 0
    status: str = EntryStatus.PENDING
    void_reason: str | None = None


class RepoSnapshot(SQLModel, table=True):
    __tablename__ = "repo_snapshots"
    id: int | None = Field(default=None, primary_key=True)
    entry_id: int = Field(foreign_key="project_entries.id", index=True)
    taken_at: int
    kind: str  # registration | daily | submission
    head_sha: str
    source_loc: int


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
    "EntryStatus",
    "Project",
    "ProjectEntry",
    "RepoSnapshot",
    "Session",
    "TaskAttempt",
    "Token",
    "User",
    "make_engine",
]
