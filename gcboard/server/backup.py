"""Database backups (section 8 backup_db, NFR-REL-2).

Backups use SQLite's online backup API, so they are consistent while the
server keeps running (WAL mode included). One file per day in the group time
zone, named gcboard-YYYY-MM-DD.db; the newest `keep` files are kept.

Restore procedure (also in docs/operations.md):
    1. Stop the server.
    2. gcboard-admin restore backups/gcboard-YYYY-MM-DD.db --yes
       (checks the backup, then keeps the current database as
       <database>.pre-restore-<timestamp> before replacing it)
    3. Start the server.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_NAME = re.compile(r"^gcboard-(\d{4}-\d{2}-\d{2})\.db$")
REQUIRED_TABLES = {"users", "tokens", "daily_tasks", "task_attempts", "projects", "project_entries", "writeups"}


class BackupError(Exception):
    pass


def backup_name(day: str) -> str:
    return f"gcboard-{day}.db"


def list_backups(backup_dir: Path) -> list[Path]:
    """Backup files, oldest first."""
    if not backup_dir.is_dir():
        return []
    return sorted(p for p in backup_dir.iterdir() if _NAME.match(p.name))


def backup_to(db_path: Path, target: Path) -> Path:
    """Consistent online copy of the live database."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            # The copy inherits WAL mode; switch it to a single self-contained
            # file, so reading or moving a backup never needs -wal/-shm files.
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()
    tmp.replace(target)
    return target


def check_backup(path: Path) -> None:
    """Raise BackupError unless `path` is an intact gcboard database."""
    if not path.is_file():
        raise BackupError(f"{path} does not exist")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            (result,) = con.execute("PRAGMA integrity_check").fetchone()
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"{path} is not a readable SQLite database: {exc}") from exc
    if result != "ok":
        raise BackupError(f"{path} failed the integrity check: {result}")
    if missing := REQUIRED_TABLES - tables:
        raise BackupError(f"{path} is not a gcboard database (missing {', '.join(sorted(missing))})")


def restore(backup: Path, db_path: Path, now: float | None = None) -> Path:
    """Replace the database with a checked backup. The server must be stopped.
    Returns where the previous database was kept."""
    check_backup(backup)
    stamp = datetime.fromtimestamp(now or time.time()).strftime("%Y%m%d-%H%M%S")
    kept = db_path.with_name(f"{db_path.name}.pre-restore-{stamp}")
    if db_path.exists():
        backup_to(db_path, kept)  # consistent copy, WAL included
    # Copy page by page through SQLite rather than swapping files, so the
    # live database's WAL and locks are handled by SQLite itself.
    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return kept


class BackupJob:
    """Daily backup once `backup_time` has passed in the group time zone."""

    def __init__(
        self,
        db_path: str,
        backup_dir: str,
        keep: int,
        tz: ZoneInfo,
        after: str = "03:00",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.keep = keep
        self.tz = tz
        self.after = datetime.strptime(after, "%H:%M").time()
        self.clock = clock

    async def run(self) -> Path | None:
        local = datetime.fromtimestamp(self.clock(), self.tz)
        if local.time() < self.after:
            return None
        target = self.backup_dir / backup_name(local.date().isoformat())
        if target.exists():
            return None
        backup_to(self.db_path, target)
        check_backup(target)
        for old in list_backups(self.backup_dir)[: -self.keep]:
            old.unlink()
        log.info("database backed up to %s", target)
        return target
