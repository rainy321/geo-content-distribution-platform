from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont


_PALETTES = (
    ((13, 92, 99), (57, 184, 178), (237, 243, 243)),
    ((24, 48, 71), (48, 123, 164), (232, 241, 245)),
    ((70, 46, 94), (145, 101, 170), (242, 236, 246)),
    ((104, 62, 27), (218, 142, 55), (248, 240, 227)),
)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def recommend_article_images(
    database_path: str | Path,
    *,
    media_root: str | Path,
    article: Mapping[str, Any],
    project: Mapping[str, Any],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Rank existing image assets using article and project vocabulary."""

    if not 1 <= limit <= 9:
        raise ValueError("limit 必须在 1 到 9 之间")
    root = Path(media_root).expanduser().resolve()
    terms = _article_terms(article, project)
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, filesize, upload_time, file_path,
                   media_type, tags, source
            FROM file_records
            ORDER BY upload_time DESC, id DESC
            """
        ).fetchall()

    ranked = []
    for row in rows:
        record = _serialize_record(row)
        file_path = str(record.get("file_path") or "")
        if Path(file_path).suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        candidate = (root / file_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        searchable = " ".join(
            [record.get("filename", ""), *record.get("tags", [])]
        ).lower()
        matched = [term for term in terms if term.lower() in searchable]
        score = len(matched) * 10
        if record.get("source") == "generated":
            score += 2
        if score <= 0:
            continue
        ranked.append(
            {
                **record,
                "match_score": score,
                "matched_terms": matched[:5],
            }
        )
    ranked.sort(
        key=lambda item: (item["match_score"], item["id"]),
        reverse=True,
    )
    return ranked[:limit]


def ensure_article_cover(
    database_path: str | Path,
    *,
    media_root: str | Path,
    article: Mapping[str, Any],
    project: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    recommendations = recommend_article_images(
        database_path,
        media_root=media_root,
        article=article,
        project=project,
        limit=1,
    )
    if recommendations:
        return recommendations[0], False
    return generate_article_cover(
        database_path,
        media_root=media_root,
        article=article,
        project=project,
    ), True


def generate_article_cover(
    database_path: str | Path,
    *,
    media_root: str | Path,
    article: Mapping[str, Any],
    project: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a deterministic editorial cover without contacting a provider."""

    root = Path(media_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    title = str(article.get("title") or "GEO 内容").strip()[:80]
    brand = str(project.get("name") or "GEO CONTENT").strip()[:40]
    digest = hashlib.sha256(f"{brand}|{title}".encode("utf-8")).digest()
    deep, accent, paper = _PALETTES[digest[0] % len(_PALETTES)]

    width, height = 1200, 628
    image = Image.new("RGB", (width, height), paper)
    gradient_draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        blend = min(1.0, ratio * 0.58)
        color = tuple(
            int(paper[index] * (1 - blend) + accent[index] * blend)
            for index in range(3)
        )
        gradient_draw.line((0, y, width, y), fill=color)

    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, 28, height), fill=(*accent, 255))
    draw.rounded_rectangle(
        (775, -95, 1280, 410),
        radius=95,
        fill=(*deep, 32),
        outline=(*deep, 60),
        width=2,
    )
    draw.ellipse((865, 175, 1265, 575), fill=(*accent, 54))
    draw.line((86, 112, 1110, 112), fill=(*deep, 72), width=2)

    display_font = _load_font(62, bold=True)
    body_font = _load_font(25)
    label_font = _load_font(20, bold=True)
    can_draw_chinese = display_font is not None
    display_font = display_font or ImageFont.load_default()
    body_font = body_font or ImageFont.load_default()
    label_font = label_font or ImageFont.load_default()

    draw.text((86, 68), "GEO / EDITORIAL COVER", fill=(*deep, 215), font=label_font)
    if can_draw_chinese:
        lines = _wrap_text(draw, title, display_font, max_width=760, max_lines=3)
        draw.multiline_text(
            (86, 168),
            "\n".join(lines),
            fill=(*deep, 255),
            font=display_font,
            spacing=16,
        )
        draw.text((88, 535), brand, fill=(*deep, 205), font=body_font)
    else:
        draw.text((86, 210), "GEO CONTENT", fill=(*deep, 255), font=display_font)
        draw.text((88, 535), "CONTENT OPERATIONS", fill=(*deep, 205), font=body_font)

    stored_filename = f"generated-{uuid.uuid4().hex}.png"
    output_path = root / stored_filename
    image.save(output_path, format="PNG", optimize=True)
    tags = _article_terms(article, project)[:20]
    logical_filename = f"GEO封面-{article.get('id', 'draft')}.png"
    with closing(_connect(database_path)) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO file_records (
                    filename, filesize, file_path, media_type, tags, source
                ) VALUES (?, ?, ?, 'image', ?, 'generated')
                """,
                (
                    logical_filename,
                    round(output_path.stat().st_size / (1024 * 1024), 2),
                    stored_filename,
                    json.dumps(tags, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT * FROM file_records WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
    return _serialize_record(row)


def _load_font(size: int, *, bold: bool = False):
    candidates = (
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
        if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return None


def _wrap_text(draw, text: str, font, *, max_width: int, max_lines: int):
    lines = []
    current = ""
    for character in text:
        candidate = current + character
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if current and width > max_width:
            lines.append(current)
            current = character
            if len(lines) == max_lines - 1:
                break
        else:
            current = candidate
    remainder_start = sum(len(line) for line in lines)
    if current:
        visible = current
        if remainder_start + len(current) < len(text):
            visible = current[:-1] + "…" if len(current) > 1 else "…"
        lines.append(visible)
    return lines[:max_lines]


def _article_terms(
    article: Mapping[str, Any],
    project: Mapping[str, Any],
) -> list[str]:
    raw: list[str] = []
    for value in (
        project.get("name"),
        project.get("product"),
        project.get("industry"),
    ):
        if value:
            raw.append(str(value))
    for collection in (article.get("tags"), project.get("keywords")):
        if isinstance(collection, Sequence) and not isinstance(collection, str):
            raw.extend(str(item) for item in collection)
    raw.extend(re.findall(r"[A-Za-z][A-Za-z0-9 .+-]{1,24}|[\u4e00-\u9fff]{2,8}", str(article.get("title") or "")))
    result = []
    for item in raw:
        term = item.strip().lower()
        if len(term) >= 2 and term not in result:
            result.append(term)
    return result


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    return conn


def _serialize_record(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    try:
        tags = json.loads(result.get("tags") or "[]")
    except (TypeError, json.JSONDecodeError):
        tags = []
    result["tags"] = [str(item) for item in tags] if isinstance(tags, list) else []
    return result
