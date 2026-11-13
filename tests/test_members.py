"""NFR-PRIV-1: the admin can delete a member's data on request."""

import asyncio

from sqlmodel import Session, select

from gcboard.server import admin
from gcboard.server.db import AuditLog, AuthChallenge, ProjectEntry, RepoSnapshot, TaskAttempt, Token, User, Writeup

from .sample_writeup import ANSWERS
from .test_api import login
from .test_projects import app, client, project  # noqa: F401
from .test_writeups import entry, finish, submit_project


def test_delete_member(app, client, cf, repos, clock, project, tmp_path, config, capsys):
    leaver = login(client, cf, clock, "leaver")
    stays = login(client, cf, clock, "stays")
    for subs in cf.subs.values():
        subs.clear()

    # Both solve today's task; leaver is faster.
    task = asyncio.run(app.state.jobs.post_daily_task())
    t0 = int(clock())
    for h in (leaver, stays):
        client.post("/api/v1/tasks/today/start", headers=h)
    cf.submit("leaver", task.contest_id, task.problem_index, t0 + 300, "OK")
    cf.submit("stays", task.contest_id, task.problem_index, t0 + 600, "OK")
    clock.advance(700)
    asyncio.run(app.state.jobs.verify_attempts())

    # Both submit qualifying projects; leaver first.
    submit_project(client, repos, leaver, project, repo="https://github.com/leaver/ttt")
    finish(client, clock, leaver, ANSWERS)
    submit_project(client, repos, stays, project, repo="https://github.com/stays/ttt")
    finish(client, clock, stays, ANSWERS)
    assert entry(client, stays)["submit_rank"] == 2
    board = client.get("/api/v1/leaderboard?period=week", headers=stays).json()["rows"]
    assert [(r["cf_handle"], r["points"]) for r in board] == [("leaver", 110), ("stays", 100)]

    cfg = tmp_path / "admin.toml"
    cfg.write_text(f"[server]\ndatabase = '{config.server.database}'\nadmins = ['stays']\n")
    admin.main(["--config", str(cfg), "ban", "leaver", "--reason", "leaver asked for a break"])
    admin.main(["--config", str(cfg), "unban", "leaver", "--reason", "back again"])

    # Without --yes: only a list of what would be deleted.
    admin.main(["--config", str(cfg), "delete-member", "LEAVER", "--reason", "asked by email"])
    out = capsys.readouterr().out
    assert "Would delete leaver" in out and "1 writeups" in out and "1 task attempts" in out
    with Session(app.state.engine) as s:
        assert s.exec(select(User).where(User.cf_handle == "leaver")).first() is not None

    admin.main(["--config", str(cfg), "delete-member", "leaver", "--reason", "asked by email", "--yes"])
    out = capsys.readouterr().out
    assert "deleted leaver" in out and "1 project entries" in out and "Backups taken before now" in out

    with Session(app.state.engine) as s:
        assert s.exec(select(User).where(User.cf_handle == "leaver")).first() is None
        stays_id = s.exec(select(User.id).where(User.cf_handle == "stays")).one()
        stays_entry = s.exec(select(ProjectEntry.id).where(ProjectEntry.user_id == stays_id)).one()
        # Every remaining row belongs to the member who stays.
        for model in (TaskAttempt, ProjectEntry, Token):
            assert {r.user_id for r in s.exec(select(model)).all()} == {stays_id}
        for model in (Writeup, RepoSnapshot):
            assert {r.entry_id for r in s.exec(select(model)).all()} == {stays_entry}
        assert s.exec(select(AuthChallenge).where(AuthChallenge.cf_handle == "leaver")).first() is None
        audit = s.exec(select(AuditLog)).all()
        assert all("leaver" not in (a.target + a.reason).lower() for a in audit)
        assert any(a.action == "delete-member" and a.target.startswith("member #") for a in audit)

    # Their old token no longer works, and nobody can see a profile.
    assert client.get("/api/v1/me", headers=leaver).status_code == 401
    assert client.get("/api/v1/users/leaver", headers=stays).status_code == 404
    # Everyone else is rescored as if the member never took part.
    board = client.get("/api/v1/leaderboard?period=week", headers=stays).json()["rows"]
    assert [(r["cf_handle"], r["points"], r["rank"]) for r in board] == [("stays", 110, 1)]
    assert entry(client, stays)["submit_rank"] == 1
