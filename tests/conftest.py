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


class FakeRepos:
    """In-memory stand-in for GitHub: url -> {head, private, commits{sha: {path: bytes}}}."""

    def __init__(self) -> None:
        self.repos: dict[str, dict] = {}
        self.down = False
        self.downloads = 0
        self._n = 0

    def push(self, url: str, files: dict[str, str]) -> str:
        """Commit a full new tree (path -> text) to the repo and return its SHA."""
        repo = self.repos.setdefault(url, {"private": False, "commits": {}, "head": None})
        self._n += 1
        sha = f"{self._n:040x}"
        repo["commits"][sha] = {p: t.encode() for p, t in files.items()}
        repo["head"] = sha
        return sha

    def _repo(self, ref):
        from gcboard.server.repos import RepoError, RepoUnavailable

        if self.down:
            raise RepoError("down")
        repo = self.repos.get(ref.url)
        if repo is None or repo["private"]:
            raise RepoUnavailable("repository not found, or it is private")
        if repo["head"] is None:
            raise RepoUnavailable("repository is empty")
        return repo

    async def head_sha(self, ref):
        return self._repo(ref)["head"]

    async def files_at(self, ref, sha):
        from gcboard.server.repos import VERIFY_FILE, RepoFiles, is_source_path

        self.downloads += 1
        tree = self._repo(ref)["commits"][sha]
        verify = tree.get(VERIFY_FILE)
        return RepoFiles(
            sha=sha,
            sources={p: b for p, b in tree.items() if is_source_path(p)},
            verify_file=verify.decode() if verify is not None else None,
        )


@pytest.fixture
def repos() -> FakeRepos:
    return FakeRepos()
