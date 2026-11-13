"""M4: writeup sessions, rate-checked saves, grading, and project points."""

import asyncio

from sqlmodel import Session

from gcboard.server import admin
from gcboard.server.db import Project

from .sample_writeup import ANSWERS, FILES, VAGUE
from .test_api import login
from .test_projects import DAY, REPO, app, client, project, register_and_verify, url  # noqa: F401

DRAFT = "/api/v1/writeups/current/draft"
SUBMIT = "/api/v1/writeups/current/submit"


def submit_project(client, repos, h, project, repo=REPO) -> dict:
    code = register_and_verify(client, repos, h, project, repo=repo)
    repos.push(repo, {".gcboard": code, **FILES})
    r = client.post(url(project, "submit"), headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def finish(client, clock, h, answers) -> dict:
    """Type at a human pace (the server's clock moves on), then submit."""
    clock.advance(200)
    r = client.post(SUBMIT, json={"answers": answers}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def entry(client, h) -> dict:
    return client.get("/api/v1/projects/current", headers=h).json()["entry"]


def test_submit_starts_locked_writeup(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    ws = submit_project(client, repos, h, project)
    snip = ws["snippet"]
    assert ws["questions"][0] == (
        f"This is {snip['path']}, lines {snip['start']}-{snip['end']} of your submission. Explain what it does, "
        "why you wrote it this way, and how it connects to the rest of the project."
    )
    assert ws["questions"][2] == "How would you add undo?"  # the project's change question, revealed now
    assert snip["text"] == "\n".join(FILES[snip["path"]].splitlines()[snip["start"] - 1 : snip["end"]])
    assert ws["deadline"] - ws["started_at"] == 45 * 60 and ws["started_at"] == int(clock())
    assert ws["answers"] == ["", "", "", ""] and ws["min_words"] == [80, 100, 60, 60]
    # The client locks onto it from /me, e.g. after a crash (FR-WRITE-7).
    assert client.get("/api/v1/me", headers=h).json()["writeup"]["deadline"] == ws["deadline"]
    assert client.get("/api/v1/writeups/current", headers=h).json()["snippet"] == snip


def test_rate_checked_saves(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    submit_project(client, repos, h, project)
    clock.advance(10)
    ok = ["a" * 250, "", "", ""]  # 25/s x 10 s + 50 = 300 allowed
    r = client.put(DRAFT, json={"answers": ok}, headers=h)
    assert r.status_code == 200 and r.json()["answers"] == ok
    # A paste arriving as fast keystrokes: rejected, and the last accepted text comes back.
    r = client.put(DRAFT, json={"answers": ["a" * 250, "b" * 100, "", ""]}, headers=h)
    assert r.status_code == 409 and r.json()["error"] == "typing_too_fast"
    assert r.json()["answers"] == ok
    # Deleting is always fine; so is the 50-character allowance.
    assert client.put(DRAFT, json={"answers": ["a" * 10, "", "", ""]}, headers=h).status_code == 200
    assert client.put(DRAFT, json={"answers": ["a" * 10, "b" * 50, "", ""]}, headers=h).status_code == 200
    clock.advance(500)
    r = client.put(DRAFT, json={"answers": ["a" * 10001, "", "", ""]}, headers=h)
    assert r.status_code == 422 and r.json()["error"] == "answer_too_long"
    assert client.put(DRAFT, json={"answers": ["", ""]}, headers=h).status_code == 422


def test_early_submit_and_score(client, cf, repos, clock, project):
    h = login(client, cf, clock, "noor")
    submit_project(client, repos, h, project)
    clock.advance(100)
    r = client.post(SUBMIT, json={"answers": [ANSWERS[0], "Too short.", "", ""]}, headers=h)
    assert r.status_code == 400 and r.json()["error"] == "below_minimum"
    assert "Q2 2/100" in r.json()["message"] and "Q1" not in r.json()["message"]
    assert client.get("/api/v1/writeups/current", headers=h).json()["answers"][0] == ANSWERS[0]  # save kept

    result = finish(client, clock, h, ANSWERS)
    assert result["auto_submitted"] is False and result["passed"] is True
    assert result["breakdown"]["grounding"] > 0 and 0 < result["score"] <= 100
    assert client.get("/api/v1/me", headers=h).json()["writeup"] is None
    assert client.get("/api/v1/writeups/current", headers=h).status_code == 404
    assert client.put(DRAFT, json={"answers": ANSWERS}, headers=h).status_code == 404

    e = entry(client, h)
    assert e["writeup"]["score"] == result["score"]
    # Everything pushed at once -> history 0.5; first qualified -> 300 speed points.
    assert e["history_factor"] == 0.5 and e["submit_rank"] == 1
    assert e["points"] == round(300 * result["score"] / 100 * 0.5) and e["provisional"] is True

    profile = client.get("/api/v1/users/noor", headers=login(client, cf, clock, "viewer")).json()
    assert profile["projects"][0]["writeup_score"] == result["score"]
    assert "minimax" not in str(profile)  # answers are never shown to others (FR-PROF-3)


def test_deadline_auto_submits_last_saved_text(app, client, cf, repos, clock, project):
    h1 = login(client, cf, clock, "noor")
    h2 = login(client, cf, clock, "zaid99")
    submit_project(client, repos, h1, project)
    submit_project(client, repos, h2, project, repo="https://github.com/zaid/ttt")
    clock.advance(200)
    client.put(DRAFT, json={"answers": [ANSWERS[0], "", "", ""]}, headers=h1)
    clock.advance(45 * 60)
    # A save after the deadline ends the session with the last saved text.
    r = client.put(DRAFT, json={"answers": ANSWERS}, headers=h1)
    assert r.status_code == 409 and r.json()["error"] == "deadline_passed"
    w = entry(client, h1)["writeup"]
    assert w["auto_submitted"] and w["score"] == 0 and not w["passed"]  # empty answers fail the gate
    assert w["submitted_at"] == entry(client, h1)["submitted_at"] + 45 * 60
    # The other member never came back: the sweep job submits theirs.
    assert asyncio.run(app.state.writeups.sweep()) == 1
    assert entry(client, h2)["writeup"]["auto_submitted"] is True


def test_finalize_and_leaderboard(app, client, cf, repos, clock, project):
    ws = app.state.writeups
    weak = login(client, cf, clock, "weak")
    good = login(client, cf, clock, "good")
    late = login(client, cf, clock, "late")
    submit_project(client, repos, weak, project, repo="https://github.com/weak/ttt")
    assert finish(client, clock, weak, VAGUE)["passed"] is False
    clock.advance(600)
    submit_project(client, repos, good, project, repo="https://github.com/good/ttt")
    score = finish(client, clock, good, ANSWERS)["score"]
    # weak submitted first but didn't qualify, so it doesn't push good down (5.2).
    assert entry(client, weak)["points"] == 0 and entry(client, weak)["submit_rank"] is None
    assert entry(client, good)["submit_rank"] == 1

    with Session(app.state.engine) as s:
        closes_at = s.get(Project, project).closes_at
    clock.t = closes_at - 60
    submit_project(client, repos, late, project, repo="https://github.com/late/ttt")  # writeup runs past close
    clock.t = closes_at + 60
    assert asyncio.run(ws.finalize_projects()) == 0  # waits for the open writeup
    board = client.get("/api/v1/leaderboard?period=week", headers=good).json()
    expected = round(300 * score / 100 * 0.5)
    assert [(r["cf_handle"], r["points"], r["provisional"]) for r in board["rows"]] == [("good", expected, True)]

    clock.t = closes_at + 45 * 60
    assert asyncio.run(ws.sweep()) == 1
    assert asyncio.run(ws.finalize_projects()) == 1
    board = client.get("/api/v1/leaderboard?period=week", headers=good).json()
    assert [(r["cf_handle"], r["points"], r["provisional"], r["solved"]) for r in board["rows"]] == [
        ("good", expected, False, 0)
    ]
    assert entry(client, good)["provisional"] is False  # the closed project is still shown, with results
    clock.t += 8 * DAY  # next week: the project points stay in that earlier week
    assert client.get("/api/v1/leaderboard?period=week", headers=good).json()["rows"] == []
    assert client.get("/api/v1/leaderboard?period=all", headers=good).json()["rows"][0]["points"] == expected
    assert client.get("/api/v1/users/good", headers=good).json()["weekly_wins"] == 1


def test_recompute_is_stable_and_admin_can_read(app, client, cf, repos, clock, project, tmp_path, config, capsys):
    h = login(client, cf, clock, "noor")
    submit_project(client, repos, h, project)
    before = finish(client, clock, h, ANSWERS)
    cfg = tmp_path / "admin.toml"
    cfg.write_text(f"[server]\ndatabase = '{config.server.database}'\n")
    admin.main(["--config", str(cfg), "recompute"])
    after = entry(client, h)
    assert after["writeup"]["score"] == before["score"] and after["writeup"]["breakdown"] == before["breakdown"]
    admin.main(["--config", str(cfg), "writeup", "noor", str(project)])
    out = capsys.readouterr().out
    assert "minimax" in out and f"score {before['score']}" in out
