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
    "music video",
    "album",
    "karaoke",
    "gameplay",
    "screen recording",
    "screencast",
    "wikipedia",
    "wp25",
    "wikimedia",
    "wikimania",
    "anthem",
    "caption",
    "billboard",
    "commercial",
    "promo",
    "donation",
    "scp-",
    "tutorial",
    "ukulele",
    "guitar",
    "horseback",
    "flamingo",
    "cctv",
    "wallet theft",
    "brush pack",
    "painter",
    "hubblecast",
    "esocast",
    "sora in action",
    "graduation",
    "from farm to plate",
    "recipe",
    "street light",
    "full moon",
)

# Family-safety blocklist (checked against title + categories + description metadata)
UNSAFE_TOKENS = (
    "nude", "nudity", "naked", "topless", "nsfw", "porn", "erotic", "sex", "sexual",
    "genital", "penis", "vagina", "breast", "nipple", "masturbat", "orgasm", "fetish",
    "bdsm", "bondage", "lingerie", "bikini", "stripper", "striptease", "intercourse",
    "condom", "explicit", "adult film", "xxx", "gore", "corpse", "dead body", "autopsy",
    "beheading", "execution", "suicide", "murder", "torture", "gun", "weapon", "war crime",
    "drug use", "injecting", "surgery", "pregnan", "childbirth",
    "dancing", "pole dance", "fashion show", "model posing",
)


def _is_family_safe_text(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    blob = re.sub(r"<[^>]+>", " ", blob)
    return not any(tok in blob for tok in UNSAFE_TOKENS)


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


def search_pexels(query: str, per_page: int = 8, orientation: Optional[str] = "portrait") -> list[VideoCandidate]:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return []
    params: dict[str, Any] = {"query": query, "per_page": per_page}
    if orientation:
        params["orientation"] = orientation
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": api_key, "User-Agent": USER_AGENT},
        params=params,
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
            key=lambda f: abs((f.get("height") or 0) - 1080),
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
        meta_text = " ".join(
            str(meta.get(k, {}).get("value", ""))
            for k in ("Categories", "ImageDescription", "ObjectName", "Restrictions")
        )
        if not _is_family_safe_text(title, meta_text):
            continue
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


def _download_raw_clip(
    candidate: VideoCandidate,
    raw_path: Path,
    max_download_mb: int = 32,
) -> Optional[dict]:
    """Download candidate video up to max_download_mb and verify it is decodable before screening."""
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    try:
        with requests.get(candidate.download_url, headers=headers, stream=True, timeout=25) as r:
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
        return info
    except Exception:
        raw_path.unlink(missing_ok=True)
        return None


def _transcode_to_mp4(raw_path: Path, info: dict, dest_mp4: Path) -> Optional[dict]:
    """Transcode approved raw clip (up to 25s slice) into clean yuv420p H.264 MP4."""
    try:
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
        return probe_video(dest_mp4)
    except Exception:
        return None
    finally:
        raw_path.unlink(missing_ok=True)


def download_and_normalize_clip(
    candidate: VideoCandidate,
    dest_mp4: Path,
    max_download_mb: int = 32,
) -> Optional[dict]:
    """Download candidate video and normalize to clean H.264 MP4 (max 25s slice) for fast rendering."""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    raw_path = dest_mp4.with_suffix(".raw_dl")
    info = _download_raw_clip(candidate, raw_path, max_download_mb=max_download_mb)
    if not info:
        return None
    return _transcode_to_mp4(raw_path, info, dest_mp4)


def _build_frame_strip(clip: Path, out_jpg: Path) -> Optional[Path]:
    """Grab 3 frames (15% / 50% / 85%) of a clip and tile them horizontally for vision screening."""
    try:
        from PIL import Image

        info = probe_video(clip)
        if not info:
            return None
        dur = max(1.0, float(info["duration"]))
        frames = []
        for i, frac in enumerate((0.15, 0.5, 0.85)):
            fp = out_jpg.with_name(f"{out_jpg.stem}_f{i}.jpg")
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{dur * frac:.2f}", "-i", str(clip), "-frames:v", "1",
                 "-vf", "scale=-2:360", "-q:v", "4", str(fp)],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if fp.exists():
                frames.append(Image.open(fp).convert("RGB"))
                fp.unlink(missing_ok=True)
        if not frames:
            return None
        total_w = sum(f.width for f in frames) + 8 * (len(frames) - 1)
        strip = Image.new("RGB", (total_w, max(f.height for f in frames)), (0, 0, 0))
        x = 0
        for f in frames:
            strip.paste(f, (x, 0))
            x += f.width + 8
        strip.save(out_jpg, quality=85)
        return out_jpg
    except Exception:
        return None


