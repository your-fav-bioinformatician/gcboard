"""Server configuration (config.toml). All tunable values live here (NFR-MAINT-2)."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import scoring, textrules
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
    backup_dir: str = "backups"  # relative paths are relative to the database file
    backup_keep: int = 14
    backup_time: str = "03:00"  # daily, group time zone

    @field_validator("backup_time")
    @classmethod
    def _check_backup_time(cls, v: str) -> str:
        parse_hhmm(v)
        return v

    @property
    def backup_path(self) -> Path:
        p = Path(self.backup_dir)
        return p if p.is_absolute() else Path(self.database).resolve().parent / p


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
    # Grading constants (SRD 5.3)
    gate_min_words: int = 20
    gate_min_total: int = 150
    gate_max_jaccard: float = 0.8
    snippet_ids_target: int = 3
    other_ids_target: int = 6
    ttr_window: int = 100
    ttr_target: float = 0.55
    density_full: float = 0.20
    density_zero: float = 0.45
    sentence_min: int = 6
    sentence_max: int = 35
    sentence_hard_max: int = 80

    @field_validator("min_words")
    @classmethod
    def _four(cls, v: list[int]) -> list[int]:
        if len(v) != 4:
            raise ValueError("min_words needs 4 values, one per question")
        return v

    def rules(self) -> "textrules.GradingRules":
        return textrules.GradingRules(
            min_words=tuple(self.min_words),  # type: ignore[arg-type]
            gate_min_words=self.gate_min_words,
            gate_min_total=self.gate_min_total,
            gate_max_jaccard=self.gate_max_jaccard,
            snippet_ids_target=self.snippet_ids_target,
            other_ids_target=self.other_ids_target,
            ttr_window=self.ttr_window,
            ttr_target=self.ttr_target,
            density_full=self.density_full,
            density_zero=self.density_zero,
            sentence_min=self.sentence_min,
            sentence_max=self.sentence_max,
            sentence_hard_max=self.sentence_hard_max,
        )


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


CONFIG_ENV = "GCBOARD_CONFIG_TOML"


def load_server_config(path: str | Path, environ: Mapping[str, str] = os.environ) -> Config:
    """The config file if it exists, else the whole TOML from the
    GCBOARD_CONFIG_TOML variable (for hosts like Railway, where config lives
    in service variables). Then platform defaults and the storage check."""
    if Path(path).exists():
        config = load_config(path)
    elif environ.get(CONFIG_ENV):
        config = Config.model_validate(tomllib.loads(environ[CONFIG_ENV]))
    else:
        raise ValueError(f"no config: create {path} or set the {CONFIG_ENV} variable to its contents")
    return check_storage(config, environ)


def check_storage(config: Config, environ: Mapping[str, str] = os.environ) -> Config:
    """On Railway, keep the database on the attached volume: anything else is
    wiped on every redeploy. With no database set, default to the volume."""
    volume = environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    on_railway = bool(environ.get("RAILWAY_PROJECT_ID") or environ.get("RAILWAY_ENVIRONMENT_NAME"))
    if not on_railway and not volume:
        return config
    if not volume:
        raise ValueError("no volume attached: attach a Railway volume (e.g. at /data) so the database survives redeploys")
    if "database" not in config.server.model_fields_set:
        config.server.database = str(Path(volume) / "gcboard.db")
    db = Path(config.server.database)
    if not db.is_absolute() or Path(volume).resolve() not in db.resolve().parents:
        raise ValueError(f"database {db} is not on the volume {volume}; it would be lost on redeploy")
    return config
