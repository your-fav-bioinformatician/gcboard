"""M3: project registration, verification, snapshots, submission."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from gcboard.server import admin
from gcboard.server.db import Project
from gcboard.server.main import create_app

from .test_api import login

DAY = 24 * 3600
REPO = "https://github.com/noor/ttt"


def code_lines(n: int) -> str:
    return "".join(f"x{i} = {i}\n" for i in range(n))


@pytest.fixture
def app(config, cf, repos, clock):
    return create_app(config, cf=cf, repos=repos, clock=clock, run_scheduler=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def project(app, clock) -> int:
    """Open since Monday 00:00 (two days before the clock fixture), 14 days long."""
    opens = int(datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp())
    with Session(app.state.engine) as s:
        p = Project(
            title="Terminal tic-tac-toe", spec_md="# Tic-tac-toe\nBuild it.", opens_at=opens,
            closes_at=opens + 14 * DAY, change_question="How would you add undo?",
            change_keyword_groups=[["undo"], ["stack", "history"]],
        )
        s.add(p)
        s.commit()
        return p.id


def url(pid: int, action: str = "") -> str:
    return f"/api/v1/projects/{pid}/{action}".rstrip("/")


def register_and_verify(client, repos, headers, pid, repo=REPO, lines=0) -> str:
    if repo not in repos.repos:
        repos.push(repo, {"README.md": "new repo"})
    r = client.post(url(pid, "register"), json={"repo_url": repo}, headers=headers)
    assert r.status_code == 200, r.text
    code = r.json()["entry"]["verify_code"]
    repos.push(repo, {".gcboard": code + "\n", "main.py": code_lines(lines)})
    r = client.post(url(pid, "register/verify"), headers=headers)
    assert r.status_code == 200, r.text
    return code


def test_current_project_hides_change_question(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    d = client.get("/api/v1/projects/current", headers=h).json()
    assert d["project"]["title"] == "Terminal tic-tac-toe"
    assert "change" not in str(d["project"]).lower() and "undo" not in str(d)
    assert d["entry"] is None and d["max_registration_loc"] == 50


def test_registration_and_verification(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    r = client.post(url(project, "register"), json={"repo_url": "https://gitlab.com/noor/ttt"}, headers=h)
    assert r.status_code == 422 and r.json()["error"] == "invalid_repo_url"
    r = client.post(url(project, "register"), json={"repo_url": REPO}, headers=h)
    assert r.status_code == 400 and r.json()["error"] == "repo_unavailable"  # doesn't exist yet

    repos.push(REPO, {"README.md": "hi"})
    r = client.post(url(project, "register"), json={"repo_url": "github.com/Noor/TTT.git"}, headers=h)
    assert r.status_code == 200
    entry = r.json()["entry"]
    assert entry["status"] == "pending" and entry["repo_url"] == REPO
    code = entry["verify_code"]

    r = client.post(url(project, "register/verify"), headers=h)
    assert r.json()["error"] == "code_mismatch" and "no .gcboard" in r.json()["message"]
    repos.push(REPO, {".gcboard": "wrong", "README.md": "hi"})
    assert client.post(url(project, "register/verify"), headers=h).json()["error"] == "code_mismatch"

    # More than 50 source lines: "start from an empty repo" (FR-PROJ-5).
    repos.push(REPO, {".gcboard": code, "main.py": code_lines(51)})
    r = client.post(url(project, "register/verify"), headers=h)
    assert r.status_code == 400 and r.json()["error"] == "repo_not_empty"
    assert "Start from an empty repo" in r.json()["message"]

    sha = repos.push(REPO, {".gcboard": f"  {code}\n", "main.py": code_lines(50)})
    r = client.post(url(project, "register/verify"), headers=h)
    assert r.status_code == 200
    e = r.json()["entry"]
    assert e["status"] == "registered" and e["verify_code"] is None
    assert [(s["kind"], s["head_sha"], s["source_loc"]) for s in e["snapshots"]] == [("registration", sha, 50)]
    # Verified: can't switch repos any more.
    r = client.post(url(project, "register"), json={"repo_url": REPO}, headers=h)
    assert r.json()["error"] == "already_registered"


def test_repo_cannot_be_shared_or_squatted(client, cf, repos, clock, project):
    owner = login(client, cf, clock, "noor")
    squatter = login(client, cf, clock, "zaid99")
    repos.push(REPO, {"README.md": "hi"})
    # An unverified registration doesn't lock the repo away from its owner...
    assert client.post(url(project, "register"), json={"repo_url": REPO}, headers=squatter).status_code == 200
    register_and_verify(client, repos, owner, project)
    # ...and once the owner is verified, nobody else can use it.
    r = client.post(url(project, "register/verify"), headers=squatter)
    assert r.status_code == 409 and r.json()["error"] == "repo_taken"
    r = client.post(url(project, "register"), json={"repo_url": REPO}, headers=squatter)
    assert r.json()["error"] == "repo_taken"


def test_snapshots_history_and_submit(app, client, cf, repos, clock, project):
    svc = app.state.projects
    h = login(client, cf, clock, "noor")
    clock.t -= 3 * 3600  # Wednesday 09:00
    code = register_and_verify(client, repos, h, project, lines=0)

    def snap() -> int:
        return asyncio.run(svc.daily_snapshots())

    assert snap() == 0  # before 12:00
    clock.t += 3 * 3600  # 12:00
    repos.push(REPO, {".gcboard": code, "main.py": code_lines(100)})
    assert snap() == 1
    assert snap() == 0  # once a day
    before = repos.downloads
    clock.t += DAY  # Thursday, no new commit: no download needed
    assert snap() == 1 and repos.downloads == before

    clock.t += DAY  # Friday: repo went private
    repos.repos[REPO]["private"] = True
    assert snap() == 0
    entry = client.get("/api/v1/projects/current", headers=h).json()["entry"]
    assert entry["status"] == "unreachable"
    r = client.post(url(project, "submit"), headers=h)
    assert r.status_code == 400 and r.json()["error"] == "repo_unavailable"
    clock.t += 600
    repos.repos[REPO]["private"] = False
    repos.push(REPO, {".gcboard": code, "main.py": code_lines(200)})
    assert snap() == 0  # retry waits 30 minutes after a failure
    clock.t += 1800
    assert snap() == 1
    assert client.get("/api/v1/projects/current", headers=h).json()["entry"]["status"] == "registered"

    clock.t += DAY  # Saturday
    sha = repos.push(REPO, {".gcboard": code, "main.py": code_lines(300), "tests/test_x.py": code_lines(999)})
    assert snap() == 1
    proj = client.get("/api/v1/projects/current", headers=h).json()
    # registration 0 -> 100 -> 100 (same) -> 200 -> 300: three active intervals, no dump.
    assert proj["entry"]["history_factor"] == 1.0

    clock.t += 3600
    r = client.post(url(project, "submit"), headers=h)
    assert r.status_code == 200, r.text
    e = r.json()["entry"]
    assert e["status"] == "submitted" and e["frozen_sha"] == sha and e["submitted_at"] == int(clock())
    assert e["snapshots"][-1]["kind"] == "submission" and e["snapshots"][-1]["source_loc"] == 300
    assert e["history_factor"] == 1.0
    assert client.post(url(project, "submit"), headers=h).json()["error"] == "already_submitted"

    # Later pushes or deletion don't matter (FR-PROJ-9) and snapshots stop.
    clock.t += DAY
    del repos.repos[REPO]
    assert snap() == 0
    p = client.get("/api/v1/users/noor", headers=h).json()
    assert p["projects"] == [
        {"title": "Terminal tic-tac-toe", "submit_rank": None, "writeup_score": None,
         "history_factor": 1.0, "points": 0, "provisional": True}
    ]


def test_dump_on_submission_day(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    register_and_verify(client, repos, h, project, lines=0)
    repos.push(REPO, {".gcboard": "x", "main.py": code_lines(500)})
    r = client.post(url(project, "submit"), headers=h)
    assert r.json()["entry"]["history_factor"] == 0.5  # SRD 5.4: all at once scores 0.5


def test_submit_requires_source_files_and_open_project(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    assert client.post(url(project, "submit"), headers=h).json()["error"] == "not_verified"
    register_and_verify(client, repos, h, project, lines=0)
    repos.push(REPO, {".gcboard": "x", "README.md": "only docs"})
    r = client.post(url(project, "submit"), headers=h)
    assert r.status_code == 400 and r.json()["error"] == "no_source_files"
    clock.t += 15 * DAY  # closed
    r = client.post(url(project, "submit"), headers=h)
    assert r.status_code == 409 and r.json()["error"] == "project_closed"
    assert client.get("/api/v1/projects/current", headers=h).json()["project"] is None


def test_admin_project_create_and_overlap(tmp_path, config, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"[server]\ndatabase = '{config.server.database}'\n")
    (tmp_path / "spec.md").write_text("# Spec\n", encoding="utf-8")
    proj = tmp_path / "p.toml"
    proj.write_text(
        'title = "TTT"\nspec_file = "spec.md"\nopens = "2026-11-16 00:00"\n'
        'change_question = "How would you add undo?"\nchange_keyword_groups = [["Undo"], ["stack", "history"]]\n',
        encoding="utf-8",
    )
    admin.main(["--config", str(cfg), "project-create", str(proj)])
    assert "2026-11-16 00:00 -> 2026-11-30 00:00" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="overlaps project 1"):
        admin.main(["--config", str(cfg), "project-create", str(proj)])
    proj.write_text(proj.read_text().replace("2026-11-16", "2026-11-30"), encoding="utf-8")
    admin.main(["--config", str(cfg), "project-create", str(proj)])  # back to back is fine
    admin.main(["--config", str(cfg), "projects"])
    out = capsys.readouterr().out
    assert out.count("upcoming") == 2
    from sqlmodel import select

    from gcboard.server.db import make_engine

    with Session(make_engine(config.server.database)) as s:
        assert s.exec(select(Project)).first().change_keyword_groups == [["undo"], ["stack", "history"]]
