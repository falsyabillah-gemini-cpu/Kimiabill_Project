"""Google Drive uploader + email notifier for Kimiabill Studio episodes.

Supports two backends automatically:
1. Google Apps Script Web App (`GDRIVE_WEBHOOK_URL`) — works on GitHub Actions, VPS/Docker,
   and local machines without Google Cloud Console setup; uploads directly to your personal
   Google Drive and sends email notification via Gmail (`MailApp`).
2. Local `gdrive` CLI fallback (when running inside Antigravity VM).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import requests

DEFAULT_NOTIFY_EMAIL = "researchfalsyabillah@gmail.com"


def _run_gdrive(args: list[str]) -> str:
    proc = subprocess.run(["gdrive", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gdrive {' '.join(args)} failed: {proc.stderr or proc.stdout}")
    return proc.stdout.strip()


def ensure_folder(name: str, parent_id: Optional[str] = None) -> str:
    search_args = ["readonly", "search", "--name-exact", name, "--type", "folder", "--json"]
    if parent_id:
        search_args.extend(["--parent-id", parent_id])
    raw = _run_gdrive(search_args)
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = items.get("files", [])
        if items:
            return items[0]["id"]
    except json.JSONDecodeError:
        pass

    mkdir_args = ["mutate", "mkdir", name]
    if parent_id:
        mkdir_args.extend(["--parent", parent_id])
    _run_gdrive(mkdir_args)

    raw_after = _run_gdrive(search_args)
    items_after = json.loads(raw_after)
    if isinstance(items_after, dict):
        items_after = items_after.get("files", [])
    if not items_after:
        raise RuntimeError(f"Created folder '{name}' but could not locate its ID.")
    return items_after[0]["id"]


def _build_captions_summary(episode_dir: Path) -> tuple[str, str]:
    """Read Agent 3/7 handoffs to build a clean title and ready-to-copy captions text."""
    title = episode_dir.name
    lines: list[str] = []

    a3_path = episode_dir / "handoffs" / "agent3_script.json"
    if a3_path.exists():
        try:
            a3 = json.loads(a3_path.read_text(encoding="utf-8"))
            title = a3.get("title") or title
        except Exception:
            pass

    a7_path = episode_dir / "handoffs" / "agent7_package.json"
    if a7_path.exists():
        try:
            a7 = json.loads(a7_path.read_text(encoding="utf-8"))
            for copy in a7.get("platform_copies", []):
                plat = str(copy.get("platform", "")).upper().replace("_", " ")
                tags = " ".join(copy.get("hashtags", []))
                lines.append(f"[{plat}]")
                lines.append(f"Judul: {copy.get('title', '')}")
                lines.append(f"Caption:\n{copy.get('caption', '')}")
                if tags:
                    lines.append(f"Hashtag: {tags}")
                if copy.get("pinned_comment"):
                    lines.append(f"Pinned Comment: {copy.get('pinned_comment')}")
                lines.append("")
        except Exception:
            pass

    return title, "\n".join(lines).strip()


def _normalize_webhook_url(raw: str) -> str:
    """Accept full https://.../exec URL, scheme-less script.google.com URL, or raw Deployment ID (AKfy...)."""
    url = raw.strip().strip('"').strip("'")
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("script.google.com/"):
        return f"https://{url}"
    if "/" not in url and len(url) >= 20:
        # User pasted the Google Apps Script Deployment ID (e.g. AKfycb...)
        return f"https://script.google.com/macros/s/{url}/exec"
    return f"https://{url}"


