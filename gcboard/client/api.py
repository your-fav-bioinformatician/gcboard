"""HTTP calls to the gcboard server, token storage, and server clock offset."""

from __future__ import annotations

import os
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import quote
from typing import Any

import httpx
from platformdirs import user_config_dir

from gcboard import __version__
from gcboard.shared import schemas

DEFAULT_SERVER = "http://127.0.0.1:8000"


def config_dir() -> Path:
    return Path(user_config_dir("gcboard", appauthor=False))


def server_url() -> str:
    """GCBOARD_SERVER env var, else `server_url` in the client config file."""
    if env := os.environ.get("GCBOARD_SERVER"):
        return env.rstrip("/")
    path = config_dir() / "config.toml"
    if path.exists():
        with open(path, "rb") as f:
            url = tomllib.load(f).get("server_url")
        if url:
            return str(url).rstrip("/")
    return DEFAULT_SERVER


def _token_path() -> Path:
    return config_dir() / "token"


def load_token() -> str | None:
    try:
        return _token_path().read_text().strip() or None
    except FileNotFoundError:
        return None


def save_token(token: str) -> None:
    """Write the token readable by the owner only (FR-AUTH-4)."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def clear_token() -> None:
    _token_path().unlink(missing_ok=True)


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, body: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.error, self.message = status, error, message
        self.body = body or {}  # e.g. "answers" on a rejected writeup save


class Api:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or server_url()) + "/api/v1"
        self.token = token
        self.offset = 0.0  # server_time - local_time
        self._http = httpx.AsyncClient(timeout=10, headers={"User-Agent": f"gcboard-client/{__version__}"})

    def server_now(self) -> float:
        """Local clock corrected to server time (FR-TASK-10)."""
        return time.time() + self.offset

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = await self._http.request(method, self.base_url + path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(0, "network", f"Can't reach the server ({exc.__class__.__name__}).") from exc
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code >= 400:
            body = body if isinstance(body, dict) else {}
            raise ApiError(
                r.status_code, body.get("error", "http_error"), body.get("message", f"HTTP {r.status_code}"), body
            )
        return body

    async def sync_clock(self) -> schemas.TimeResponse:
        t0 = time.time()
        data = schemas.TimeResponse.model_validate(await self._request("GET", "/time"))
        t1 = time.time()
        self.offset = data.server_time - (t0 + t1) / 2
        return data

    async def challenge(self, handle: str) -> schemas.ChallengeResponse:
        return schemas.ChallengeResponse.model_validate(
            await self._request("POST", "/auth/challenge", json={"cf_handle": handle})
        )

    async def verify(self, handle: str) -> schemas.VerifyResponse:
        return schemas.VerifyResponse.model_validate(
            await self._request("POST", "/auth/verify", json={"cf_handle": handle})
        )

    async def me(self) -> schemas.Me:
        return schemas.Me.model_validate(await self._request("GET", "/me"))

    async def leaderboard(self, period: str) -> schemas.LeaderboardResponse:
        return schemas.LeaderboardResponse.model_validate(
            await self._request("GET", "/leaderboard", params={"period": period})
        )

    async def today(self) -> schemas.TodayResponse:
        return schemas.TodayResponse.model_validate(await self._request("GET", "/tasks/today"))

    async def start_task(self) -> schemas.TodayResponse:
        return schemas.TodayResponse.model_validate(await self._request("POST", "/tasks/today/start"))

    async def profile(self, handle: str) -> schemas.Profile:
        return schemas.Profile.model_validate(await self._request("GET", f"/users/{quote(handle, safe='')}"))

    async def current_project(self) -> schemas.ProjectResponse:
        return schemas.ProjectResponse.model_validate(await self._request("GET", "/projects/current"))

    async def register_repo(self, project_id: int, repo_url: str) -> schemas.ProjectResponse:
        return schemas.ProjectResponse.model_validate(
            await self._request("POST", f"/projects/{project_id}/register", json={"repo_url": repo_url})
        )

    async def verify_repo(self, project_id: int) -> schemas.ProjectResponse:
        return schemas.ProjectResponse.model_validate(
            await self._request("POST", f"/projects/{project_id}/register/verify")
        )

    async def submit_project(self, project_id: int) -> schemas.WriteupSession:
        """Freezes the entry and starts the locked writeup."""
        return schemas.WriteupSession.model_validate(await self._request("POST", f"/projects/{project_id}/submit"))

    async def writeup(self) -> schemas.WriteupSession:
        return schemas.WriteupSession.model_validate(await self._request("GET", "/writeups/current"))

    async def save_draft(self, answers: list[str]) -> schemas.WriteupSession:
        return schemas.WriteupSession.model_validate(
            await self._request("PUT", "/writeups/current/draft", json={"answers": answers})
        )

    async def submit_writeup(self, answers: list[str] | None) -> schemas.WriteupResult:
        return schemas.WriteupResult.model_validate(
            await self._request("POST", "/writeups/current/submit", json={"answers": answers})
        )

    async def rules(self) -> schemas.RulesResponse:
        return schemas.RulesResponse.model_validate(await self._request("GET", "/rules"))

    async def aclose(self) -> None:
        await self._http.aclose()
