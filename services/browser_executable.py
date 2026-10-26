"""Choose an ordinary installed browser without downloading or spawning helpers."""
from __future__ import annotations

import os
from pathlib import Path


def _installed_browser(relative_path, variables):
    for variable in variables:
        root = os.getenv(variable)
        candidate = Path(root) / relative_path if root else None
        if candidate is not None and candidate.is_file():
            return str(candidate)
    return None


def edge_executable():
    return _installed_browser(
        "Microsoft/Edge/Application/msedge.exe",
        ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"),
    )


def chrome_executable():
    return _installed_browser(
        "Google/Chrome/Application/chrome.exe",
        ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"),
    )


def browser_executable_options(configured_path=""):
    """Respect explicit configuration, otherwise prefer installed Chrome, then Edge."""
    executable = str(configured_path or "").strip() or chrome_executable() or edge_executable()
    return {"executable_path": executable} if executable else {}
