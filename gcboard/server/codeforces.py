"""Codeforces API wrapper, daily problem picker, and attempt verifier.

All calls go through one shared queue that waits `min_interval` seconds
between requests (Codeforces allows about 1 request per 2 seconds). The
picker and verifier are pure functions so they can be unit-tested.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol

import httpx

from gcboard import __version__

log = logging.getLogger(__name__)

API_BASE = "https://codeforces.com/api/"

Problem = dict[str, Any]
Submission = dict[str, Any]
ProblemKey = tuple[int, str]


class CodeforcesError(Exception):
    """Codeforces is unreachable or returned an error."""


class CodeforcesNotFound(CodeforcesError):
    """The requested handle (or other object) does not exist."""


def problem_url(contest_id: int, index: str) -> str:
    return f"https://codeforces.com/problemset/problem/{contest_id}/{index}"


def problem_key(p: Problem) -> ProblemKey:
    return (int(p["contestId"]), str(p["index"]))


def submission_key(s: Submission) -> ProblemKey | None:
    p = s.get("problem", {})
    contest_id = p.get("contestId", s.get("contestId"))
    if contest_id is None or "index" not in p:
        return None
    return (int(contest_id), str(p["index"]))


class CodeforcesAPI(Protocol):
    """What the rest of the server needs from Codeforces (a fake in tests)."""

    async def problemset_problems(self) -> list[Problem]: ...
    async def user_status(self, handle: str, from_: int = 1, count: int | None = None) -> list[Submission]: ...
    async def user_info(self, handles: list[str]) -> list[dict[str, Any]]: ...


class CodeforcesClient:
    def __init__(
        self,
        min_interval: float = 2.0,
        retries: int = 3,
        problems_ttl: float = 3600,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.retries = retries
        self.problems_ttl = problems_ttl
        self._http = http or httpx.AsyncClient(
            timeout=30, headers={"User-Agent": f"gcboard/{__version__}"}
        )
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._problems: list[Problem] | None = None
        self._problems_at = 0.0

    async def call(self, method: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        error: CodeforcesError = CodeforcesError("no attempts made")
        for attempt in range(self.retries):
            if attempt:
                await asyncio.sleep(2**attempt)
            async with self._lock:
                wait = self._last_call + self.min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    resp = await self._http.get(API_BASE + method, params=params)
                except httpx.HTTPError as exc:
                    error = CodeforcesError(f"{method}: {exc!r}")
                    continue
                finally:
                    self._last_call = time.monotonic()
            try:
                data = resp.json()
            except ValueError:
                error = CodeforcesError(f"{method}: HTTP {resp.status_code}, not JSON")
                continue
            if data.get("status") == "OK":
                return data["result"]
            comment = str(data.get("comment", f"HTTP {resp.status_code}"))
            if "not found" in comment.lower():
                raise CodeforcesNotFound(comment)
            error = CodeforcesError(f"{method}: {comment}")
        log.warning("Codeforces call failed: %s", error)
        raise error

    async def problemset_problems(self) -> list[Problem]:
        if self._problems is None or time.monotonic() - self._problems_at > self.problems_ttl:
            result = await self.call("problemset.problems")
            self._problems = result["problems"]
            self._problems_at = time.monotonic()
        return self._problems

    async def user_status(self, handle: str, from_: int = 1, count: int | None = None) -> list[Submission]:
        return await self.call("user.status", handle=handle, **{"from": from_}, count=count)

    async def user_info(self, handles: list[str]) -> list[dict[str, Any]]:
        return await self.call("user.info", handles=";".join(handles))


async def user_status_since(cf: CodeforcesAPI, handle: str, since: int, page: int = 50) -> list[Submission]:
    """Submissions newest-first, paging back until one predates `since`."""
    out: list[Submission] = []
    start = 1
    while True:
        batch = await cf.user_status(handle, start, page)
        out.extend(batch)
        if len(batch) < page or batch[-1]["creationTimeSeconds"] < since:
            return out
        start += page


# --------------------------------------------------------------------------
# Picker (FR-TASK-3, FR-TASK-4)
# --------------------------------------------------------------------------


def pick_problem(
    problems: Iterable[Problem],
    target_rating: int,
    fallback_steps: Iterable[int],
    excluded_tags: Iterable[str],
    excluded_keys: set[ProblemKey],
    rng: random.Random,
) -> Problem | None:
    """Random problem at exactly the target rating, else ±step for each
    fallback step in turn. Returns None if nothing qualifies."""
    bad_tags = set(excluded_tags)
    eligible = [
        p
        for p in problems
        if "rating" in p
        and "contestId" in p
        and not bad_tags.intersection(p.get("tags", []))
        and problem_key(p) not in excluded_keys
    ]
    for step in [0, *fallback_steps]:
        ratings = {target_rating - step, target_rating + step}
        candidates = sorted((p for p in eligible if p["rating"] in ratings), key=problem_key)
        if candidates:
            return rng.choice(candidates)
    return None


# --------------------------------------------------------------------------
# Verifier (FR-TASK-7, FR-TASK-8)
# --------------------------------------------------------------------------

PENDING: Literal["pending"] = "pending"


@dataclass(frozen=True)
class AttemptResult:
    submission_id: int
    accepted_at: int
    runtime_ms: int
    rejected_count: int


def evaluate_attempt(
    submissions: Iterable[Submission],
    contest_id: int,
    index: str,
    started_at: int,
    closes_at: int,
) -> AttemptResult | Literal["pending"] | None:
    """First AC on exactly this problem in [started_at, closes_at).

    Returns PENDING if a submission before the first AC is still being judged
    (it could turn out to be the first AC), or None if there is no AC yet.
    Rejected submissions are counted excluding COMPILATION_ERROR.
    """
    key = (contest_id, index)
    relevant = sorted(
        (
            s
            for s in submissions
            if submission_key(s) == key and started_at <= s["creationTimeSeconds"] < closes_at
        ),
        key=lambda s: (s["creationTimeSeconds"], s["id"]),
    )
    rejected = 0
    for s in relevant:
        verdict = s.get("verdict")
        if verdict in (None, "TESTING"):
            return PENDING
        if verdict == "OK":
            return AttemptResult(
                submission_id=int(s["id"]),
                accepted_at=int(s["creationTimeSeconds"]),
                runtime_ms=int(s.get("timeConsumedMillis", 0)),
                rejected_count=rejected,
            )
        if verdict != "COMPILATION_ERROR":
            rejected += 1
    return None
