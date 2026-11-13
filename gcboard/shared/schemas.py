"""Request and response models shared by the client and the server (section 7)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Codeforces handles: 3-24 characters of letters, digits, underscore, dot, dash.
HANDLE_PATTERN = r"^[A-Za-z0-9_.\-]{3,24}$"


class ErrorBody(BaseModel):
    error: str
    message: str


class TimeResponse(BaseModel):
    server_time: float
    server_version: str
    min_client_version: str


class HandleRequest(BaseModel):
    cf_handle: str = Field(pattern=HANDLE_PATTERN)


class ChallengeResponse(BaseModel):
    cf_handle: str
    contest_id: int
    index: str
    name: str
    url: str
    issued_at: int
    expires_at: int


class VerifyResponse(BaseModel):
    token: str
    cf_handle: str
    is_admin: bool


class SnippetInfo(BaseModel):
    path: str
    start: int
    end: int
    text: str


class WriteupSession(BaseModel):
    """An open writeup: while this exists the client shows nothing else."""

    server_time: float
    questions: list[str]
    snippet: SnippetInfo
    started_at: int
    deadline: int
    answers: list[str]  # last accepted text
    min_words: list[int]
    autosave_seconds: int
    max_answer_chars: int


class DraftRequest(BaseModel):
    answers: list[str] = Field(min_length=4, max_length=4)


class SubmitWriteupRequest(BaseModel):
    answers: list[str] | None = Field(default=None, min_length=4, max_length=4)


class WriteupResult(BaseModel):
    score: float
    passed: bool  # at or above the project pass threshold
    auto_submitted: bool
    submitted_at: int
    breakdown: dict[str, Any]


class Me(BaseModel):
    cf_handle: str
    is_admin: bool
    cf_rating: int | None
    cf_rank: str | None
    joined_at: int
    writeup: WriteupSession | None = None  # open writeup session: the client locks onto it


class ProblemInfo(BaseModel):
    contest_id: int
    index: str
    name: str
    url: str


class AttemptInfo(BaseModel):
    started_at: int
    solved: bool
    accepted_at: int | None
    solve_seconds: int | None
    runtime_ms: int | None
    rejected_count: int | None
    points: int
    provisional: bool


class TodayTask(BaseModel):
    task_date: str
    rating: int
    posted_at: int
    closes_at: int
    problem: ProblemInfo | None  # only after the member starts (FR-TASK-5)
    attempt: AttemptInfo | None


class TodayResponse(BaseModel):
    server_time: float
    task: TodayTask | None
    message: str | None = None


class BoardRow(BaseModel):
    rank: int
    cf_handle: str
    points: int
    provisional: bool
    solved: int
    streak: int


class LeaderboardResponse(BaseModel):
    period: Literal["week", "all"]
    label: str
    ends_at: int | None
    rows: list[BoardRow]


class ProjectHistoryRow(BaseModel):
    title: str
    submit_rank: int | None
    writeup_score: float | None
    history_factor: float | None
    points: int
    provisional: bool


class Profile(BaseModel):
    """FR-PROF-1. Visible to every member; writeup answers are never included."""

    cf_handle: str
    cf_rating: int | None
    cf_rank: str | None
    rating_synced_at: int | None
    joined_at: int
    current_streak: int
    best_streak: int
    solved_week: int
    solved_all: int
    avg_solve_seconds: int | None
    fastest_solve_seconds: int | None
    weekly_wins: int
    best_weekly_finish: int | None
    projects: list[ProjectHistoryRow]


class RulesResponse(BaseModel):
    timezone: str
    daily_post_time: str
    tasks: dict[str, Any]
    projects: dict[str, Any]
    writeup: dict[str, Any]
    history: dict[str, Any]


class RegisterRequest(BaseModel):
    repo_url: str = Field(min_length=10, max_length=200)


class SnapshotInfo(BaseModel):
    taken_at: int
    kind: Literal["registration", "daily", "submission"]
    head_sha: str
    source_loc: int


class EntryInfo(BaseModel):
    repo_url: str
    status: Literal["pending", "registered", "unreachable", "submitted", "void"]
    verify_code: str | None  # only while the .gcboard file is not verified yet
    registered_at: int
    verified_at: int | None
    submitted_at: int | None
    frozen_sha: str | None
    history_factor: float | None  # final once submitted; otherwise "if you submitted now"
    snapshots: list[SnapshotInfo]
    writeup: WriteupResult | None = None
    submit_rank: int | None = None  # among qualified entries
    points: int = 0
    provisional: bool = True  # until the project closes and every writeup has ended


class ProjectInfo(BaseModel):
    """The change question is not included: it is revealed in the writeup (FR-WRITE-1)."""

    id: int
    title: str
    spec_md: str
    opens_at: int
    closes_at: int
    is_open: bool = True  # False: the most recent project, already closed


class UpcomingProject(BaseModel):
    id: int
    title: str
    opens_at: int
    closes_at: int


class ProjectResponse(BaseModel):
    server_time: float
    project: ProjectInfo | None
    upcoming: UpcomingProject | None
    entry: EntryInfo | None
    max_registration_loc: int
    writeup_minutes: int
