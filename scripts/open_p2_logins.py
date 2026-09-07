from __future__ import annotations

import argparse
import asyncio
import queue
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.createTable import initialize_database
from myUtils.login import (
    bilibili_cookie_gen,
    douyin_cookie_gen,
    get_ks_cookie,
    get_tencent_cookie,
    tiktok_cookie_gen,
)


LOGIN_TARGETS = (
    ("douyin", "抖音", douyin_cookie_gen),
    ("kuaishou", "快手", get_ks_cookie),
    ("bilibili", "Bilibili", bilibili_cookie_gen),
    ("channels", "视频号", get_tencent_cookie),
    ("tiktok", "TikTok", tiktok_cookie_gen),
)


def _run_login(
    key: str,
    runner,
    account_name: str,
    status_queue: queue.Queue,
    database_path: Path,
    cookies_directory: Path,
    timeout_seconds: int,
) -> None:
    try:
        asyncio.run(
            runner(
                account_name,
                status_queue,
                database_path=database_path,
                cookies_directory=cookies_directory,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception as exc:
        print(f"LOGIN_ERROR {key} {exc.__class__.__name__}", flush=True)
        status_queue.put("500")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="一次打开五个 P2 渠道的人工登录入口。"
    )
    parser.add_argument("--database", type=Path, default=Path("db/database.db"))
    parser.add_argument("--cookies", type=Path, default=Path("cookiesFile"))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--platform",
        action="append",
        choices=[item[0] for item in LOGIN_TARGETS],
        help="只打开指定平台；可重复传入。默认打开全部平台。",
    )
    parser.add_argument(
        "--account-name",
        help="单平台重试时使用的账号显示名，避免与仍在等待的旧会话冲突。",
    )
    args = parser.parse_args()

    database_path = args.database.expanduser().resolve()
    cookies_directory = args.cookies.expanduser().resolve()
    initialize_database(database_path)
    cookies_directory.mkdir(parents=True, exist_ok=True)

    sessions = []
    selected = set(args.platform or [])
    targets = [item for item in LOGIN_TARGETS if not selected or item[0] in selected]
    if args.account_name and len(targets) != 1:
        parser.error("--account-name 只能和单个 --platform 一起使用")
    for key, label, runner in targets:
        status_queue: queue.Queue = queue.Queue()
        account_name = args.account_name or f"{key}-main"
        thread = threading.Thread(
            target=_run_login,
            args=(
                key,
                runner,
                account_name,
                status_queue,
                database_path,
                cookies_directory,
                args.timeout,
            ),
            daemon=True,
            name=f"login-{key}",
        )
        thread.start()
        sessions.append(
            {
                "key": key,
                "label": label,
                "thread": thread,
                "queue": status_queue,
                "terminal": False,
            }
        )
        time.sleep(0.8)

    print(
        f"LOGIN_BATCH_READY {len(targets)} 个平台登录入口正在打开，请在可见窗口中完成登录。",
        flush=True,
    )
    deadline = time.monotonic() + max(30, args.timeout)
    while time.monotonic() < deadline:
        all_terminal = True
        for session in sessions:
            if session["terminal"]:
                continue
            all_terminal = False
            try:
                status = session["queue"].get_nowait()
            except queue.Empty:
                if not session["thread"].is_alive():
                    session["terminal"] = True
                    print(f"LOGIN_RESULT {session['key']} stopped", flush=True)
                continue
            normalized = str(status)
            if normalized in {"200", "500"}:
                session["terminal"] = True
                result = "success" if normalized == "200" else "failed"
                print(f"LOGIN_RESULT {session['key']} {result}", flush=True)
            elif normalized == "MANUAL_LOGIN" or normalized.startswith("data:image"):
                print(f"LOGIN_READY {session['key']}", flush=True)
        if all(session["terminal"] for session in sessions):
            break
        time.sleep(0.25)

    pending = [session["key"] for session in sessions if not session["terminal"]]
    if pending:
        print("LOGIN_PENDING " + ",".join(pending), flush=True)


if __name__ == "__main__":
    main()
