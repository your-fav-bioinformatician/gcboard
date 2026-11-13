"""Local demo server for the manual terminal checklist (docs/terminal-checklist.md).

Runs the real gcboard server with fake Codeforces and GitHub and seeds:
- a member "demo" who is already logged in (token written for the client),
- three other members with solves this week (leaderboard, streaks),
- an open daily task that "demo" hasn't started,
- an open project where "demo" has a verified repo with real code,
  so submitting starts a real writeup session.

Usage (from the repository root):
    uv run --extra server python scripts/demo_server.py --data DIR [--port 8765]

Then, in the terminal under test:
    XDG_CONFIG_HOME=DIR/xdg GCBOARD_SERVER=http://127.0.0.1:8765 gcboard

Demo-only endpoint: POST /demo/ac records an accepted solution for "demo"
on today's task (use it after pressing s on the Today screen).
Nothing here talks to the real Codeforces or GitHub.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from gcboard.server.config import Config  # noqa: E402
from gcboard.server.db import (  # noqa: E402
    DailyTask,
    EntryStatus,
    Project,
    ProjectEntry,
    RepoSnapshot,
    TaskAttempt,
    Token,
    User,
)
from gcboard.server.main import create_app, hash_token  # noqa: E402
from tests.conftest import FakeCF, FakeRepos  # noqa: E402
from tests.sample_writeup import FILES, KEYWORD_GROUPS  # noqa: E402

DAY = 86400
TOKEN = "demo-token-for-terminal-checks"
DEMO_REPO = "https://github.com/demo/tictactoe"


def seed(app, cf: FakeCF, repos: FakeRepos, now: int) -> None:
    jobs = app.state.jobs
    with Session(app.state.engine) as s:
        users = {}
        for handle, rating, rank in [("demo", 1350, "pupil"), ("ali_k", 1610, "expert"),
                                     ("noor", 1480, "specialist"), ("zaid99", 1190, "pupil")]:
            cf.add_user(handle, rating)
            u = User(cf_handle=handle, cf_rating=rating, cf_rank=rank, joined_at=now - 30 * DAY,
                     rating_synced_at=now)
            s.add(u)
            s.flush()
            users[handle] = u.id
        s.add(Token(user_id=users["demo"], token_hash=hash_token(TOKEN), created_at=now))

        # Three finished days this week with solves, for points and streaks.
        problems = iter(cf.problems[5:])
        for back in (3, 2, 1):
            p = next(problems)
            t = DailyTask(task_date=time.strftime("%Y-%m-%d", time.gmtime(now - back * DAY)),
                          contest_id=p["contestId"], problem_index=p["index"], name=p["name"],
                          rating=p["rating"], posted_at=now - back * DAY - 3600,
                          closes_at=now - (back - 1) * DAY - 3600, finalized_at=now - (back - 1) * DAY)
            s.add(t)
            s.flush()
            for handle, secs, ms, rej in [("ali_k", 900 + 60 * back, 46, back % 2), ("noor", 1300, 31, 0),
                                          ("zaid99", 2100 + 100 * back, 46, 1), ("demo", 1700, 62, 0)]:
                if handle == "zaid99" and back == 2:
                    continue  # breaks zaid99's streak
                s.add(TaskAttempt(user_id=users[handle], task_id=t.id, started_at=t.posted_at + 60,
                                  accepted_at=t.posted_at + 60 + secs, submission_id=1, runtime_ms=ms,
                                  rejected_count=rej, solve_seconds=secs, provisional=False))
            s.flush()
            jobs.rescore_task(s, t)

        # An open project: demo's repo is verified and has steady history.
        project = Project(title="Terminal tic-tac-toe", spec_md=(ROOT / "examples" / "tictactoe.md").read_text(),
                          opens_at=now - 3 * DAY, closes_at=now + 11 * DAY,
                          change_question="How would you add an undo move to your game?",
                          change_keyword_groups=KEYWORD_GROUPS)
        s.add(project)
        s.flush()
        entry = ProjectEntry(project_id=project.id, user_id=users["demo"], repo_url=DEMO_REPO,
                             verify_code="gcboard-demo", registered_at=now - 3 * DAY,
                             verified_at=now - 3 * DAY, status=EntryStatus.REGISTERED)
        s.add(entry)
        s.flush()
        for back, loc in [(3, 0), (2, 40), (1, 90)]:
            sha = repos.push(DEMO_REPO, {".gcboard": "gcboard-demo", "board.py": FILES["board.py"][: loc * 30]})
            s.add(RepoSnapshot(entry_id=entry.id, taken_at=now - back * DAY,
                               kind="registration" if back == 3 else "daily", head_sha=sha, source_loc=loc))
        repos.push(DEMO_REPO, {".gcboard": "gcboard-demo", **FILES})  # the finished game
        s.commit()

    asyncio.run(jobs.post_daily_task())  # today's task, not started by demo
    with Session(app.state.engine) as s:
        today = s.exec(select(DailyTask).where(DailyTask.finalized_at == None)).first()  # noqa: E711
        for handle, secs, ms in [("ali_k", 1120, 46), ("noor", 1500, 31)]:
            uid = s.exec(select(User.id).where(User.cf_handle == handle)).one()
            s.add(TaskAttempt(user_id=uid, task_id=today.id, started_at=now - 3000, accepted_at=now - 3000 + secs,
                              submission_id=2, runtime_ms=ms, rejected_count=0, solve_seconds=secs))
        s.flush()
        jobs.rescore_task(s, today)
        s.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="folder for the demo database and client token (wiped)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    data = Path(args.data).resolve()
    shutil.rmtree(data, ignore_errors=True)
    (data / "xdg" / "gcboard").mkdir(parents=True)
    (data / "xdg" / "gcboard" / "token").write_text(TOKEN)

    config = Config.model_validate({"server": {"database": str(data / "demo.db"), "admins": ["demo"]}})
    cf, repos = FakeCF(), FakeRepos()
    app = create_app(config, cf=cf, repos=repos, run_scheduler=False)
    seed(app, cf, repos, int(time.time()))

    @app.post("/demo/ac")
    async def demo_ac() -> dict:
        """Record an accepted solution for "demo" on today's task."""
        with Session(app.state.engine) as s:
            task = s.exec(select(DailyTask).where(DailyTask.finalized_at == None)).first()  # noqa: E711
        cf.submit("demo", task.contest_id, task.problem_index, int(time.time()), "WRONG_ANSWER")
        cf.submit("demo", task.contest_id, task.problem_index, int(time.time()), "OK", ms=31)
        await app.state.jobs.verify_attempts()
        return {"ok": True}

    print(f"\nDemo server on http://127.0.0.1:{args.port}. In the terminal under test, run:\n")
    print(f"  XDG_CONFIG_HOME={data / 'xdg'} GCBOARD_SERVER=http://127.0.0.1:{args.port} gcboard\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
