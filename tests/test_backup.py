"""M5: daily backups and the restore procedure (NFR-REL-2)."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from gcboard.server import admin
from gcboard.server.backup import BackupError, BackupJob, check_backup, list_backups, restore
from gcboard.server.db import User, make_engine
from gcboard.server.main import create_app

from .test_api import login

HOUR = 3600


def users_in(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return sorted(r[0] for r in con.execute("SELECT cf_handle FROM users"))
    finally:
        con.close()


@pytest.fixture
def app(config, cf, repos, clock):
    return create_app(config, cf=cf, repos=repos, clock=clock, run_scheduler=False)


def test_daily_backup_job_and_retention(app, cf, clock, config):
    with TestClient(app) as client:
        login(client, cf, clock, "noor")
    job = app.state.jobs.backup
    assert job.backup_dir == Path(config.server.database).parent / "backups"  # next to the database
    clock.t -= 10 * HOUR  # Wednesday 02:00 UTC: before backup_time 03:00
    assert asyncio.run(job.run()) is None
    clock.t += 2 * HOUR
    first = asyncio.run(job.run())
    assert first.name == "gcboard-2026-11-11.db" and users_in(first) == ["noor"]
    assert asyncio.run(job.run()) is None  # once a day
    for _ in range(20):
        clock.t += 24 * HOUR
        asyncio.run(job.run())
    kept = list_backups(job.backup_dir)
    assert len(kept) == 14 and kept[-1].name == "gcboard-2026-12-01.db" and first not in kept
    # Each backup is one self-contained file: no -wal/-shm left beside it.
    assert sorted(p.name for p in job.backup_dir.iterdir()) == [p.name for p in kept]
    check_backup(kept[-1])
    assert sorted(p.name for p in job.backup_dir.iterdir()) == [p.name for p in kept]


def test_tick_runs_the_backup(app, clock):
    asyncio.run(app.state.jobs.tick())  # 12:00, after backup_time
    assert len(list_backups(app.state.jobs.backup.backup_dir)) == 1


def test_backup_restore_round_trip(app, cf, clock, config, tmp_path):
    db = Path(config.server.database)
    with TestClient(app) as client:
        login(client, cf, clock, "noor")
        backup = asyncio.run(app.state.jobs.backup.run())
        login(client, cf, clock, "zaid99")  # happens after the backup
    assert users_in(db) == ["noor", "zaid99"]

    app.state.engine.dispose()  # "stop the server"
    kept = restore(backup, db, now=clock())
    assert users_in(db) == ["noor"]
    assert users_in(kept) == ["noor", "zaid99"]  # nothing is lost: the replaced database is kept
    # The restored database works normally (WAL comes back on first open).
    with Session(make_engine(str(db))) as s:
        assert [u.cf_handle for u in s.exec(select(User)).all()] == ["noor"]


def test_restore_refuses_bad_files(app, config, tmp_path):
    db = Path(config.server.database)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    with pytest.raises(BackupError, match="not a readable SQLite database"):
        restore(junk, db)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
    with pytest.raises(BackupError, match="not a gcboard database"):
        check_backup(other)
    with pytest.raises(BackupError, match="does not exist"):
        check_backup(tmp_path / "missing.db")


def test_admin_backup_and_restore_commands(app, cf, clock, config, tmp_path, capsys):
    cfg = tmp_path / "admin.toml"
    cfg.write_text(f"[server]\ndatabase = '{config.server.database}'\nadmins = ['noor']\n")
    with TestClient(app) as client:
        login(client, cf, clock, "noor")
    admin.main(["--config", str(cfg), "backup"])
    admin.main(["--config", str(cfg), "backups"])
    out = capsys.readouterr().out
    assert "backed up to" in out and "1 backups in" in out
    backup = list_backups(Path(config.server.database).parent / "backups")[0]
    app.state.engine.dispose()  # "stop the server"
    with pytest.raises(SystemExit, match="Stop the server"):
        admin.main(["--config", str(cfg), "restore", str(backup)])
    admin.main(["--config", str(cfg), "restore", str(backup), "--yes"])
    assert "previous database kept as" in capsys.readouterr().out
    admin.main(["--config", str(cfg), "audit"])
    assert "restore" in capsys.readouterr().out