def _post_webhook(webhook_url: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    webhook_url = _normalize_webhook_url(webhook_url)
    token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
    if token:
        payload = {**payload, "token": token}
    resp = requests.post(webhook_url, json=payload, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Google Drive webhook error: {data}")
    return data


def _extract_youtube_copy(episode_dir: Path, fallback_title: str) -> dict[str, Any]:
    """Extract YouTube Shorts title, description (including references), and tags."""
    refs: list[str] = []
    state_path = episode_dir / "episode_state.json"
    if state_path.exists():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            refs = (st.get("render_plan") or {}).get("references") or []
        except Exception:
            pass
    ref_block = ""
    if refs:
        ref_lines = "\n".join(f"- {r}" for r in refs)
        ref_block = f"\n\nReferensi & Sumber:\n{ref_lines}"

    a7_path = episode_dir / "handoffs" / "agent7_package.json"
    if a7_path.exists():
        try:
            a7 = json.loads(a7_path.read_text(encoding="utf-8"))
            for copy in a7.get("platform_copies", []):
                if copy.get("platform") == "youtube_shorts":
                    tags = [str(t).lstrip("#") for t in copy.get("hashtags", [])]
                    tag_str = " ".join(copy.get("hashtags", []))
                    desc = f"{copy.get('caption', '')}{ref_block}\n\n{tag_str}".strip()
                    return {
                        "title": copy.get("title") or fallback_title,
                        "description": desc,
                        "tags": tags or ["sains", "kimiabill", "shorts"],
                    }
        except Exception:
            pass
    return {
        "title": fallback_title,
        "description": f"{fallback_title}{ref_block}\n\n#sains #kimiabill #shorts",
        "tags": ["sains", "kimiabill", "shorts"],
    }


def _upload_via_webhook(
    episode_dir: Path,
    episode_name: str,
    root_folder_name: str,
    webhook_url: str,
    allow_youtube: bool = True,
    qa_summary: str = "",
) -> str:
    """Upload final video, thumbnail, and caption sheet via Google Apps Script webhook + YouTube + email notify."""
    init_res = _post_webhook(
        webhook_url,
        {
            "action": "init_episode",
            "root_folder": root_folder_name,
            "episode_name": episode_name,
        },
        timeout=60,
    )
    folder_id = init_res["folder_id"]
    folder_url = init_res["folder_url"]

    title, captions_text = _build_captions_summary(episode_dir)
    if captions_text:
        caption_file = episode_dir / "CAPTION_SIAP_UPLOAD.txt"
        caption_file.write_text(f"Judul: {title}\n\n{captions_text}\n", encoding="utf-8")

    video_file_id: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_file_id: Optional[str] = None
    thumbnail_url: Optional[str] = None

    upload_patterns = (
        "FINAL_*.mp4",
        "THUMBNAIL_*.jpg",
        "CAPTION_SIAP_UPLOAD.txt",
        "*_contact_sheet.jpg",
    )
    for pattern in upload_patterns:
        for f in sorted(episode_dir.glob(pattern)):
            # Skip files > 36 MB for base64 Apps Script 50 MB payload cap
            if f.stat().st_size > 36 * 1024 * 1024:
                print(f"[gdrive] Skipping {f.name} (>36 MB Apps Script limit)")
                continue
            mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            res = _post_webhook(
                webhook_url,
                {
                    "action": "upload_file",
                    "folder_id": folder_id,
                    "filename": f.name,
                    "mime_type": mime,
                    "content_base64": b64,
                },
                timeout=240,
            )
            if f.name.startswith("FINAL_") and f.suffix == ".mp4":
                video_file_id = res.get("file_id")
                video_url = res.get("file_url")
            elif f.name.startswith("THUMBNAIL_"):
                thumbnail_file_id = res.get("file_id")
                thumbnail_url = res.get("file_url")

    # Auto-upload to YouTube Shorts via Apps Script ONLY when QA gate passed & PUBLISH_MODE is draft/public
    publish_mode = os.environ.get("PUBLISH_MODE", "draft").strip().lower()
    youtube_url: Optional[str] = None
    youtube_privacy: Optional[str] = None
    youtube_note: Optional[str] = None

    if not allow_youtube:
        youtube_note = f"DITAHAN OLEH AGENT 5 QA AUDITOR (tidak di-upload ke YouTube): {qa_summary}"
        print(f"[youtube] {youtube_note}")
    elif video_file_id and publish_mode in ("draft", "public"):
        yt_copy = _extract_youtube_copy(episode_dir, title)
        privacy = "public" if publish_mode == "public" else "unlisted"
        try:
            yt_res = _post_webhook(
                webhook_url,
                {
                    "action": "upload_youtube",
                    "video_file_id": video_file_id,
                    "thumbnail_file_id": thumbnail_file_id,
                    "title": yt_copy["title"],
                    "description": yt_copy["description"],
                    "tags": yt_copy["tags"],
                    "privacy": privacy,
                },
                timeout=240,
            )
            youtube_url = yt_res.get("youtube_url")
            youtube_privacy = yt_res.get("privacy", privacy)
            print(f"[youtube] Berhasil upload ke YouTube Shorts ({youtube_privacy}): {youtube_url}")
        except Exception as exc:
            youtube_note = f"Belum ter-upload otomatis ({exc})"
            print(f"[youtube] Peringatan: gagal upload YouTube via webhook ({exc})")
    elif publish_mode == "dry_run":
        youtube_note = "Mode dry_run (hanya simpan ke Drive; ubah publish_mode ke draft/public untuk auto-upload YouTube)."

    notify_to = os.environ.get("NOTIFY_EMAIL_TO", DEFAULT_NOTIFY_EMAIL).strip() or DEFAULT_NOTIFY_EMAIL
    email_preview = captions_text[:3200]
    if qa_summary:
        email_preview = f"[HASIL AUDIT QA AGENT 5]\n{qa_summary}\n\n{email_preview}".strip()
    try:
        _post_webhook(
            webhook_url,
            {
                "action": "notify_email",
                "to": notify_to,
                "episode_name": episode_name,
                "title": title,
                "folder_url": folder_url,
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
                "youtube_url": youtube_url,
                "youtube_privacy": youtube_privacy,
                "youtube_note": youtube_note,
                "captions_preview": email_preview,
            },
            timeout=60,
        )
        print(f"[gdrive] Notifikasi email terkirim ke {notify_to}")
    except Exception as exc:
        print(f"[gdrive] Peringatan: upload Drive sukses, namun gagal kirim email ({exc})")

    return folder_url


def can_upload_to_gdrive() -> bool:
    return bool(os.environ.get("GDRIVE_WEBHOOK_URL", "").strip() or shutil.which("gdrive"))


def upload_episode_to_gdrive(
    episode_dir: Path,
    episode_name: str,
    root_folder_name: Optional[str] = None,
    allow_youtube: bool = True,
    qa_summary: str = "",
) -> str:
    """Upload final video, thumbnail, and logs to Google Drive (webhook or gdrive CLI)."""
    root_name = root_folder_name or os.environ.get("GDRIVE_ROOT_FOLDER", "Kimiabill_Studio")
    webhook_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if webhook_url:
        return _upload_via_webhook(
            episode_dir,
            episode_name,
            root_name,
            webhook_url,
            allow_youtube=allow_youtube,
            qa_summary=qa_summary,
        )

    root_id = ensure_folder(root_name)
    ep_folder_id = ensure_folder(episode_name, parent_id=root_id)
    logs_folder_id = ensure_folder("01_agent_logs", parent_id=ep_folder_id)
    assets_folder_id = ensure_folder("02_real_footage", parent_id=ep_folder_id)

    for pattern in ("FINAL_*.mp4", "THUMBNAIL_*.jpg", "*_contact_sheet.jpg", "episode_state.json"):
        for f in sorted(episode_dir.glob(pattern)):
            _run_gdrive(["mutate", "upload", str(f), "--parent", ep_folder_id])

    handoffs_dir = episode_dir / "handoffs"
    if handoffs_dir.exists():
        for f in sorted(handoffs_dir.glob("*.json")):
            _run_gdrive(["mutate", "upload", str(f), "--parent", logs_folder_id])

    assets_dir = episode_dir / "assets"
    if assets_dir.exists():
        for f in sorted(assets_dir.glob("*.mp4")):
            _run_gdrive(["mutate", "upload", str(f), "--parent", assets_folder_id])

    url = _run_gdrive(["readonly", "url", ep_folder_id])
    return url.splitlines()[-1].strip()
