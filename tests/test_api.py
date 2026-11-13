"""End-to-end tests of the API and jobs against a fake Codeforces."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from gcboard.server.main import create_app


@pytest.fixture
def app(config, cf, clock, rng):
    app = create_app(config, cf=cf, clock=clock, run_scheduler=False)
    app.state.jobs.rng = rng
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def run(coro):
    return asyncio.run(coro)


def login(client, cf, clock, handle) -> dict:
    cf.add_user(handle)
    r = client.post("/api/v1/auth/challenge", json={"cf_handle": handle.lower()})
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["cf_handle"] == handle  # canonical case from Codeforces
    cf.submit(handle, ch["contest_id"], ch["index"], int(clock()) + 5, "COMPILATION_ERROR")
    r = client.post("/api/v1/auth/verify", json={"cf_handle": handle})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_clock_fixture_is_a_wednesday(clock):
    assert datetime.fromtimestamp(clock(), timezone.utc).weekday() == 2


def test_auth_requires_compile_error_in_window(client, cf, clock):
    cf.add_user("noor")
    ch = client.post("/api/v1/auth/challenge", json={"cf_handle": "noor"}).json()
    r = client.post("/api/v1/auth/verify", json={"cf_handle": "noor"})
    assert r.status_code == 400 and r.json()["error"] == "challenge_not_met"
    # A compile error made before the challenge was issued does not count.
    cf.submit("noor", ch["contest_id"], ch["index"], ch["issued_at"] - 1, "COMPILATION_ERROR")
    assert client.post("/api/v1/auth/verify", json={"cf_handle": "noor"}).status_code == 400
    # Wrong verdict does not count either.
    cf.submit("noor", ch["contest_id"], ch["index"], ch["issued_at"] + 3, "WRONG_ANSWER")
    assert client.post("/api/v1/auth/verify", json={"cf_handle": "noor"}).status_code == 400


def test_unknown_handle_and_bad_input(client):
    r = client.post("/api/v1/auth/challenge", json={"cf_handle": "ghost"})
    assert r.status_code == 404 and r.json() == {
        "error": "handle_not_found",
        "message": "Codeforces has no user named 'ghost'.",
    }
    r = client.post("/api/v1/auth/challenge", json={"cf_handle": "a b"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"
    r = client.get("/api/v1/me")
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"


def test_admin_from_config(client, cf, clock):
    h = login(client, cf, clock, "Boss")
    assert client.get("/api/v1/me", headers=h).json()["is_admin"] is True


def test_daily_task_full_flow(app, client, cf, clock):
    jobs = app.state.jobs
    ali = login(client, cf, clock, "ali_k")
    noor = login(client, cf, clock, "noor")
    zaid = login(client, cf, clock, "zaid99")
    idle = login(client, cf, clock, "idle")
    # Forget the login compile errors (on random problems) so the pick is predictable.
    for subs in cf.subs.values():
        subs.clear()
    # Members' past submissions are excluded from the pick (FR-TASK-3).
    cf.submit("noor", 1003, "A", int(clock()) - 99999, "WRONG_ANSWER")

    assert client.get("/api/v1/tasks/today", headers=ali).json()["task"] is None
    task = run(jobs.post_daily_task())
    assert task.rating == 1000  # Wednesday
    assert (task.contest_id, task.problem_index) == (1004, "A")
    assert run(jobs.post_daily_task()) is None  # idempotent

    # Before starting, the problem is hidden (FR-TASK-5).
    today = client.get("/api/v1/tasks/today", headers=ali).json()
    assert today["task"]["rating"] == 1000 and today["task"]["problem"] is None
    assert "1004" not in str(today)

    t0 = int(clock())
    for h in (ali, noor, zaid):
        r = client.post("/api/v1/tasks/today/start", headers=h)
        assert r.status_code == 200
        assert r.json()["task"]["problem"]["url"].endswith("/1004/A")
        assert r.json()["task"]["attempt"]["started_at"] == t0
    clock.advance(30)
    # Starting again does not reset the timer (FR-TASK-6).
    again = client.post("/api/v1/tasks/today/start", headers=ali).json()
    assert again["task"]["attempt"]["started_at"] == t0

    # 5.6 worked example: solve times, runtimes, and rejections.
    cf.submit("ali_k", 1004, "A", t0 + 600, "WRONG_ANSWER")
    cf.submit("ali_k", 1004, "A", t0 + 1000, "COMPILATION_ERROR")
    cf.submit("ali_k", 1004, "A", t0 + 1120, "OK", ms=46)
    cf.submit("noor", 1004, "A", t0 + 1500, "OK", ms=31)
    cf.submit("zaid99", 1004, "A", t0 + 2400, "OK", ms=46)
    cf.submit("zaid99", 9999, "A", t0 + 100, "OK")  # other problem: ignored
    clock.advance(3000)
    run(jobs.verify_attempts())

    a = client.get("/api/v1/tasks/today", headers=ali).json()["task"]["attempt"]
    assert a == {
        "started_at": t0, "solved": True, "accepted_at": t0 + 1120, "solve_seconds": 1120,
        "runtime_ms": 46, "rejected_count": 1, "points": 101, "provisional": True,
    }
    board = client.get("/api/v1/leaderboard?period=week", headers=ali).json()
    assert board["label"].startswith("week 46")
    assert [(r["rank"], r["cf_handle"], r["points"], r["provisional"]) for r in board["rows"]] == [
        (1, "ali_k", 101, True), (2, "noor", 100, True), (3, "zaid99", 86, True),
    ]
    assert all(r["cf_handle"] != "idle" for r in board["rows"])  # appears once they score
    assert board["rows"][0]["streak"] == 1

    # Close: finalize, no longer provisional; the next day's task gets posted.
    clock.advance(24 * 3600)
    run(jobs.tick())
    board = client.get("/api/v1/leaderboard?period=all", headers=idle).json()
    assert [(r["points"], r["provisional"]) for r in board["rows"]] == [(101, False), (100, False), (86, False)]
    today = client.get("/api/v1/tasks/today", headers=ali).json()["task"]
    assert today["rating"] == 1100 and today["attempt"] is None  # Thursday
    # Today's open, unsolved task doesn't break yesterday's streak.
    assert client.get("/api/v1/leaderboard?period=all", headers=ali).json()["rows"][0]["streak"] == 1


def test_close_waits_for_codeforces(app, client, cf, clock):
    jobs = app.state.jobs
    h = login(client, cf, clock, "noor")
    task = run(jobs.post_daily_task())
    client.post("/api/v1/tasks/today/start", headers=h)
    cf.submit("noor", task.contest_id, task.problem_index, int(clock()) + 60, "OK")
    clock.t = task.closes_at + 10
    cf.down = True
    run(jobs.close_due_tasks())
    from sqlmodel import Session

    from gcboard.server.db import DailyTask

    with Session(app.state.engine) as s:
        assert s.get(DailyTask, task.id).finalized_at is None
    cf.down = False
    run(jobs.close_due_tasks())
    rows = client.get("/api/v1/leaderboard?period=all", headers=h).json()["rows"]
    assert rows[0]["points"] == 110 and rows[0]["provisional"] is False  # AC before close still counts


def test_post_retries_then_skips(app, cf, clock):
    jobs = app.state.jobs
    cf.down = True
    assert run(jobs.post_daily_task()) is None
    cf.down = False
    clock.advance(60)
    assert run(jobs.post_daily_task()) is None  # within the 5-minute retry gap
    clock.advance(300)
    assert run(jobs.post_daily_task()) is not None


def test_post_gives_up_after_retry_window(config, cf, clock, rng):
    config.general.daily_post_time = "06:00"
    app = create_app(config, cf=cf, clock=clock, run_scheduler=False)
    jobs = app.state.jobs
    cf.down = True
    run(jobs.post_daily_task())
    clock.advance(6 * 3600 + 1)
    cf.down = False
    assert run(jobs.post_daily_task()) is None


def test_rate_limit(app, client, cf, clock):
    h = login(client, cf, clock, "noor")
    app.state.limiter.per_minute = 3
    codes = [client.get("/api/v1/me", headers=h).status_code for _ in range(5)]
    assert codes[-1] == 429
