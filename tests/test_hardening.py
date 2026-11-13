"""M5: schema check, request limits, rate limiter cleanup, GitHub retries."""

import asyncio
import io
import sqlite3
import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from gcboard.server import repos as repos_mod
from gcboard.server.db import SchemaError, make_engine
from gcboard.server.errors import ApiError
from gcboard.server.main import RateLimiter, create_app
from gcboard.server.repos import GitHubClient, RepoError, RepoRef, RepoUnavailable

REF = RepoRef("noor", "ttt")


def test_old_database_is_refused(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, cf_handle TEXT, joined_at INTEGER)")
    con.close()
    with pytest.raises(SchemaError, match=r"users: cf_rating, cf_rank, is_admin, banned, rating_synced_at"):
        make_engine(str(db))


def test_oversized_request_is_refused(config, cf, repos, clock):
    with TestClient(create_app(config, cf=cf, repos=repos, clock=clock, run_scheduler=False)) as c:
        r = c.put("/api/v1/writeups/current/draft", content=b"x" * (300 * 1024),
                  headers={"Content-Type": "application/json"})
        assert r.status_code == 413 and r.json()["error"] == "too_large"


def test_rate_limiter_forgets_idle_keys():
    rl = RateLimiter(2)
    for i in range(500):
        rl.check(f"token{i}", now=0)
    with pytest.raises(ApiError):
        rl.check("token0", now=1)
        rl.check("token0", now=1)
    rl.check("fresh", now=120)  # a minute later: idle keys are swept
    assert set(rl._hits) == {"fresh"}


def github(handler, **kw) -> GitHubClient:
    return GitHubClient(http=httpx.AsyncClient(base_url="https://api.github.com",
                                               transport=httpx.MockTransport(handler)), **kw)


def test_github_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(repos_mod, "RETRY_DELAY", 0)
    calls = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(502)
        if request.url.path == "/repos/noor/ttt":
            return httpx.Response(200, json={"private": False, "default_branch": "main"})
        return httpx.Response(200, text="abc123\n")

    assert asyncio.run(github(flaky).head_sha(REF)) == "abc123"
    assert calls == ["/repos/noor/ttt", "/repos/noor/ttt", "/repos/noor/ttt/commits/main"]

    with pytest.raises(RepoError, match="HTTP 503"):
        asyncio.run(github(lambda r: httpx.Response(503)).head_sha(REF))
    with pytest.raises(RepoUnavailable, match="private"):
        asyncio.run(github(lambda r: httpx.Response(200, json={"private": True})).head_sha(REF))
    with pytest.raises(RepoUnavailable, match="not found"):
        asyncio.run(github(lambda r: httpx.Response(404)).head_sha(REF))


def _tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"noor-ttt-abc/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_github_tarball_download_and_size_cap():
    data = _tarball({"game.py": b"print(1)\n", ".gcboard": b"gcboard-x\n"})
    files = asyncio.run(github(lambda r: httpx.Response(200, content=data)).files_at(REF, "abc"))
    assert files.source_loc == 1 and files.verify_file.strip() == "gcboard-x"
    big = _tarball({"blob.bin": bytes(range(256)) * 8000})
    with pytest.raises(RepoUnavailable, match="larger than"):
        asyncio.run(github(lambda r: httpx.Response(200, content=big), max_bytes=1024).files_at(REF, "abc"))
