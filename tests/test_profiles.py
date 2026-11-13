"""M2: profiles, weekly wins, and rating sync."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from gcboard.server.codeforces import CodeforcesError
from gcboard.server.main import create_app

from .test_api import login

DAY = 24 * 3600


@pytest.fixture
def app(config, cf, clock, rng):
    app = create_app(config, cf=cf, clock=clock, run_scheduler=False)
    app.state.jobs.rng = rng
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def play_day(app, client, cf, clock, results: dict[str, int | None], headers: dict[str, dict]) -> None:
    """Post today's task; each member starts it at once and solves it in the
    given number of seconds (None = starts but never solves). Then close it."""
    jobs = app.state.jobs
    task = asyncio.run(jobs.post_daily_task())
    assert task is not None
    t0 = int(clock())
    for handle, secs in results.items():
        client.post("/api/v1/tasks/today/start", headers=headers[handle])
        if secs is not None:
            cf.submit(handle, task.contest_id, task.problem_index, t0 + secs, "OK")
    clock.t = task.closes_at
    asyncio.run(jobs.close_due_tasks())


def test_profile_stats_and_weekly_wins(app, client, cf, clock):
    h = {name: login(client, cf, clock, name) for name in ("ali_k", "noor", "zaid99")}
    for subs in cf.subs.values():
        subs.clear()
    clock.t -= 11 * 3600  # Wednesday 01:00, week 46

    play_day(app, client, cf, clock, {"ali_k": 600, "noor": 900, "zaid99": None}, h)  # Wed
    play_day(app, client, cf, clock, {"ali_k": 300, "noor": 200}, h)  # Thu
    # Fri-Sun: no tasks (e.g. Codeforces outage) - streaks are not broken.
    clock.t += 3 * DAY + 3600  # Monday 01:00, week 47
    play_day(app, client, cf, clock, {"noor": 120}, h)  # Mon

    ali = client.get("/api/v1/users/ALI_K", headers=h["noor"]).json()  # any member, any case
    assert ali["cf_handle"] == "ali_k"
    assert (ali["solved_all"], ali["solved_week"]) == (2, 0)
    assert (ali["avg_solve_seconds"], ali["fastest_solve_seconds"]) == (450, 300)
    assert (ali["current_streak"], ali["best_streak"]) == (0, 2)  # missed Monday
    # Week 46: ali 100+10 (Wed) + 90+10 (Thu; tie on runtime) = 210 vs noor 90+10 + 100+10 = 210.
    # Tied on points and solves -> lower total solve time wins: ali 900s vs noor 1100s.
    assert (ali["weekly_wins"], ali["best_weekly_finish"]) == (1, 1)
    assert ali["projects"] == []

    noor = client.get("/api/v1/users/noor", headers=h["noor"]).json()
    assert (noor["current_streak"], noor["best_streak"]) == (3, 3)
    assert (noor["solved_week"], noor["solved_all"]) == (1, 3)
    # Week 47 is still running, so it doesn't count as a finish yet.
    assert (noor["weekly_wins"], noor["best_weekly_finish"]) == (0, 2)

    zaid = client.get("/api/v1/users/zaid99", headers=h["noor"]).json()
    assert zaid["solved_all"] == 0 and zaid["avg_solve_seconds"] is None
    assert zaid["best_weekly_finish"] is None  # never scored


def test_profile_not_found_and_banned(app, client, cf, clock):
    h = login(client, cf, clock, "noor")
    login(client, cf, clock, "zaid99")
    r = client.get("/api/v1/users/ghost", headers=h)
    assert r.status_code == 404 and r.json()["error"] == "user_not_found"
    from sqlmodel import Session, select

    from gcboard.server.db import User

    with Session(app.state.engine) as s:
        u = s.exec(select(User).where(User.cf_handle == "zaid99")).one()
        u.banned = True
        s.add(u)
        s.commit()
    assert client.get("/api/v1/users/zaid99", headers=h).status_code == 404


def test_sync_ratings(app, client, cf, clock):
    jobs = app.state.jobs
    for name in ("ali_k", "noor", "renamed"):
        login(client, cf, clock, name)  # login stores the rating (1200)
    assert asyncio.run(jobs.sync_ratings()) == 0  # everyone synced just now

    cf.users["ali_k"].update(rating=1520, rank="specialist")
    cf.users["noor"].update(rating=1900, rank="candidate master")
    del cf.users["renamed"]  # handle changed on Codeforces: fails the batch call
    clock.advance(DAY + 1)
    assert asyncio.run(jobs.sync_ratings()) == 2
    p = client.get("/api/v1/users/noor", headers=login(client, cf, clock, "viewer")).json()
    assert (p["cf_rating"], p["cf_rank"], p["rating_synced_at"]) == (1900, "candidate master", int(clock()))
    assert asyncio.run(jobs.sync_ratings()) == 0  # not stale any more, including "renamed"

    # Codeforces down: the job raises (and is retried next hour), nothing changes.
    clock.advance(DAY + 1)
    cf.down = True
    with pytest.raises(CodeforcesError):
        asyncio.run(jobs.sync_ratings())
    cf.down = False
    assert asyncio.run(jobs.sync_ratings()) == 3
