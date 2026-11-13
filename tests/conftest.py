from __future__ import annotations

import random

import pytest

from gcboard.server.codeforces import CodeforcesError, CodeforcesNotFound
from gcboard.server.config import Config


class FakeClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeCF:
    """In-memory stand-in for the Codeforces API."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.subs: dict[str, list[dict]] = {}  # handle -> submissions, newest first
        self.problems: list[dict] = [
            {"contestId": 1000 + i, "index": "A", "name": f"Problem {i}", "rating": r, "tags": []}
            for i, r in enumerate([800, 800, 900, 1000, 1000, 1100, 1200, 1300, 1400, 1500])
        ]
        self.down = False
        self._next_id = 1

    def add_user(self, handle: str, rating: int = 1200) -> None:
        self.users[handle.lower()] = {"handle": handle, "rating": rating, "rank": "pupil"}
        self.subs.setdefault(handle.lower(), [])

    def submit(self, handle: str, contest_id: int, index: str, at: int, verdict: str | None, ms: int = 100) -> None:
        s = {
            "id": self._next_id,
            "contestId": contest_id,
            "creationTimeSeconds": at,
            "problem": {"contestId": contest_id, "index": index},
            "timeConsumedMillis": ms,
        }
        if verdict is not None:
            s["verdict"] = verdict
        self._next_id += 1
        self.subs[handle.lower()].insert(0, s)

    def _check(self) -> None:
        if self.down:
            raise CodeforcesError("down")

    async def problemset_problems(self):
        self._check()
        return self.problems

    async def user_status(self, handle, from_=1, count=None):
        self._check()
        if handle.lower() not in self.subs:
            raise CodeforcesNotFound(f"handle: User with handle {handle} not found")
        subs = self.subs[handle.lower()][from_ - 1 :]
        return subs[:count] if count else subs

    async def user_info(self, handles):
        self._check()
        out = []
        for h in handles:
            if h.lower() not in self.users:
                raise CodeforcesNotFound(f"handles: User with handle {h} not found")
            out.append(self.users[h.lower()])
        return out


@pytest.fixture
def cf() -> FakeCF:
    return FakeCF()


@pytest.fixture
def clock() -> FakeClock:
    # Wednesday 2026-11-11 12:00:00 UTC
    return FakeClock(1794398400.0)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config.model_validate(
        {"server": {"database": str(tmp_path / "test.db"), "admins": ["Boss"], "codeforces_min_interval": 0}}
    )


@pytest.fixture
def rng() -> random.Random:
    return random.Random(42)
