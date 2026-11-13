"""FastAPI app and routes (SRD section 7). The server is the authority for
every timestamp and score; the client only displays and collects input."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import random
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from gcboard import __version__
from gcboard.shared.schemas import (
    AttemptInfo,
    ChallengeResponse,
    HandleRequest,
    LeaderboardResponse,
    Me,
    ProblemInfo,
    Profile,
    ProjectResponse,
    RegisterRequest,
    RulesResponse,
    TimeResponse,
    TodayResponse,
    TodayTask,
    VerifyResponse,
)

from . import board
from .codeforces import CodeforcesAPI, CodeforcesClient, CodeforcesError, CodeforcesNotFound, problem_key, problem_url, submission_key
from .config import Config, load_config
from .errors import ApiError
from .db import AuthChallenge, DailyTask, TaskAttempt, Token, User, make_engine
from .jobs import Jobs, start_scheduler
from .projects import ProjectService
from .repos import GitHubClient, RepoHost

log = logging.getLogger("gcboard")

# A challenge may be confirmed for this long after it expires, as long as the
# compilation-error submission itself was made inside the 5-minute window.
VERIFY_GRACE_SECONDS = 600


class RateLimiter:
    """Sliding one-minute window per key."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float) -> None:
        q = self._hits[key]
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= self.per_minute:
            raise ApiError(429, "rate_limited", "Too many requests; slow down.")
        q.append(now)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def get_session(request: Request):
    with Session(request.app.state.engine) as s:
        yield s


SessionDep = Annotated[Session, Depends(get_session)]


def now_of(request: Request) -> float:
    return request.app.state.clock()