def vision_screen_clip(clip: Path, scene: SceneSpec) -> tuple[bool, bool, str]:
    """Ask Gemini Vision whether a clip is family-safe, relevant, and free of watermarks/UI overlays.

    Returns (accepted, safe_but_weak, reason). If vision screening is unavailable, falls back to accepting
    (metadata blocklist has already been applied).
    """
    if os.environ.get("ASSET_VISION_SCREEN", "true").lower() != "true":
        return True, True, "vision screening disabled"
    if not os.environ.get("GEMINI_API_KEY"):
        return True, True, "no GEMINI_API_KEY; skipped vision screening"
    strip = _build_frame_strip(clip, clip.with_name(f"{clip.stem}_screen.jpg"))
    if strip is None:
        return False, False, "could not extract frames"
    try:
        from pydantic import BaseModel, Field

        from src.providers import call_structured_agent

        class ClipScreen(BaseModel):
            family_safe: bool = Field(description="False if ANY nudity, sexual/suggestive content, gore, violence, or otherwise inappropriate imagery for a kids/teen science channel")
            relevance_score: float = Field(ge=0, le=10, description="How well the frames visually match the scene narration and visual query")
            has_watermark_or_ui: bool = Field(description="True if frames show music-player/album art, app UI, big logos, text slides, or channel watermarks")
            is_real_footage: bool = Field(description="True if real camera footage (not cartoon/animation/slide)")
            is_scientific_visualization: bool = Field(
                default=False,
                description="True if it is a serious scientific visualization (molecular/protein model, microscopy, "
                "research simulation render) - NOT a cartoon, fractal screensaver, or text slide",
            )
            reason: str = ""

        verdict = call_structured_agent(
            provider="auto",
            model=os.environ.get("GEMINI_SCREEN_MODEL", "gemini-3.5-flash-lite"),
            system_prompt=(
                "You are a strict visual safety & relevance screener for 'Kimiabill', a family-friendly "
                "Indonesian pure-science short-video channel watched by students. The attached image shows "
                "3 frames sampled from a candidate stock clip. Judge it strictly."
            ),
            user_payload={
                "scene_narration": scene.narration,
                "visual_query": scene.visual_query,
            },
            response_model=ClipScreen,
            handoff_path=None,
            image_paths=[strip],
        )
    except Exception as exc:  # noqa: BLE001
        return False, False, f"vision screening error (rejected for safety): {exc}"
    finally:
        strip.unlink(missing_ok=True)

    min_rel = float(os.environ.get("ASSET_MIN_RELEVANCE", "5"))
    sci_ok = verdict.is_scientific_visualization and verdict.relevance_score >= 6.5
    clean = verdict.family_safe and (verdict.is_real_footage or sci_ok) and not verdict.has_watermark_or_ui
    ok = clean and verdict.relevance_score >= min_rel
    return ok, clean, (
        f"safe={verdict.family_safe} real={verdict.is_real_footage} sci={verdict.is_scientific_visualization} "
        f"ui={verdict.has_watermark_or_ui} rel={verdict.relevance_score} {verdict.reason}"
    )


def _next_free_path(assets_dir: Path, scene_id: str) -> Path:
    """Versioned filenames so re-scouting never overwrites footage used by earlier render versions."""
    base = assets_dir / f"{scene_id}.mp4"
    if not base.exists():
        return base
    k = 2
    while (assets_dir / f"{scene_id}_alt{k}.mp4").exists():
        k += 1
    return assets_dir / f"{scene_id}_alt{k}.mp4"


