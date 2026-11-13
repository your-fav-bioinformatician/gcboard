"""FR-TASK-11: links open once, in the default browser."""

import subprocess

from gcboard.client import browser

URL = "https://codeforces.com/problemset/problem/1004/A"


def test_linux_uses_xdg_open_once(monkeypatch):
    calls = []
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.setattr(browser.shutil, "which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setattr(browser.webbrowser, "open", lambda url: calls.append("webbrowser"))
    assert browser.open_url(URL)
    assert len(calls) == 1  # no fallback to other browsers
    cmd, kw = calls[0]
    assert cmd == ["/usr/bin/xdg-open", URL]
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL  # keeps the TUI clean


def test_macos_uses_open(monkeypatch):
    calls = []
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(browser.sys, "platform", "darwin")
    monkeypatch.setattr(browser.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert browser.open_url(URL) and calls == [["open", URL]]


def test_browser_env_var_wins(monkeypatch):
    calls = []
    monkeypatch.setenv("BROWSER", "firefox")
    monkeypatch.setattr(browser.webbrowser, "open", lambda url: calls.append(url) or True)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *a, **k: calls.append("popen"))
    assert browser.open_url(URL) and calls == [URL]


def test_falls_back_when_no_opener(monkeypatch):
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.setattr(browser.shutil, "which", lambda name: None)
    monkeypatch.setattr(browser.webbrowser, "open", lambda url: True)
    assert browser.open_url(URL)
