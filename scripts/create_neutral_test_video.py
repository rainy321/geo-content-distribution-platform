from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

import cv2
import numpy as np


WIDTH = 720
HEIGHT = 1280
FPS = 30
DURATION_SECONDS = 10


def create_video(output_path: Path) -> dict[str, object]:
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = None
    selected_codec = ""
    for codec in ("avc1", "H264", "mp4v"):
        candidate = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*codec),
            FPS,
            (WIDTH, HEIGHT),
        )
        if candidate.isOpened():
            writer = candidate
            selected_codec = codec
            break
        candidate.release()
    if writer is None:
        raise RuntimeError("当前 OpenCV 没有可用的 MP4 编码器")

    total_frames = FPS * DURATION_SECONDS
    y_axis = np.linspace(0, 1, HEIGHT, dtype=np.float32)[:, None]
    x_axis = np.linspace(0, 1, WIDTH, dtype=np.float32)[None, :]
    try:
        for frame_index in range(total_frames):
            phase = frame_index / total_frames
            frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            frame[:, :, 0] = np.clip(38 + 45 * y_axis + 18 * x_axis, 0, 255)
            frame[:, :, 1] = np.clip(73 + 85 * x_axis + 16 * y_axis, 0, 255)
            frame[:, :, 2] = np.clip(82 + 72 * (1 - y_axis), 0, 255)

            center_x = int(WIDTH * (0.5 + 0.28 * math.sin(phase * math.tau)))
            center_y = int(HEIGHT * (0.34 + 0.12 * math.cos(phase * math.tau)))
            cv2.circle(frame, (center_x, center_y), 150, (101, 211, 200), -1)
            cv2.circle(
                frame,
                (WIDTH - center_x // 2, int(HEIGHT * 0.72)),
                210,
                (44, 116, 126),
                8,
            )
            cv2.rectangle(frame, (70, 790), (650, 1080), (238, 245, 244), -1)
            cv2.putText(
                frame,
                "GEO CONTENT FLOW",
                (112, 885),
                cv2.FONT_HERSHEY_DUPLEX,
                1.25,
                (19, 57, 62),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                "Neutral platform test material",
                (114, 946),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (41, 91, 96),
                2,
                cv2.LINE_AA,
            )
            progress_end = 114 + int(470 * phase)
            cv2.line(frame, (114, 1015), (584, 1015), (185, 207, 204), 8)
            cv2.line(frame, (114, 1015), (progress_end, 1015), (37, 171, 161), 8)
            writer.write(frame)
    finally:
        writer.release()

    capture = cv2.VideoCapture(str(output_path))
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        measured_fps = float(capture.get(cv2.CAP_PROP_FPS))
        measured_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        measured_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        codec_value = int(capture.get(cv2.CAP_PROP_FOURCC))
    finally:
        capture.release()
    if frame_count <= 0 or measured_fps <= 0:
        raise RuntimeError("生成的视频无法重新读取")

    return {
        "path": str(output_path),
        "codec": "".join(
            chr((codec_value >> (8 * index)) & 0xFF) for index in range(4)
        ).strip() or selected_codec,
        "width": measured_width,
        "height": measured_height,
        "fps": round(measured_fps, 2),
        "duration_seconds": round(frame_count / measured_fps, 2),
        "size_bytes": output_path.stat().st_size,
        "contains_audio": False,
    }


def register_video(database_path: Path, metadata: dict[str, object]) -> int:
    database_path = database_path.expanduser().resolve()
    relative_path = Path(str(metadata["path"])).name
    with sqlite3.connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id FROM file_records WHERE file_path = ?",
            (relative_path,),
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = connection.execute(
            """
            INSERT INTO file_records (
                filename, filesize, file_path, media_type, tags, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "GEO-P2-中性链路测试视频.mp4",
                float(metadata["size_bytes"]),
                relative_path,
                "video",
                json.dumps(["GEO", "链路测试", "中性素材"], ensure_ascii=False),
                "generated",
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成中性 P2 平台链路测试视频。")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("videoFile/geo-p2-neutral-test.mp4"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("db/database.db"),
        help="将素材幂等登记到本地素材库。",
    )
    args = parser.parse_args()
    metadata = create_video(args.output)
    metadata["material_id"] = register_video(args.database, metadata)
    for key, value in metadata.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
