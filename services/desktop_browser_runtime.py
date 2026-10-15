"""Load native collector dependencies before the GUI changes loader state."""
from __future__ import annotations

import importlib


def initialize_browser_runtime():
    native = importlib.import_module("greenlet._greenlet")
    api = importlib.import_module("playwright.async_api")
    return {"greenlet_extension": getattr(native, "__file__", None),
            "playwright_available": callable(api.async_playwright)}