def scout_assets_for_scenes(
    scenes: list[SceneSpec],
    assets_dir: Path,
    memory: StudioMemory,
    manual_overrides: Optional[dict[str, VideoCandidate]] = None,
    exclude_urls: Optional[set[str]] = None,
    existing_assets: Optional[dict[str, SceneAsset]] = None,
    target_scene_ids: Optional[set[str]] = None,
    tried_urls_out: Optional[set[str]] = None,
) -> Agent6Output:
    """Scout real video footage + cached SFX for each scene (supports targeted re-scouting)."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    results: list[SceneAsset] = []
    used_urls: set[str] = set(exclude_urls or ())
    used_stems: set[str] = set()
    max_screens = int(os.environ.get("ASSET_MAX_SCREENS_PER_SCENE", "6"))

    for scene in scenes:
        if target_scene_ids is not None and scene.scene_id not in target_scene_ids and existing_assets and scene.scene_id in existing_assets:
            results.append(existing_assets[scene.scene_id])
            used_urls.add(existing_assets[scene.scene_id].source_url)
            continue

        dest_mp4 = _next_free_path(assets_dir, scene.scene_id)
        chosen_cand: Optional[VideoCandidate] = None
        chosen_info: Optional[dict] = None
        fallback_cand: Optional[VideoCandidate] = None
        fallback_info: Optional[dict] = None
        fallback_raw: Optional[Path] = None
        screens_done = 0

        if manual_overrides and scene.scene_id in manual_overrides:
            cand = manual_overrides[scene.scene_id]
            info = download_and_normalize_clip(cand, dest_mp4)
            if info:
                chosen_cand, chosen_info = cand, info

        if chosen_cand is None:
            raw_queries = [scene.visual_query] + list(scene.fallback_queries)
            stop_words = {
                "the", "and", "with", "from", "into", "that", "this", "for", "are", "was",
                "in", "on", "of", "to", "a", "an", "at", "by", "or", "as", "is", "be",
                "video", "clip", "footage", "close", "closeup", "shot", "view", "real",
                "screen", "monitor", "computer", "dark", "background", "human", "person", "hand",
            }
            broadened: list[str] = []
            for q in raw_queries:
                words = [w for w in re.findall(r"[A-Za-z]+", q.lower()) if len(w) > 3 and w not in stop_words]
                if len(words) >= 2:
                    broadened.append(" ".join(words[:2]))
                    broadened.append(" ".join(words[-2:]))
            generic_science_fallbacks = [
                "chemistry laboratory experiment",
                "chemical reaction beaker",
                "optical microscope cells",
                "physics laboratory experiment",
                "crystal growth microscope",
                "science laboratory glassware",
            ]
            seen_q: set[str] = set()
            queries: list[str] = []
            for q in [*raw_queries, *broadened, *generic_science_fallbacks]:
                q_norm = q.strip()
                if q_norm and q_norm.lower() not in seen_q:
                    seen_q.add(q_norm.lower())
                    queries.append(q_norm)

            for q in queries:
                pexels_cands = search_pexels(q, orientation="portrait")
                if len(pexels_cands) < 3:
                    pexels_cands += search_pexels(q, orientation="landscape")
                is_generic_q = q in generic_science_fallbacks
                wiki_cands = search_wikimedia_videos(q) if (len(pexels_cands) < 2 or is_generic_q) else []
                if wiki_cands and not is_generic_q:
                    q_terms = [w for w in re.findall(r"[a-z]+", q.lower()) if len(w) > 3 and w not in stop_words]
                    if q_terms:
                        wiki_cands = [
                            wc for wc in wiki_cands
                            if any(t in wc.title.lower() for t in q_terms)
                        ]
                cands = pexels_cands + wiki_cands
                # Prefer Pexels & >=720p clips first
                cands.sort(key=lambda c: (0 if c.source == "pexels" else 1, 0 if c.width >= 720 else 1, -min(c.width, 1920)))
                per_query_screens = 0
                for cand in cands:
                    stem = cand.title.lower()[:32]
                    if cand.download_url in used_urls or cand.page_url in used_urls or stem in used_stems:
                        continue
                    if screens_done >= max_screens or per_query_screens >= 2:
                        break
                    raw_path = dest_mp4.with_suffix(".raw_dl")
                    raw_info = _download_raw_clip(cand, raw_path)
                    used_urls.add(cand.download_url)
                    used_urls.add(cand.page_url)
                    used_stems.add(stem)
                    if not raw_info:
                        continue
                    screens_done += 1
                    per_query_screens += 1
                    ok, clean, reason = vision_screen_clip(raw_path, scene)
                    print(f"[asset_scout] {scene.scene_id} screen '{cand.title[:60]}': ok={ok} ({reason[:160]})")
                    if ok:
                        chosen_info = _transcode_to_mp4(raw_path, raw_info, dest_mp4)
                        if chosen_info:
                            chosen_cand = cand
                            if fallback_raw is not None:
                                fallback_raw.unlink(missing_ok=True)
                                fallback_raw = None
                            break
                        continue
                    if clean and fallback_cand is None:
                        fb_raw = dest_mp4.with_name(f"{dest_mp4.stem}_fb.raw")
                        raw_path.replace(fb_raw)
                        fallback_cand, fallback_info, fallback_raw = cand, raw_info, fb_raw
                    else:
                        raw_path.unlink(missing_ok=True)
                if chosen_cand or screens_done >= max_screens:
                    break

            if chosen_cand is None and fallback_cand is not None and fallback_raw is not None:
                norm_info = _transcode_to_mp4(fallback_raw, fallback_info or {}, dest_mp4)
                if norm_info:
                    chosen_cand, chosen_info = fallback_cand, norm_info
                    print(f"[asset_scout] {scene.scene_id}: memakai fallback aman (relevansi rendah)")

        if fallback_raw is not None and fallback_raw.exists():
            fallback_raw.unlink(missing_ok=True)

        if (chosen_cand is None or chosen_info is None) and existing_assets and scene.scene_id in existing_assets:
            # Re-scout found nothing better/safer: keep the previous footage instead of crashing the episode
            print(f"[asset_scout] {scene.scene_id}: tidak ada pengganti yang lolos screening; memakai footage sebelumnya")
            results.append(existing_assets[scene.scene_id])
            continue

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

    if tried_urls_out is not None:
        tried_urls_out.update(used_urls)
    return Agent6Output(assets=results, bgm_path=memory.get_bgm_path())
