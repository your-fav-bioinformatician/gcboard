"""GitHub reads and source-line counting (SRD 5.3 "source files", 5.4).

The server reads public repos with its own token. A commit is fetched as one
tarball and read in memory: nothing is unpacked to disk and nothing from a
repo is ever executed or interpreted (NFR-SEC-2). Identifier extraction and
snippet selection arrive with M4.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import tarfile
from dataclasses import dataclass
from typing import Protocol

import httpx

from gcboard import __version__

log = logging.getLogger(__name__)

API = "https://api.github.com"

SOURCE_EXTENSIONS = frozenset(
    ".py .js .ts .jsx .tsx .java .c .h .cpp .hpp .cc .cs .go .rs .rb .php .kt .swift .lua .dart".split()
)
EXCLUDED_PATH_PARTS = ("test", "vendor", "node_modules", "dist", "build", ".min.")
MAX_SOURCE_BYTES = 200 * 1024
VERIFY_FILE = ".gcboard"
RETRIES = 3
RETRY_DELAY = 1.0  # seconds; grows with each attempt

_REPO_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


class RepoError(Exception):
    """GitHub could not be read right now (network, rate limit, server error)."""


class RepoUnavailable(RepoError):
    """The repo does not exist, is private, or is empty."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def parse_repo_url(url: str) -> RepoRef | None:
    """Accept github.com/owner/repo URLs only; normalize to lowercase."""
    m = _REPO_URL.match(url.strip())
    if not m or m.group(2) in (".", ".."):
        return None
    return RepoRef(m.group(1).lower(), m.group(2).lower())


def is_source_path(path: str) -> bool:
    """SRD 5.3: an allowed extension, and no excluded word anywhere in the path.

    The exclusion is a plain substring match, exactly as written in the SRD,
    so for example `src/contest.py` is excluded because it contains "test".
    """
    lower = path.lower()
    dot = lower.rfind(".")
    if dot == -1 or lower[dot:] not in SOURCE_EXTENSIONS:
        return False
    return not any(part in lower for part in EXCLUDED_PATH_PARTS)


def count_lines(data: bytes) -> int:
    """Non-blank lines."""
    return sum(1 for line in data.decode("utf-8", errors="replace").splitlines() if line.strip())


@dataclass(frozen=True)
class RepoFiles:
    """Source files (path -> bytes) of one commit, plus the .gcboard file."""

    sha: str
    sources: dict[str, bytes]
    verify_file: str | None

    @property
    def source_loc(self) -> int:
        return sum(count_lines(b) for b in self.sources.values())


def read_tarball(data: bytes, sha: str) -> RepoFiles:
    """Pick source files and .gcboard out of a GitHub tarball, in memory.

    GitHub prefixes every path with one top-level directory; it is stripped.
    Only regular files are read; links, devices and oversized files are skipped.
    """
    sources: dict[str, bytes] = {}
    verify: str | None = None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if not member.isreg():
                continue
            parts = member.name.split("/", 1)
            if len(parts) != 2 or not parts[1]:
                continue
            path = parts[1]
            if path == VERIFY_FILE and member.size <= 1024:
                f = tar.extractfile(member)
                verify = f.read().decode("utf-8", errors="replace") if f else None
            elif member.size <= MAX_SOURCE_BYTES and is_source_path(path):
                f = tar.extractfile(member)
                if f:
                    sources[path] = f.read()
    return RepoFiles(sha=sha, sources=sources, verify_file=verify)


class RepoHost(Protocol):
    """What the server needs from GitHub (a fake in tests)."""

    async def head_sha(self, repo: RepoRef) -> str: ...
    async def files_at(self, repo: RepoRef, sha: str) -> RepoFiles: ...


class GitHubClient:
    def __init__(self, token: str = "", max_bytes: int = 50 * 1024 * 1024, http: httpx.AsyncClient | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"gcboard/{__version__}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.max_bytes = max_bytes
        # httpx drops the Authorization header when a redirect leaves api.github.com.
        self._http = http or httpx.AsyncClient(base_url=API, headers=headers, timeout=60, follow_redirects=True)

    async def _get(self, path: str, **kw) -> httpx.Response:
        """GET with up to RETRIES attempts on network errors and 5xx."""
        for attempt in range(RETRIES):
            if attempt:
                await asyncio.sleep(RETRY_DELAY * attempt)
            try:
                r = await self._http.get(path, **kw)
            except httpx.HTTPError as exc:
                error = RepoError(f"GitHub unreachable: {exc!r}")
                continue
            if r.status_code >= 500:
                error = RepoError(f"GitHub returned HTTP {r.status_code}")
                continue
            break
        else:
            raise error
        if r.status_code == 404:
            raise RepoUnavailable("repository not found, or it is private")
        if r.status_code == 409:
            raise RepoUnavailable("repository is empty")
        if r.status_code >= 400:
            raise RepoError(f"GitHub returned HTTP {r.status_code}")
        return r

    async def head_sha(self, repo: RepoRef) -> str:
        info = (await self._get(f"/repos/{repo.owner}/{repo.name}")).json()
        if info.get("private"):
            raise RepoUnavailable("repository is private")
        branch = info["default_branch"]
        r = await self._get(
            f"/repos/{repo.owner}/{repo.name}/commits/{branch}", headers={"Accept": "application/vnd.github.sha"}
        )
        return r.text.strip()

    async def files_at(self, repo: RepoRef, sha: str) -> RepoFiles:
        try:
            async with self._http.stream("GET", f"/repos/{repo.owner}/{repo.name}/tarball/{sha}") as r:
                if r.status_code == 404:
                    raise RepoUnavailable("repository not found, or it is private")
                if r.status_code >= 400:
                    raise RepoError(f"GitHub returned HTTP {r.status_code}")
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > self.max_bytes:
                        raise RepoUnavailable(f"repository is larger than {self.max_bytes // 2**20} MB")
        except httpx.HTTPError as exc:
            raise RepoError(f"GitHub unreachable: {exc!r}") from exc
        try:
            return read_tarball(bytes(buf), sha)
        except tarfile.TarError as exc:
            raise RepoError(f"could not read repository archive: {exc}") from exc
