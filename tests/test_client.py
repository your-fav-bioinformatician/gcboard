"""Drive the Textual client against a real server (fake Codeforces) in a thread."""

import asyncio
import os
import socket
import stat
import threading
import time

import pytest
import uvicorn
from textual.widgets import DataTable, Static

from gcboard.server.main import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(config, cf, repos, clock, rng, tmp_path, monkeypatch):
    app = create_app(config, cf=cf, repos=repos, clock=clock, run_scheduler=False)
    app.state.jobs.rng = rng
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    monkeypatch.setenv("GCBOARD_SERVER", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    yield app
    srv.should_exit = True
    thread.join(5)


def text(app, selector: str) -> str:
    return str(app.screen.query_one(selector, Static).render())


async def settle(pilot) -> None:
    for _ in range(3):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.05)


def test_login_today_and_board(server, cf, clock, tmp_path):
    from gcboard.client.app import GcboardApp

    async def scenario():
        cf.add_user("noor")
        app = GcboardApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot)
            assert app.current_mode == "login"
            await pilot.press(*"noor", "enter")
            await settle(pilot)
            assert "Prove you own noor" in text(app, "#challenge")
            ch = app.screen.challenge
            cf.submit("noor", ch.contest_id, ch.index, int(clock()) + 5, "COMPILATION_ERROR")
            await pilot.press("v")
            await settle(pilot)
            assert app.current_mode == "board" and app.handle == "noor"

            # Token saved with owner-only permissions.
            token_file = tmp_path / "xdg" / "gcboard" / "token"
            if os.name == "posix":
                assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

            task = await server.state.jobs.post_daily_task()
            await pilot.press("t")
            await settle(pilot)
            assert "hidden" in text(app, "#today")
            assert str(task.contest_id) not in text(app, "#today")

            await pilot.press("s")
            await settle(pilot)
            await pilot.press("y")
            await settle(pilot)
            shown = text(app, "#today")
            assert f"{task.contest_id}{task.problem_index}" in shown and "elapsed" in shown

            cf.submit("noor", task.contest_id, task.problem_index, int(clock()) + 300, "OK", ms=31)
            clock.advance(400)
            await server.state.jobs.verify_attempts()
            await pilot.press("r")
            await settle(pilot)
            assert "solved" in text(app, "#today") and "5:00" in text(app, "#today")

            await pilot.press("l")
            await settle(pilot)
            table = app.screen.query_one(DataTable)
            assert table.get_row_at(0) == ["1", "noor", "~110", "1", "1d"]
            app.save_screenshot(str(tmp_path / "board.svg"))

            # Enter on a leaderboard row opens that member's profile; escape goes back.
            from gcboard.client.screens.profile import ProfileScreen

            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, ProfileScreen) and len(app.screen_stack) == 2
            prof = text(app, "#profile")
            assert "1 this week · 1 all time" in prof and "fastest 5:00" in prof
            await pilot.press("escape")
            await settle(pilot)
            assert len(app.screen_stack) == 1 and app.current_mode == "board"

            # m opens my own profile; l returns to the board from a pushed profile.
            await pilot.press("m")
            await settle(pilot)
            assert app.current_mode == "me" and "profile noor" in text(app, "#profile-title")
            await pilot.press("l", "enter")
            await settle(pilot)
            await pilot.press("l")
            await settle(pilot)
            assert app.current_mode == "board" and len(app.screen_stack) == 1

            await pilot.press("question_mark")
            await settle(pilot)
            assert "max(30, 100 - 10" in text(app, "#rules")

    asyncio.run(scenario())


def test_small_terminal_notice(server):
    from gcboard.client.app import GcboardApp
    from gcboard.client.screens.common import TooSmallScreen

    async def scenario():
        app = GcboardApp()
        async with app.run_test(size=(70, 20)) as pilot:
            await settle(pilot)
            assert isinstance(app.screen, TooSmallScreen)
            await pilot.resize_terminal(90, 30)
            await settle(pilot)
            assert not isinstance(app.screen, TooSmallScreen)

    asyncio.run(scenario())


def test_project_register_verify_submit(server, cf, repos, clock, tmp_path):
    from sqlmodel import Session

    from gcboard.client.app import GcboardApp
    from gcboard.client.screens.common import ConfirmScreen
    from gcboard.server.db import Project

    with Session(server.state.engine) as s:
        s.add(Project(title="Terminal tic-tac-toe", spec_md="# Tic-tac-toe\n\nBuild **it**.",
                      opens_at=int(clock()) - 3600, closes_at=int(clock()) + 14 * 86400,
                      change_question="How would you add undo?", change_keyword_groups=[["undo"]]))
        s.commit()
    repo = "https://github.com/noor/ttt"
    repos.push(repo, {"README.md": "new"})

    async def scenario():
        cf.add_user("noor")
        app = GcboardApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot)
            await pilot.press(*"noor", "enter")
            await settle(pilot)
            ch = app.screen.challenge
            cf.submit("noor", ch.contest_id, ch.index, int(clock()) + 5, "COMPILATION_ERROR")
            await pilot.press("v")
            await settle(pilot)

            await pilot.press("p")
            await settle(pilot)
            assert app.current_mode == "project"
            assert "project Terminal tic-tac-toe" in text(app, "#project-title")
            assert "not registered" in text(app, "#project-info")
            assert "undo" not in text(app, "#project-info")

            await pilot.press("e")
            await settle(pilot)
            await pilot.press(*"github.com/noor/ttt", "enter")
            await settle(pilot)
            info = text(app, "#project-info")
            assert "waiting for verification" in info
            code = next(w for w in info.split() if w.startswith("gcboard-"))

            repos.push(repo, {".gcboard": code, "game.py": "print(1)\n"})
            await pilot.press("v")
            await settle(pilot)
            assert "verified" in text(app, "#project-info") and "registration" in text(app, "#project-info")

            await pilot.press("s")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await settle(pilot)
            info = text(app, "#project-info")
            assert "submitted" in info and "history" in info
            app.save_screenshot(str(tmp_path / "project.svg"))

    asyncio.run(scenario())
