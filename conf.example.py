import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
XHS_SERVER = os.getenv("XHS_SERVER", "http://127.0.0.1:11901")
LOCAL_CHROME_PATH = os.getenv("LOCAL_CHROME_PATH", "").strip()
LOCAL_CHROME_HEADLESS = os.getenv(
    "LOCAL_CHROME_HEADLESS", "true"
).strip().lower() in {"1", "true", "yes", "on"}
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
