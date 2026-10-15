"""Start the local built workspace and open its saved GEO report."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def get_reports(port: int):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/geo-tests/reports", timeout=2) as response:
            payload = json.load(response)
        return payload["data"] if payload.get("code") == 200 and isinstance(payload.get("data"), list) else None
    except urllib.error.HTTPError as error:
        return [] if error.code == 401 else None
    except (OSError, ValueError, KeyError):
        return None


def main() -> int:
    if not (ROOT / "sau_frontend/dist/index.html").is_file():
        print("缺少前端构建，请在 sau_frontend 中运行 npm run build。")
        return 1
    port, reports = 5409, None
    for candidate in range(5409, 5421):
        reports = get_reports(candidate)
        if reports is not None:
            port = candidate
            break
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", candidate))
            except OSError:
                continue
        port = candidate
        break
    else:
        print("本机端口 5409–5420 均被占用。")
        return 1
    output = ROOT / "runtime/geo-tests/local-app"
    output.mkdir(parents=True, exist_ok=True)
    if reports is None:
        environment = {**os.environ, "SERVER_HOST": "127.0.0.1", "SERVER_PORT": str(port),
                       "RUN_PUBLISH_SCHEDULER": "false", "SEED_DEMO_DATA": "false"}
        with (output / "backend.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "sau_backend.py"],
                                       cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (output / "server.json").write_text(json.dumps({"pid": process.pid, "port": port,
                         "url": f"http://127.0.0.1:{port}/#/geo-tests"}), encoding="utf-8")
        for _ in range(100):
            if process.poll() is not None:
                break
            reports = get_reports(port)
            if reports is not None:
                break
            time.sleep(0.25)
        if reports is None:
            print(f"启动未完成，请查看 {output / 'backend.log'}")
            return 1
    selected = next((item for item in reports if item.get("status") == "completed"), None)
    url = f"http://127.0.0.1:{port}/#/geo-tests"
    if selected:
        url += "?report=" + selected["id"]
    print(url)
    webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
