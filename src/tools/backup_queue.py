"""Standby Backup Video Queue (`data/backup_queue.json`).

Stores verified `unlisted` YouTube Shorts + Google Drive drafts held in reserve
so that if scheduled daily production fails or the owner rejects an active draft,
the pipeline can automatically promote a ready-to-publish backup episode.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKUP_QUEUE_PATH = BASE_DIR / "data" / "backup_queue.json"


def _queue_path(base_dir: Path = BASE_DIR) -> Path:
    return base_dir / "data" / "backup_queue.json"


def load_backup_queue(base_dir: Path = BASE_DIR) -> list[dict[str, Any]]:
    path = _queue_path(base_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def save_backup_queue(items: list[dict[str, Any]], base_dir: Path = BASE_DIR) -> None:
    path = _queue_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items[-60:], ensure_ascii=False, indent=2), encoding="utf-8")


def peek_backup_count(base_dir: Path = BASE_DIR) -> int:
    return len(load_backup_queue(base_dir))


def push_backup_video(
    record: dict[str, Any] | Any,
    *,
    reason: str = "saved_manual",
    base_dir: Path = BASE_DIR,
) -> dict[str, Any]:
    """Push an unlisted episode draft onto the FIFO standby queue (deduped by episode_id)."""
    data = record.model_dump() if hasattr(record, "model_dump") else dict(record)
    ep_id = str(data.get("episode_id") or "").strip()
    if not ep_id:
        raise ValueError("Cannot queue backup video without episode_id")
    if not data.get("youtube_video_id") and not data.get("drive_url") and not data.get("youtube_url"):
        raise ValueError(f"Backup video '{ep_id}' lacks YouTube or Drive reference")

    item = {
        "episode_id": ep_id,
        "topic_title": str(data.get("topic_title") or ep_id),
        "topic_slug": str(data.get("topic_slug") or ep_id),
        "source_url": data.get("source_url"),
        "keywords": list(data.get("keywords") or []),
        "topic_bucket": str(data.get("topic_bucket") or "everyday"),
        "discipline": str(data.get("discipline") or "kimia"),
        "section": str(data.get("section") or "REGULER"),
        "duration_sec": float(data.get("duration_sec") or 0.0),
        "references": list(data.get("references") or []),
        "youtube_video_id": data.get("youtube_video_id"),
        "youtube_url": data.get("youtube_url"),
        "drive_url": data.get("drive_url"),
        "artifact_path_or_url": data.get("artifact_path_or_url"),
        "qa_summary": str(data.get("qa_summary") or "Video Cadangan Lolos QA"),
        "pinned_comment": data.get("pinned_comment"),
        "source_reason": reason,
        "queued_at": datetime.now(WIB).isoformat(timespec="seconds"),
    }
    items = [x for x in load_backup_queue(base_dir) if str(x.get("episode_id") or "") != ep_id]
    items.append(item)
    save_backup_queue(items, base_dir)
    return item


def pop_next_backup_video(base_dir: Path = BASE_DIR) -> Optional[dict[str, Any]]:
    """Pop the oldest valid standby backup video (FIFO) from `data/backup_queue.json`."""
    items = load_backup_queue(base_dir)
    while items:
        cand = items.pop(0)
        if isinstance(cand, dict) and (cand.get("youtube_video_id") or cand.get("youtube_url")):
            save_backup_queue(items, base_dir)
            return cand
    save_backup_queue(items, base_dir)
    return None
