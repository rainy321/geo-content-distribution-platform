"""Read existing local reports without accepting filesystem paths from clients."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _reports(root: Path):
    root = root.resolve()
    if not root.is_dir():
        return
    for path in root.rglob("report.json"):
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(root)
            if resolved.stat().st_size > 5 * 1024 * 1024:
                continue
            report = json.loads(resolved.read_text(encoding="utf-8"))
            if (report.get("schema_version") != "1.0"
                    or report.get("kind") != "geo_search_test_report"
                    or not isinstance(report.get("profile"), dict)
                    or not isinstance(report["profile"].get("name"), str)
                    or not isinstance(report.get("cases"), list)):
                continue
            report_id = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:24]
            yield report_id, report
        except (OSError, ValueError, AttributeError):
            continue


def list_saved_reports(root: Path) -> list[dict]:
    reports = [{"id": report_id, "name": report["profile"]["name"],
                "run_id": report.get("run_id"), "status": report.get("status"),
                "updated_at": report.get("updated_at"), "case_count": len(report["cases"])}
               for report_id, report in _reports(root)]
    return sorted(reports, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def read_saved_report(root: Path, report_id: str) -> dict | None:
    for candidate, report in _reports(root):
        if candidate == report_id:
            return report
    return None
