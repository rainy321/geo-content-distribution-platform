"""Generate a credential-free DongClaw GEO evaluation template or report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from services.dongclaw_evaluation_service import (  # noqa: E402
    DongClawEvaluationError,
    build_dongclaw_input_template,
    evaluate_dongclaw_manifest,
    render_dongclaw_markdown,
)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate imported Qwen/DeepSeek/Doubao answers for the fixed, "
            "authorized DongClaw pilot. This command never calls a provider."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-template", type=Path, metavar="PATH")
    mode.add_argument("--input", type=Path, metavar="PATH")
    parser.add_argument("--json-output", type=Path, metavar="PATH")
    parser.add_argument("--markdown-output", type=Path, metavar="PATH")
    parser.add_argument(
        "--study-id", default="dongclaw-geo-pilot-20260910", metavar="ID"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        if args.write_template is not None:
            template = build_dongclaw_input_template(study_id=args.study_id)
            _atomic_write(
                args.write_template,
                json.dumps(template, ensure_ascii=False, indent=2) + "\n",
            )
            print(f"Template written: {args.write_template.resolve()}")
            return 0

        if args.json_output is None and args.markdown_output is None:
            raise DongClawEvaluationError(
                "使用 --input 时至少提供 --json-output 或 --markdown-output"
            )
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        report = evaluate_dongclaw_manifest(payload)
        if args.json_output is not None:
            _atomic_write(
                args.json_output,
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            )
        if args.markdown_output is not None:
            _atomic_write(args.markdown_output, render_dongclaw_markdown(report))
        print(
            f"Evaluation complete: status={report['status']}, "
            f"attempts={len(payload.get('attempts', []))}"
        )
        return 0
    except (DongClawEvaluationError, json.JSONDecodeError, OSError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2


def _atomic_write(path: Path, content: str) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
