"""Production WSGI entry point for the GEO web application.

``python sau_backend.py`` remains the local-development entry point. Containers
and production installs use this module so Flask's development server is never
part of the production request path.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any


DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 5409
DEFAULT_SERVER_THREADS = 8
DEFAULT_CHANNEL_TIMEOUT_SECONDS = 180


def _bounded_int(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value or "")
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def build_waitress_options(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build bounded Waitress options from an explicit environment mapping."""

    values = os.environ if environ is None else environ
    host = str(values.get("SERVER_HOST", DEFAULT_SERVER_HOST)).strip()
    if not host:
        host = DEFAULT_SERVER_HOST

    return {
        "host": host,
        "port": _bounded_int(
            values.get("SERVER_PORT"),
            default=DEFAULT_SERVER_PORT,
            minimum=1,
            maximum=65535,
        ),
        "threads": _bounded_int(
            values.get("SERVER_THREADS"),
            default=DEFAULT_SERVER_THREADS,
            minimum=1,
            maximum=64,
        ),
        "channel_timeout": _bounded_int(
            values.get("SERVER_CHANNEL_TIMEOUT_SECONDS"),
            default=DEFAULT_CHANNEL_TIMEOUT_SECONDS,
            minimum=30,
            maximum=600,
        ),
        "ident": "GEO",
    }


def main(
    *,
    environ: Mapping[str, str] | None = None,
    serve_callable: Callable[..., Any] | None = None,
    application: Any = None,
) -> None:
    """Serve the existing Flask application with a production WSGI server."""

    if serve_callable is None:
        from waitress import serve as serve_callable

    if application is None:
        from sau_backend import app as application

    serve_callable(application, **build_waitress_options(environ))


if __name__ == "__main__":
    main()
