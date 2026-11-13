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
            from gcboard.client.screens.writeup import WriteupScreen

            assert isinstance(app.screen, WriteupScreen)  # the locked writeup starts at once

    asyncio.run(scenario())


def test_writeup_lock_paste_relaunch_and_submit(server, cf, repos, clock, tmp_path):
    from sqlmodel import Session
    from textual import events
    from textual.widgets import TabbedContent, TextArea

    from gcboard.client.app import GcboardApp
    from gcboard.client.screens.common import ConfirmScreen
    from gcboard.client.screens.writeup import WriteupScreen
    from gcboard.server.db import Project

    from .sample_writeup import ANSWERS, FILES

    with Session(server.state.engine) as s:
        s.add(Project(title="TTT", spec_md="# TTT", opens_at=int(clock()) - 3600,
                      closes_at=int(clock()) + 14 * 86400, change_question="How would you add undo?",
                      change_keyword_groups=[["undo"], ["stack"]]))
        s.commit()
    repo = "https://github.com/noor/ttt"
    repos.push(repo, {"README.md": "new"})

    async def into_writeup(app, pilot):
        cf.add_user("noor")
        await settle(pilot)
        await pilot.press(*"noor", "enter")
        await settle(pilot)
        ch = app.screen.challenge
        cf.submit("noor", ch.contest_id, ch.index, int(clock()) + 5, "COMPILATION_ERROR")
        await pilot.press("v")
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)
        await pilot.press("e")
        await pilot.press(*"github.com/noor/ttt", "enter")
        await settle(pilot)
        code = next(w for w in str(app.screen.query_one("#project-info").render()).split() if w.startswith("gcboard-"))
        repos.push(repo, {".gcboard": code})
        await pilot.press("v")
        await settle(pilot)
        repos.push(repo, {".gcboard": code, **FILES})  # the finished project
        await pilot.press("s")
        await settle(pilot)
        await pilot.press("y")
        await settle(pilot)

    async def first_run():
        app = GcboardApp()
        async with app.run_test(size=(100, 32)) as pilot:
            await into_writeup(app, pilot)
            screen = app.screen
            assert isinstance(screen, WriteupScreen) and app.writeup is not None
            assert "undo" in str(screen.query_one("#q3 .question").render())

            # Locked: navigation and quitting do nothing (FR-WRITE-3).
            await pilot.press("l", "t", "question_mark", "ctrl+q", "q")
            await settle(pilot)
            assert app.screen is screen and app.return_code is None
            a1 = screen.query_one("#a1", TextArea)
            assert a1.text == "lt?q"  # with the editor focused, letters are just text
            a1.load_text("")

            await pilot.press(*"hello there")
            assert a1.text == "hello there"
            # Pasting is refused both ways (FR-WRITE-4).
            a1.post_message(events.Paste("pasted from somewhere"))
            await settle(pilot)
            app.copy_to_clipboard("from the clipboard")
            await pilot.press("ctrl+v")
            await settle(pilot)
            assert a1.text == "hello there"
            assert "Pasting is disabled" in text(app, "#status")

            from textual.containers import VerticalScroll

            snippet = screen.query_one("#snippet", VerticalScroll)
            await pilot.press("ctrl+down")
            await settle(pilot)
            assert snippet.scroll_y == 3 or snippet.max_scroll_y < 3  # code scrolls without leaving the answer
            assert a1.has_focus

            await pilot.press("tab")
            assert screen.query_one(TabbedContent).active == "q2" and screen.query_one("#a2", TextArea).has_focus
            await pilot.press("shift+tab")
            assert screen.query_one(TabbedContent).active == "q1"

            clock.advance(10)
            screen.save()
            await settle(pilot)
            # Text that grows faster than typing is rolled back to the last save (FR-WRITE-6).
            a1.load_text("hello there " + "x" * 2000)
            screen.save()
            await settle(pilot)
            assert a1.text == "hello there"
            assert "faster than anyone can type" in text(app, "#status")

    async def relaunch_and_submit():
        app = GcboardApp()  # e.g. after a crash: straight back into the writeup (FR-WRITE-7)
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            screen = app.screen
            assert isinstance(screen, WriteupScreen)
            assert screen.query_one("#a1", TextArea).text == "hello there"
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert "Not enough words yet" in text(app, "#status")
            for i, answer in enumerate(ANSWERS):
                screen.query_one(f"#a{i + 1}", TextArea).load_text(answer)
            clock.advance(300)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await settle(pilot)
            assert app.writeup is None and app.current_mode == "project"
            info = text(app, "#project-info")
            assert "qualified" in info and "grounding" in info and "submission rank #1" in info
            app.save_screenshot(str(tmp_path / "result.svg"))

    asyncio.run(first_run())
    asyncio.run(relaunch_and_submit())
