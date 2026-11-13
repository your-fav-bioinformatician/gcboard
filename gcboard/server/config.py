"""Server configuration (config.toml). All tunable values live here (NFR-MAINT-2)."""

from __future__ import annotations

import tomllib
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import scoring
from .timewin import parse_hhmm


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneralConfig(_Section):
    timezone: str = "UTC"
    daily_post_time: str = "00:00"

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA time zone: {v!r}") from exc
        return v

    @field_validator("daily_post_time")
    @classmethod
    def _check_post_time(cls, v: str) -> str:
        parse_hhmm(v)
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def post_time(self) -> time:
        return parse_hhmm(self.daily_post_time)


class ServerConfig(_Section):
    database: str = "gcboard.db"
    admins: list[str] = Field(default_factory=list)  # Codeforces handles (FR-AUTH-6)
    min_client_version: str = "0.1.0"
    requests_per_minute: int = 60
    codeforces_min_interval: float = 2.0
    challenge_minutes: int = 5
    post_retry_minutes: int = 5
    post_retry_hours: int = 6
    # Read-only GitHub token for repo reads. Prefer the GCBOARD_GITHUB_TOKEN
    # environment variable, which overrides this. Never sent to clients.
    github_token: str = ""


class TasksConfig(_Section):
    rating_by_weekday: list[int] = Field(default_factory=lambda: [800, 900, 1000, 1100, 1200, 1300, 1400])
    rating_fallback_steps: list[int] = Field(default_factory=lambda: [100, 200])
    excluded_tags: list[str] = Field(default_factory=lambda: ["*special", "interactive"])
    base_max: int = 100
    base_step: int = 10
    base_min: int = 30
    runtime_bonus: list[int] = Field(default_factory=lambda: [10, 6, 3])
    penalty_per_reject: int = 5
    penalty_cap: int = 20
    min_task_points: int = 10

    @field_validator("rating_by_weekday")
    @classmethod
    def _seven_days(cls, v: list[int]) -> list[int]:
        if len(v) != 7:
            raise ValueError("rating_by_weekday needs 7 values, Monday to Sunday")
        return v

    def rules(self) -> scoring.TaskRules:
        return scoring.TaskRules(
            base_max=self.base_max,
            base_step=self.base_step,
            base_min=self.base_min,
            runtime_bonus=tuple(self.runtime_bonus),
            penalty_per_reject=self.penalty_per_reject,
            penalty_cap=self.penalty_cap,
            min_task_points=self.min_task_points,
        )


class ProjectsConfig(_Section):
    length_days: int = 14
    max_registration_loc: int = 50
    snapshot_time: str = "12:00"  # daily snapshots, group time zone
    max_repo_mb: int = 50  # largest repo download the server accepts

    @field_validator("snapshot_time")
    @classmethod
    def _check_snapshot_time(cls, v: str) -> str:
        parse_hhmm(v)
        return v
    speed_max: float = 300
    speed_decay: float = 0.85
    speed_min: float = 90
    pass_threshold: float = 40

    def rules(self) -> scoring.ProjectRules:
        return scoring.ProjectRules(self.speed_max, self.speed_decay, self.speed_min, self.pass_threshold)


class WriteupConfig(_Section):
    time_limit_minutes: int = 45
    min_words: list[int] = Field(default_factory=lambda: [80, 100, 60, 60])
    autosave_seconds: int = 10
    max_chars_per_second: int = 25
    burst_allowance_chars: int = 50
    max_answer_chars: int = 10000


class HistoryConfig(_Section):
    base: float = 0.4
    per_active_interval: float = 0.2
    dump_threshold: float = 0.70
    dump_multiplier: float = 0.75
    floor: float = 0.5

    def rules(self) -> scoring.HistoryRules:
        return scoring.HistoryRules(
            self.base, self.per_active_interval, self.dump_threshold, self.dump_multiplier, self.floor
        )


class Config(_Section):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    projects: ProjectsConfig = Field(default_factory=ProjectsConfig)
    writeup: WriteupConfig = Field(default_factory=WriteupConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)

    def is_admin(self, handle: str) -> bool:
        return handle.lower() in {h.lower() for h in self.server.admins}


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as f:
        return Config.model_validate(tomllib.load(f))
