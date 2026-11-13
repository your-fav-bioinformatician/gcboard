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


class Me(BaseModel):
    cf_handle: str
    is_admin: bool
    cf_rating: int | None
    cf_rank: str | None
    joined_at: int
    writeup: dict[str, Any] | None = None  # open writeup session (M4)


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


class RulesResponse(BaseModel):
    timezone: str
    daily_post_time: str
    tasks: dict[str, Any]
    projects: dict[str, Any]
    writeup: dict[str, Any]
    history: dict[str, Any]
