"""hx.spawn: the one helper every hx subprocess goes through.

On Windows a console-mode child inherits its parent's console. A parent that
has none — pythonw.exe, a scheduled task running a GUI binary — makes the
child allocate a fresh console window: the flashing windows seen when
`hx team stalls` ran under pythonw (Q-854), one per `hc` call, where a
blocked one could hold the run. Every subprocess entry point in hx routes
through here so `CREATE_NO_WINDOW` and a NUL stdin apply exactly when the
parent has no console to give; a child started with no stdin argument would
otherwise inherit an invalid handle. With a console, or off Windows, these
are plain subprocess calls.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


def _no_console() -> bool:
    """True when this process has no console a child could inherit.

    Only Windows distinguishes: GetConsoleWindow() answers the attached
    console's handle, NULL for pythonw.exe and other GUI parents. POSIX has
    no per-process console window, so there is never anything to hide.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        return not ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return False


def _hidden_console(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Popen kwargs plus the no-console adjustments, when there is no console.

    CREATE_NO_WINDOW keeps the child from allocating a fresh window;
    stdin=DEVNULL keeps it off an invalid inherited handle, a common block
    under pythonw. A caller's own stdin and creationflags always win.
    """
    if not _no_console():
        return kwargs
    flags = kwargs.get("creationflags") or 0
    kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
    if "stdin" not in kwargs and "input" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    return kwargs


def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run through the no-console adjustments."""
    return subprocess.run(*args, **_hidden_console(kwargs))


def check_output(*args: Any, **kwargs: Any) -> bytes:
    """subprocess.check_output through the no-console adjustments."""
    return subprocess.check_output(*args, **_hidden_console(kwargs))


def Popen(*args: Any, **kwargs: Any) -> subprocess.Popen:
    """subprocess.Popen through the no-console adjustments."""
    return subprocess.Popen(*args, **_hidden_console(kwargs))
