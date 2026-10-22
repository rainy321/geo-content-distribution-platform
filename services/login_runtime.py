"""Safe login progress messages shared by the local worker and browser helpers."""
import logging
import re
from queue import Queue
from threading import Event


class LoginStatusQueue(Queue):
    def __init__(self):
        super().__init__()
        self.cancel_event = Event()
        self.terminal_sent = False
        self.error_reported = False

    def put(self, item, *args, **kwargs):
        if str(item).startswith("LOGIN_ERROR:"):
            self.error_reported = True
        if str(item) == "500" and not self.error_reported:
            self.error_reported = True
            super().put("LOGIN_ERROR:login_failed")
        if str(item) in {"200", "500"}:
            self.terminal_sent = True
        return super().put(item, *args, **kwargs)


class LoginCancelled(Exception):
    pass


def check_login_cancelled(status_queue):
    event = getattr(status_queue, "cancel_event", None)
    if event is not None and event.is_set():
        raise LoginCancelled()


def login_error_code(exc, *, stage=None):
    if isinstance(exc, LoginCancelled):
        return "login_cancelled"
    if isinstance(exc, (ModuleNotFoundError, ImportError)):
        return "component_missing"
    # Exception text is inspected locally and never forwarded to the client.
    message = str(exc).lower()
    if "executable doesn't exist" in message or "executable not found" in message or "distribution 'chrome' is not found" in message:
        return "browser_not_found"
    if "targetclosederror" in type(exc).__name__.lower() or any(
        marker in message for marker in ("target page, context or browser has been closed", "browser has been closed", "target closed")
    ):
        return "browser_closed"
    if stage == "launch" or "browsertype.launch:" in message:
        return "browser_launch_failed"
    if stage == "navigation":
        return "navigation_failed"
    if isinstance(exc, TimeoutError) or "timeouterror" in type(exc).__name__.lower():
        return "login_timeout"
    return "login_failed"


def report_login_error(status_queue, exc=None, *, code=None, stage=None):
    reason = code or login_error_code(exc, stage=stage)
    # Diagnostics stay local. Account/session values never enter this protocol.
    diagnostic = ""
    if exc is not None and stage == "launch":
        diagnostic = _safe_launch_diagnostic(exc)
    exception_type = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:80] if exc is not None else "none"
    logging.warning("Media login failure stage=%s code=%s exception_type=%s launch_diagnostic=%s",
                    stage if stage in {"launch", "navigation", "login"} else "unknown",
                    reason, exception_type, diagnostic)
    status_queue.put(f"LOGIN_ERROR:{reason}")
    status_queue.put("500")


def _safe_launch_diagnostic(exc):
    """Keep browser startup call logs while dropping secrets and profile names."""
    lines = []
    for line in str(exc)[:16000].splitlines():
        # Chromium launch arguments contain no credentials in our login flow;
        # defensively remove unexpected diagnostic lines that could carry them.
        if re.search(r"(?i)(cookie|authorization|password|passwd|token|secret|account)[\s:=]", line):
            lines.append("<sensitive diagnostic line removed>")
            continue
        line = re.sub(r"(?i)([A-Z]:[\\/]Users[\\/])[^\\/\s]+", r"\1<user>", line)
        line = re.sub(r"(?i)(https?://)[^/\s]*@", r"\1<credentials>@", line)
        line = re.sub(r"(?i)(--proxy[^\s=]*=)(?:\"[^\"]*\"|[^\s]+)", r"\1<redacted>", line)
        line = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line)
        lines.append(line)
    return "\n".join(lines)
