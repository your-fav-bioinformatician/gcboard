"""M5 terminal compatibility: run the real `gcboard` process in a
pseudo-terminal under different TERM values, color depths, and NO_COLOR,
at the minimum 80x24 size (NFR-COMPAT-1/2).

This covers what can be automated. The named terminals in SRD 2.3 still need
the manual pass in docs/terminal-checklist.md.
"""

import fcntl
import os
import re
import select
import struct
import subprocess
import sys
import termios
import time

import pytest
from sqlmodel import Session

from gcboard.server.db import Token, User
from gcboard.server.main import hash_token

from .test_client import server  # noqa: F401  (fixture)

pty = pytest.importorskip("pty")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")

ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b[\]P_^][^\x1b\x07]*(?:\x07|\x1b\\)|\x1b[@-_]")
TRUECOLOR = re.compile(r"\x1b\[[0-9;]*[34]8;2;")
COLOR256 = re.compile(r"\x1b\[[0-9;]*[34]8;5;")


class Terminal:
    """The client running in a pty of a given size, with keystrokes and screen text."""

    def __init__(self, env: dict[str, str], cols: int = 80, rows: int = 24) -> None:
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from gcboard.client.app import main; main()"],
            stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True, start_new_session=True,
        )
        os.close(slave)
        self.raw = b""

    def read_until(self, needle: str, timeout: float = 30) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return self.text()
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if ready:
                try:
                    self.raw += os.read(self.master, 65536)
                except OSError:
                    break
        raise AssertionError(f"{needle!r} never appeared. Screen text:\n{self.text()[-2000:]}")

    def text(self) -> str:
        return ANSI.sub("", self.raw.decode("utf-8", errors="replace"))

    def send(self, keys: str) -> None:
        os.write(self.master, keys.encode())

    def wait_exit(self, timeout: float = 15) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return self.proc.returncode
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if ready:
                try:
                    self.raw += os.read(self.master, 65536)
                except OSError:
                    pass
        self.proc.kill()
        raise AssertionError("the client did not exit")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        os.close(self.master)


@pytest.fixture
def logged_in_env(server, tmp_path):  # noqa: F811
    """A member with a saved token, so the client opens straight on the board."""
    raw = "pty-test-token"
    with Session(server.state.engine) as s:
        user = User(cf_handle="noor", joined_at=0)
        s.add(user)
        s.commit()
        s.add(Token(user_id=user.id, token_hash=hash_token(raw), created_at=0))
        s.commit()
    token_dir = tmp_path / "xdg" / "gcboard"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "token").write_text(raw)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TEXTUAL", "NO_COLOR", "COLORTERM", "TERM"))}
    env.update(GCBOARD_SERVER=os.environ["GCBOARD_SERVER"], XDG_CONFIG_HOME=str(tmp_path / "xdg"), LANG="C.UTF-8")
    return env


CASES = {
    "truecolor": {"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    "256 colors": {"TERM": "xterm-256color", "TEXTUAL_COLOR_SYSTEM": "256"},
    "16 colors (xterm)": {"TERM": "xterm", "TEXTUAL_COLOR_SYSTEM": "standard"},
    "16 colors (linux console)": {"TERM": "linux", "TEXTUAL_COLOR_SYSTEM": "standard"},
    "NO_COLOR": {"TERM": "xterm-256color", "NO_COLOR": "1"},
}


@pytest.mark.parametrize("case", list(CASES))
def test_client_runs_in_terminal(case, logged_in_env):
    env = {**logged_in_env, **CASES[case]}
    term = Terminal(env)
    try:
        term.read_until("leaderboard --week")
        term.read_until("Nobody has scored yet")
        term.send("t")
        term.read_until("No task is open right now")
        term.send("?")
        term.read_until("Daily task")
        term.send("q")
        assert term.wait_exit() == 0
    finally:
        term.close()
    out = term.raw.decode("utf-8", errors="replace")
    if env.get("TEXTUAL_COLOR_SYSTEM") == "standard":
        assert not TRUECOLOR.search(out) and not COLOR256.search(out), "16-color mode sent extended colors"
    if env.get("TEXTUAL_COLOR_SYSTEM") == "256":
        assert not TRUECOLOR.search(out), "256-color mode sent truecolor"


def test_too_small_terminal_shows_notice(logged_in_env):
    term = Terminal({**logged_in_env, "TERM": "xterm-256color"}, cols=79, rows=24)
    try:
        term.read_until("Please enlarge your terminal to at least 80x24")
        term.send("q")
        assert term.wait_exit() == 0
    finally:
        term.close()
