"""Open links in the desktop's default browser (FR-TASK-11).

Python's webbrowser module counts a launch as failed when the launcher exits
at once, which xdg-open does when it hands the link to a running browser. It
then tries the next browser on its list, so the link can open in a browser
that isn't the default (or in two). Here the platform's own opener runs once,
with its output discarded so it can't draw over the terminal UI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser


def open_url(url: str) -> bool:
    """Open `url`; the BROWSER environment variable, if set, still wins."""
    if os.environ.get("BROWSER"):
        return webbrowser.open(url)
    try:
        if sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        opener = "open" if sys.platform == "darwin" else shutil.which("xdg-open")
        if opener:
            subprocess.Popen(
                [opener, url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            return True
    except OSError:
        pass
    return webbrowser.open(url)
