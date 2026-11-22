"""Google Drive uploader for Kimiabill Studio episodes using the connected `gdrive` CLI."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional


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


def upload_episode_to_gdrive(
    episode_dir: Path,
    episode_name: str,
    root_folder_name: str = "Kimiabill_Studio",
) -> str:
    """Upload final video, script/state JSON logs, and real footage assets to Google Drive."""
    root_id = ensure_folder(root_folder_name)
    ep_folder_id = ensure_folder(episode_name, parent_id=root_id)
    logs_folder_id = ensure_folder("01_agent_logs", parent_id=ep_folder_id)
    assets_folder_id = ensure_folder("02_real_footage", parent_id=ep_folder_id)

    # Upload main files in episode root (FINAL mp4, thumbnail, contact sheet, episode_state.json)
    for pattern in ("FINAL_*.mp4", "THUMBNAIL_*.jpg", "*_contact_sheet.jpg", "episode_state.json"):
        for f in sorted(episode_dir.glob(pattern)):
            _run_gdrive(["mutate", "upload", str(f), "--parent", ep_folder_id])

    # Upload handoff / agent logs
    handoffs_dir = episode_dir / "handoffs"
    if handoffs_dir.exists():
        for f in sorted(handoffs_dir.glob("*.json")):
            _run_gdrive(["mutate", "upload", str(f), "--parent", logs_folder_id])

    # Upload downloaded real footage clips
    assets_dir = episode_dir / "assets"
    if assets_dir.exists():
        for f in sorted(assets_dir.glob("*.mp4")):
            _run_gdrive(["mutate", "upload", str(f), "--parent", assets_folder_id])

    url = _run_gdrive(["readonly", "url", ep_folder_id])
    return url.splitlines()[-1].strip()