def current_user(
    request: Request, session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Log in first.")
    token_hash = hash_token(authorization.removeprefix("Bearer ").strip())
    request.app.state.limiter.check(token_hash, now_of(request))
    row = session.exec(
        select(Token, User)
        .join(User, User.id == Token.user_id)
        .where(Token.token_hash == token_hash, Token.revoked_at == None)  # noqa: E711
    ).first()
    if row is None:
        raise ApiError(401, "unauthorized", "Your session is no longer valid. Log in again.")
    _token, user = row
    if user.banned:
        raise ApiError(403, "banned", "This account is banned.")
    return user


UserDep = Annotated[User, Depends(current_user)]


def limit_by_ip(request: Request) -> None:
    host = request.client.host if request.client else "unknown"
    request.app.state.limiter.check(f"ip:{host}", now_of(request))


def find_user(session: Session, handle: str) -> User | None:
    return session.exec(select(User).where(func.lower(User.cf_handle) == handle.lower())).first()


def open_task(session: Session, now: float) -> DailyTask | None:
    return session.exec(
        select(DailyTask).where(DailyTask.posted_at <= now, DailyTask.closes_at > now)
    ).first()


def today_response(session: Session, user: User, now: float) -> TodayResponse:
    task = open_task(session, now)
    if task is None:
        return TodayResponse(server_time=now, task=None, message="No task is open right now. Check back soon.")
    attempt = session.exec(
        select(TaskAttempt).where(TaskAttempt.task_id == task.id, TaskAttempt.user_id == user.id)
    ).first()
    problem = attempt_info = None
    if attempt is not None:
        problem = ProblemInfo(
            contest_id=task.contest_id,
            index=task.problem_index,
            name=task.name,
            url=problem_url(task.contest_id, task.problem_index),
        )
        attempt_info = AttemptInfo(
            started_at=attempt.started_at,
            solved=attempt.accepted_at is not None and attempt.voided_at is None,
            accepted_at=attempt.accepted_at,
            solve_seconds=attempt.solve_seconds,
            runtime_ms=attempt.runtime_ms,
            rejected_count=attempt.rejected_count,
            points=attempt.points,
            provisional=attempt.provisional,
        )
    return TodayResponse(
        server_time=now,
        task=TodayTask(
            task_date=task.task_date,
            rating=task.rating,
            posted_at=task.posted_at,
            closes_at=task.closes_at,
            problem=problem,
            attempt=attempt_info,
        ),
    )


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

router = APIRouter(prefix="/api/v1")


@router.get("/time", response_model=TimeResponse)
def get_time(request: Request) -> TimeResponse:
    return TimeResponse(
        server_time=now_of(request),
        server_version=__version__,
        min_client_version=request.app.state.config.server.min_client_version,
    )


@router.post("/auth/challenge", response_model=ChallengeResponse, dependencies=[Depends(limit_by_ip)])
async def auth_challenge(body: HandleRequest, request: Request, session: SessionDep) -> ChallengeResponse:
    """FR-AUTH-1/2: confirm the handle exists and issue a compile-error challenge."""
    cf: CodeforcesAPI = request.app.state.cf
    config: Config = request.app.state.config
    now = int(now_of(request))
    try:
        info = await cf.user_info([body.cf_handle])
    except CodeforcesNotFound:
        raise ApiError(404, "handle_not_found", f"Codeforces has no user named {body.cf_handle!r}.")
    except CodeforcesError:
        raise ApiError(503, "codeforces_unavailable", "Codeforces is not responding. Try again in a minute.")
    handle = info[0]["handle"]

    existing = session.exec(
        select(AuthChallenge).where(
            func.lower(AuthChallenge.cf_handle) == handle.lower(),
            AuthChallenge.used_at == None,  # noqa: E711
            AuthChallenge.expires_at > now,
        )
    ).first()
    if existing is not None:
        challenge = existing
    else:
        try:
            problems = await cf.problemset_problems()
        except CodeforcesError:
            raise ApiError(503, "codeforces_unavailable", "Codeforces is not responding. Try again in a minute.")
        candidates = [p for p in problems if "contestId" in p and p["contestId"] < 100000]
        p = random.SystemRandom().choice(candidates)
        contest_id, index = problem_key(p)
        challenge = AuthChallenge(
            cf_handle=handle,
            contest_id=contest_id,
            problem_index=index,
            issued_at=now,
            expires_at=now + config.server.challenge_minutes * 60,
        )
        session.add(challenge)
        session.commit()
        session.refresh(challenge)
    name = next(
        (p.get("name", "") for p in await cf.problemset_problems()
         if problem_key(p) == (challenge.contest_id, challenge.problem_index)),
        "",
    )
    return ChallengeResponse(
        cf_handle=handle,
        contest_id=challenge.contest_id,
        index=challenge.problem_index,
        name=name,
        url=problem_url(challenge.contest_id, challenge.problem_index),
        issued_at=challenge.issued_at,
        expires_at=challenge.expires_at,
    )


@router.post("/auth/verify", response_model=VerifyResponse, dependencies=[Depends(limit_by_ip)])
async def auth_verify(body: HandleRequest, request: Request, session: SessionDep) -> VerifyResponse:
    """FR-AUTH-3..5: look for the compile error, then create or log in the account."""
    cf: CodeforcesAPI = request.app.state.cf
    config: Config = request.app.state.config
    now = int(now_of(request))
    challenge = session.exec(
        select(AuthChallenge)
        .where(
            func.lower(AuthChallenge.cf_handle) == body.cf_handle.lower(),
            AuthChallenge.used_at == None,  # noqa: E711
            AuthChallenge.expires_at + VERIFY_GRACE_SECONDS > now,
        )
        .order_by(AuthChallenge.issued_at.desc())  # type: ignore[attr-defined]
    ).first()
    if challenge is None:
        raise ApiError(400, "no_challenge", "No open challenge for this handle. Request a new one.")
    try:
        subs = await cf.user_status(challenge.cf_handle, 1, 20)
    except CodeforcesError:
        raise ApiError(503, "codeforces_unavailable", "Codeforces is not responding. Try again in a minute.")
    key = (challenge.contest_id, challenge.problem_index)
    in_window = [
        s for s in subs
        if submission_key(s) == key and challenge.issued_at <= s["creationTimeSeconds"] <= challenge.expires_at
    ]
    if not any(s.get("verdict") == "COMPILATION_ERROR" for s in in_window):
        if any(s.get("verdict") in (None, "TESTING") for s in in_window):
            raise ApiError(409, "still_judging", "Your submission is still being judged. Try again in a few seconds.")
        raise ApiError(
            400,
            "challenge_not_met",
            "No compilation-error submission to the challenge problem was found in the time window.",
        )

    challenge.used_at = now
    session.add(challenge)
    user = find_user(session, challenge.cf_handle)
    if user is None:
        user = User(cf_handle=challenge.cf_handle, joined_at=now)
    if user.banned:
        session.commit()
        raise ApiError(403, "banned", "This account is banned.")
    user.is_admin = config.is_admin(user.cf_handle)
    try:
        info = (await cf.user_info([user.cf_handle]))[0]
        user.cf_rating, user.cf_rank, user.rating_synced_at = info.get("rating"), info.get("rank"), now
    except CodeforcesError:
        pass  # the daily rating sync will fill it in
    session.add(user)
    session.commit()
    session.refresh(user)

    raw = secrets.token_urlsafe(32)  # 32 random bytes (FR-AUTH-4)
    session.add(Token(user_id=user.id, token_hash=hash_token(raw), created_at=now))
    session.commit()
    return VerifyResponse(token=raw, cf_handle=user.cf_handle, is_admin=user.is_admin)


@router.get("/me", response_model=Me)
def get_me(user: UserDep) -> Me:
    return Me(
        cf_handle=user.cf_handle,
        is_admin=user.is_admin,
        cf_rating=user.cf_rating,
        cf_rank=user.cf_rank,
        joined_at=user.joined_at,
        writeup=None,
    )


@router.get("/leaderboard", response_model=LeaderboardResponse)
def get_leaderboard(
    request: Request, session: SessionDep, _user: UserDep, period: Literal["week", "all"] = Query("week")
) -> LeaderboardResponse:
    return board.leaderboard(session, request.app.state.config, period, now_of(request))


@router.get("/users/{handle}", response_model=Profile)
def get_profile(handle: str, request: Request, session: SessionDep, _user: UserDep) -> Profile:
    """FR-PROF-1/3: any member can see any profile; banned members are hidden."""
    target = find_user(session, handle)
    if target is None or target.banned:
        raise ApiError(404, "user_not_found", f"No member named {handle!r}.")
    return board.profile(session, request.app.state.config, target, now_of(request))


@router.get("/tasks/today", response_model=TodayResponse)
def get_today(request: Request, session: SessionDep, user: UserDep) -> TodayResponse:
    return today_response(session, user, now_of(request))


@router.post("/tasks/today/start", response_model=TodayResponse)
def start_today(request: Request, session: SessionDep, user: UserDep) -> TodayResponse:
    """FR-TASK-6: one attempt per member per task, timed from server time."""
    now = now_of(request)
    task = open_task(session, now)
    if task is None:
        raise ApiError(404, "no_task", "No task is open right now.")
    exists = session.exec(
        select(TaskAttempt).where(TaskAttempt.task_id == task.id, TaskAttempt.user_id == user.id)
    ).first()
    if exists is None:
        session.add(TaskAttempt(user_id=user.id, task_id=task.id, started_at=int(now)))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()  # a concurrent start won; return that attempt
    return today_response(session, user, now)


@router.get("/projects/current", response_model=ProjectResponse)
def get_current_project(request: Request, session: SessionDep, user: UserDep) -> ProjectResponse:
    return request.app.state.projects.response(session, user, now_of(request))


@router.post("/projects/{project_id}/register", response_model=ProjectResponse)
async def register_repo(
    project_id: int, body: RegisterRequest, request: Request, session: SessionDep, user: UserDep
) -> ProjectResponse:
    """FR-PROJ-3/4: returns the verification code to commit as .gcboard."""
    svc: ProjectService = request.app.state.projects
    await svc.register(user, project_id, body.repo_url)
    return svc.response(session, user, now_of(request))


@router.post("/projects/{project_id}/register/verify", response_model=ProjectResponse)
async def verify_repo(project_id: int, request: Request, session: SessionDep, user: UserDep) -> ProjectResponse:
    svc: ProjectService = request.app.state.projects
    await svc.verify(user, project_id)
    return svc.response(session, user, now_of(request))


@router.post("/projects/{project_id}/submit", response_model=ProjectResponse)
async def submit_project(project_id: int, request: Request, session: SessionDep, user: UserDep) -> ProjectResponse:
    """FR-PROJ-7. The writeup session starts here once M4 lands."""
    svc: ProjectService = request.app.state.projects
    await svc.submit(user, project_id)
    return svc.response(session, user, now_of(request))


@router.get("/rules", response_model=RulesResponse)
def get_rules(request: Request, _user: UserDep) -> RulesResponse:
    c: Config = request.app.state.config
    return RulesResponse(
        timezone=c.general.timezone,
        daily_post_time=c.general.daily_post_time,
        tasks=c.tasks.model_dump(),
        projects=c.projects.model_dump(),
        writeup=c.writeup.model_dump(),
        history=c.history.model_dump(),
    )


# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------


def create_app(
    config: Config,
    *,
    cf: CodeforcesAPI | None = None,
    repos: RepoHost | None = None,
    clock: Callable[[], float] = time.time,
    run_scheduler: bool = True,
) -> FastAPI:
    engine = make_engine(config.server.database)
    cf = cf or CodeforcesClient(min_interval=config.server.codeforces_min_interval)
    repos = repos or GitHubClient(
        token=os.environ.get("GCBOARD_GITHUB_TOKEN") or config.server.github_token,
        max_bytes=config.projects.max_repo_mb * 1024 * 1024,
    )
    projects = ProjectService(engine, config, repos, clock)
    jobs = Jobs(engine, config, cf, clock, projects=projects)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched = start_scheduler(jobs) if run_scheduler else None
        yield
        if sched is not None:
            sched.shutdown(wait=False)

    app = FastAPI(title="gcboard", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.engine = engine
    app.state.cf = cf
    app.state.jobs = jobs
    app.state.projects = projects
    app.state.clock = clock
    app.state.limiter = RateLimiter(config.server.requests_per_minute)

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": exc.error, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": "http_error", "message": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return JSONResponse(
            {"error": "invalid_request", "message": f"{where}: {first.get('msg', 'invalid input')}"},
            status_code=422,
        )

    app.include_router(router)
    return app


def run() -> None:
    parser = argparse.ArgumentParser(prog="gcboard-server", description="Run the gcboard API server.")
    parser.add_argument("--config", default=os.environ.get("GCBOARD_CONFIG", "config.toml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import uvicorn

    uvicorn.run(create_app(load_config(args.config)), host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    run()
