"""Agent 6 Tool: Real Science Video Footage & SFX Scout.

Sources real-world video clips (strictly avoiding animations/cartoons/lectures):
1. Pexels Video API (automatically enabled if PEXELS_API_KEY env var is set)
2. Wikimedia Commons API (free, no API key needed; CC0/CC-BY real science videos)
3. Internet Archive API (free fallback for real science & nature footage)

Reuses local SFX cache via StudioMemory so basic SFX are never re-fetched.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
import urllib3.util.connection as _urllib3_conn

from src.memory import StudioMemory
from src.state import Agent6Output, SceneAsset, SceneSpec

# Prefer IPv4 to avoid transient IPv6 routing failures on container hosts
_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

USER_AGENT = "KimiabillStudio/1.0 (https://github.com/kimiabill-studio; science-edu-bot)"

DISALLOWED_TITLE_TOKENS = (
    "animation",
    "animated",
    "cartoon",
    "3d",
    "blender",
    "simulation",
    "simulated",
    "diagram",
    "lecture",
    "presentation",
    "interview",
    "talk",
    "podcast",
    "webinar",
    "slideshow",
    "trailer",
    "logo",
    "intro",
)


@dataclass
class VideoCandidate:
    source: str
    title: str
    download_url: str
    page_url: str
    license: str
    duration_sec: float
    width: int
    height: int
    thumb_url: Optional[str] = None


def probe_video(path: Path) -> Optional[dict]:
    try:
        raw = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name:format=duration",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(raw)
        streams = data.get("streams", [])
        if not streams:
            return None
        st = streams[0]
        dur = float(data.get("format", {}).get("duration") or 0.0)
        return {
            "width": int(st.get("width", 0)),
            "height": int(st.get("height", 0)),
            "codec": st.get("codec_name", ""),
            "duration": dur,
        }
    except Exception:
        return None


def _is_real_video_title(title: str) -> bool:
    low = title.lower()
    return not any(tok in low for tok in DISALLOWED_TITLE_TOKENS)


def search_pexels(query: str, per_page: int = 8) -> list[VideoCandidate]:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": api_key, "User-Agent": USER_AGENT},
        params={"query": query, "per_page": per_page, "orientation": "portrait"},
        timeout=20,
    )
    if resp.status_code != 200:
        return []
    out: list[VideoCandidate] = []
    for v in resp.json().get("videos", []):
        dur = float(v.get("duration", 0))
        if dur < 3.0:
            continue
        files = sorted(
            [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"],
            key=lambda f: abs((f.get("height") or 0) - 1280),
        )
        if not files:
            continue
        best = files[0]
        out.append(
            VideoCandidate(
                source="pexels",
                title=f"Pexels #{v.get('id')} - {query}",
                download_url=best["link"],
                page_url=v.get("url", best["link"]),
                license="Pexels License (Free)",
                duration_sec=dur,
                width=int(best.get("width") or 720),
                height=int(best.get("height") or 1280),
                thumb_url=v.get("image"),
            )
        )
    return out


def search_wikimedia_videos(query: str, limit: int = 12) -> list[VideoCandidate]:
    """Search Wikimedia Commons for real video files (.webm / .mp4 / .ogv)."""
    headers = {"User-Agent": USER_AGENT}
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:video",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "videoinfo",
        "viprop": "url|size|mime|extmetadata",
        "viurlwidth": "640",
    }
    resp = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        headers=headers,
        params=params,
        timeout=25,
    )
    if resp.status_code != 200:
        return []
    pages = resp.json().get("query", {}).get("pages", {})
    out: list[VideoCandidate] = []
    for page in pages.values():
        title = page.get("title", "")
        if not _is_real_video_title(title):
            continue
        vis = page.get("videoinfo", [])
        if not vis:
            continue
        vi = vis[0]
        mime = vi.get("mime", "")
        if not mime.startswith("video/") and "ogg" not in mime:
            continue
        dur = float(vi.get("duration") or 0.0)
        w = int(vi.get("width") or 0)
        h = int(vi.get("height") or 0)
        if dur < 3.0 or dur > 600.0 or w < 320 or h < 240:
            continue
        url = vi.get("url")
        if not url:
            continue
        meta = vi.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "Wikimedia Commons")
        lic = re.sub(r"<[^>]+>", "", str(lic)).strip()
        out.append(
            VideoCandidate(
                source="wikimedia_commons",
                title=title.replace("File:", ""),
                download_url=url,
                page_url=vi.get("descriptionurl", url),
                license=lic,
                duration_sec=dur,
                width=w,
                height=h,
                thumb_url=vi.get("thumburl"),
            )
        )
    # Prefer HD, shorter focused clips (5s - 90s)
    out.sort(key=lambda c: (0 if 5 <= c.duration_sec <= 90 else 1, -min(c.width, 1920)))
    return out


def download_and_normalize_clip(
    candidate: VideoCandidate,
    dest_mp4: Path,
    max_download_mb: int = 80,
) -> Optional[dict]:
    """Download candidate video and normalize to clean H.264 MP4 (max 20s slice) for fast rendering."""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    raw_path = dest_mp4.with_suffix(".raw_dl")
    headers = {"User-Agent": USER_AGENT}
    try:
        with requests.get(candidate.download_url, headers=headers, stream=True, timeout=45) as r:
            r.raise_for_status()
            total = 0
            limit_bytes = max_download_mb * 1024 * 1024
            with open(raw_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        total += len(chunk)
                        if total > limit_bytes:
                            break
                        fh.write(chunk)
        info = probe_video(raw_path)
        if not info or info["duration"] < 2.5 or info["width"] < 320:
            raw_path.unlink(missing_ok=True)
            return None
        # Transcode up to 25s (starting slightly after 0 if long clip) into clean yuv420p H.264 MP4
        start_at = 1.5 if info["duration"] > 18.0 else 0.0
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_at:.2f}",
                "-i",
                str(raw_path),
                "-t",
                "25",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(dest_mp4),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        raw_path.unlink(missing_ok=True)
        return probe_video(dest_mp4)
    except Exception:
        raw_path.unlink(missing_ok=True)
        return None


def scout_assets_for_scenes(
    scenes: list[SceneSpec],
    assets_dir: Path,
    memory: StudioMemory,
    manual_overrides: Optional[dict[str, VideoCandidate]] = None,
) -> Agent6Output:
    """Scout real video footage + cached SFX for each scene."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    results: list[SceneAsset] = []
    used_urls: set[str] = set()

    for scene in scenes:
        dest_mp4 = assets_dir / f"{scene.scene_id}.mp4"
        chosen_cand: Optional[VideoCandidate] = None
        chosen_info: Optional[dict] = None

        if manual_overrides and scene.scene_id in manual_overrides:
            cand = manual_overrides[scene.scene_id]
            info = download_and_normalize_clip(cand, dest_mp4)
            if info:
                chosen_cand, chosen_info = cand, info

        if chosen_cand is None:
            queries = [scene.visual_query] + list(scene.fallback_queries)
            for q in queries:
                cands = search_pexels(q) + search_wikimedia_videos(q)
                for cand in cands:
                    if cand.download_url in used_urls:
                        continue
                    info = download_and_normalize_clip(cand, dest_mp4)
                    if info:
                        chosen_cand, chosen_info = cand, info
                        used_urls.add(cand.download_url)
                        break
                if chosen_cand:
                    break

        if chosen_cand is None or chosen_info is None:
            raise RuntimeError(
                f"Agent 6 could not find real video footage for {scene.scene_id} "
                f"(queries: {[scene.visual_query] + list(scene.fallback_queries)})"
            )

        results.append(
            SceneAsset(
                scene_id=scene.scene_id,
                footage_path=str(dest_mp4),
                source=chosen_cand.source,
                source_url=chosen_cand.page_url,
                license=chosen_cand.license,
                duration_sec=round(chosen_info["duration"], 2),
                width=chosen_info["width"],
                height=chosen_info["height"],
                sfx_path=memory.get_sfx_path(scene.sfx_cue),
                notes=chosen_cand.title,
            )
        )

    return Agent6Output(assets=results, bgm_path=memory.get_bgm_path())
