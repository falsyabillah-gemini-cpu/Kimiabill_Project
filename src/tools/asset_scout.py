"""Agent 6 Tool: Real Science Video Footage & SFX Scout.

Sources real-world video clips (strictly avoiding animations/cartoons/lectures):
1. Pexels Video API (automatically enabled if PEXELS_API_KEY env var is set)
2. Pixabay Video API (automatically enabled if PIXABAY_API_KEY env var is set; free & active)
3. Wikimedia Commons API (free, no API key needed; CC0/CC-BY real science & kitchen videos)
4. NASA Images & Video API (free, no API key needed; Public Domain physics/earth/science HD clips)

Reuses local SFX cache via StudioMemory so basic SFX are never re-fetched.
"""
from __future__ import annotations

import base64
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
    "slide",
    "trailer",
    "logo",
    "intro",
    "outro",
    "bumper",
    "university",
    "pennstate",
    "life in the lab",
    "seminar",
    "arduino",
    "demo",
    "department",
    "institute",
    "faculty",
    "course",
    "hyrax",
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
    "carpentry",
    "woodworking",
    "screw pump",
    "grouted connection",
    "primordial germ cells",
    "astronaut moments",
    "nogizaka",
)

# Family-safety blocklist (checked against title + categories + description metadata)
UNSAFE_TOKENS = (
    "nude", "nudity", "naked", "topless", "nsfw", "porn", "erotic", "sex", "sexual",
    "genital", "penis", "vagina", "breast", "nipple", "masturbat", "orgasm", "fetish",
    "bdsm", "bondage", "lingerie", "bikini", "stripper", "striptease", "intercourse",
    "condom", "explicit", "adult film", "xxx", "gore", "corpse", "dead body", "autopsy",
    "beheading", "execution", "suicide", "murder", "shoot", "shooting", "knife-wielding",
    "suspect", "gun", "weapon", "war crime",
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
    ping_url: Optional[str] = None


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


_NON_FILM_QUERY_TOKENS: frozenset[str] = frozenset({
    "animation", "animated", "visualization", "diagram", "cartoon", "3d", "blender",
    "illustration", "cgi", "render", "rendered", "graphics", "graphic", "infographic",
    "closeup", "close-up", "macro", "shot", "view", "setting",
})


def _clean_stock_query(query: str) -> str:
    """Strip CGI/animation/diagram modifiers so stock video APIs return genuine real-camera footage."""
    words = [w for w in re.findall(r"[A-Za-z0-9-]+", (query or "").strip()) if w.lower() not in _NON_FILM_QUERY_TOKENS]
    cleaned = " ".join(words).strip()
    return cleaned or " ".join((query or "").split()[:3]).strip()


def _extract_pexels_slug(page_url: str, fallback: str) -> str:
    """Extract human-readable descriptive slug from Pexels video/photo URL (e.g. 'woman-drinking-water-1234')."""
    m = re.search(r"/(?:video|photo)/([a-z0-9-]+?)(?:-\d+)?/?$", (page_url or "").lower())
    if m and m.group(1):
        return m.group(1).replace("-", " ").strip()
    return fallback


def search_pexels(query: str, per_page: int = 8, orientation: Optional[str] = "portrait") -> list[VideoCandidate]:
    api_key = _resolve_pexels_api_key()
    if not api_key:
        return []
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    params: dict[str, Any] = {"query": q_clean, "per_page": per_page}
    if orientation:
        params["orientation"] = orientation
    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
            params=params,
            timeout=20,
        )
    except requests.RequestException:
        return []
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
        page_url = v.get("url", best["link"])
        slug_words = _extract_pexels_slug(page_url, q_clean)
        if not _is_real_video_title(slug_words) or not _is_family_safe_text(slug_words):
            continue
        out.append(
            VideoCandidate(
                source="pexels",
                title=f"Pexels #{v.get('id')} - {slug_words}"[:120],
                download_url=best["link"],
                page_url=page_url,
                license="Pexels License (Free)",
                duration_sec=dur,
                width=int(best.get("width") or 720),
                height=int(best.get("height") or 1280),
                thumb_url=v.get("image"),
            )
        )
    return out


def search_pexels_photos(query: str, per_page: int = 6) -> list[VideoCandidate]:
    """Search Pexels Real Photography API (same PEXELS_API_KEY, 100% real camera & monetization-safe) for Ken Burns fallback."""
    api_key = _resolve_pexels_api_key()
    if not api_key:
        return []
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    out: list[VideoCandidate] = []
    for orient in ("portrait", "landscape"):
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": api_key, "User-Agent": USER_AGENT},
                params={"query": q_clean, "per_page": per_page, "orientation": orient},
                timeout=20,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        for p in resp.json().get("photos", []):
            src = p.get("src") or {}
            img_url = src.get("large2x") or src.get("large") or src.get("original")
            if not img_url:
                continue
            alt = str(p.get("alt") or _extract_pexels_slug(p.get("url", ""), q_clean)).strip()
            if not _is_family_safe_text(alt):
                continue
            out.append(
                VideoCandidate(
                    source="pexels_photo",
                    title=f"PexelsPhoto #{p.get('id')} - {alt or q_clean}"[:120],
                    download_url=img_url,
                    page_url=p.get("url", img_url),
                    license="Pexels License (Free Real Photo)",
                    duration_sec=7.0,
                    width=int(p.get("width") or (1080 if orient == "portrait" else 1920)),
                    height=int(p.get("height") or (1920 if orient == "portrait" else 1080)),
                    thumb_url=src.get("medium") or img_url,
                )
            )
    return out


def search_openverse_photos(query: str, limit: int = 6) -> list[VideoCandidate]:
    """Search Openverse Public Domain / CC0 / CC-BY real photographs (0 API key, commercial monetization safe)."""
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    try:
        resp = requests.get(
            "https://api.openverse.org/v1/images/",
            headers={"User-Agent": USER_AGENT},
            params={
                "q": q_clean,
                "license_type": "commercial,modification",
                "extension": "jpg",
                "page_size": limit,
            },
            timeout=15,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    out: list[VideoCandidate] = []
    for item in resp.json().get("results", []):
        img_url = str(item.get("url") or "").strip()
        if not img_url.lower().startswith("http"):
            continue
        title = str(item.get("title") or q_clean).strip()
        if not _is_real_video_title(title) or not _is_family_safe_text(title):
            continue
        w = int(item.get("width") or 0)
        h = int(item.get("height") or 0)
        if (w and w < 640) or (h and h < 480):
            continue
        lic = str(item.get("license") or "cc0").upper()
        out.append(
            VideoCandidate(
                source="openverse_photo",
                title=f"Openverse - {title}"[:120],
                download_url=img_url,
                page_url=str(item.get("foreign_landing_url") or img_url),
                license=f"Openverse {lic} (Commercial Safe)",
                duration_sec=7.0,
                width=w or 1280,
                height=h or 960,
                thumb_url=item.get("thumbnail"),
            )
        )
    return out


def search_wikimedia_photos(query: str, limit: int = 6) -> list[VideoCandidate]:
    """Search Wikimedia Commons high-res real camera JPEGs (0 API key, excluding charts/diagrams/logos)."""
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            headers={"User-Agent": USER_AGENT},
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"{q_clean} filetype:bitmap",
                "gsrnamespace": "6",
                "gsrlimit": str(limit),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": "1280",
            },
            timeout=15,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    pages = resp.json().get("query", {}).get("pages", {})
    out: list[VideoCandidate] = []
    for page in pages.values():
        title = str(page.get("title") or "")
        low = title.lower()
        if not (low.endswith(".jpg") or low.endswith(".jpeg")):
            continue
        if not _is_real_video_title(title) or any(tok in low for tok in ("chart", "graph", "map", "scheme", "structure", "formula", "table", "portrait of")):
            continue
        iis = page.get("imageinfo") or []
        if not iis:
            continue
        ii = iis[0]
        w = int(ii.get("width") or 0)
        h = int(ii.get("height") or 0)
        if w < 720 or h < 480:
            continue
        url = ii.get("thumburl") or ii.get("url")
        if not url:
            continue
        meta = ii.get("extmetadata") or {}
        meta_txt = " ".join(str(meta.get(k, {}).get("value", "")) for k in ("Categories", "ImageDescription"))
        if not _is_real_video_title(meta_txt) or not _is_family_safe_text(title, meta_txt):
            continue
        lic = re.sub(r"<[^>]+>", "", str(meta.get("LicenseShortName", {}).get("value", "CC Wikimedia"))).strip()
        if not any(ok_l in lic.lower() for ok_l in ("cc", "public domain", "pd", "gfdl")):
            continue
        out.append(
            VideoCandidate(
                source="wikimedia_photo",
                title=f"WikiPhoto - {title.replace('File:', '')}"[:120],
                download_url=url,
                page_url=ii.get("descriptionurl", url),
                license=lic,
                duration_sec=7.0,
                width=w,
                height=h,
                thumb_url=ii.get("thumburl"),
            )
        )
    return out


def _render_photo_ken_burns_mp4(candidate: VideoCandidate, dest_mp4: Path, duration_sec: float = 7.0) -> Optional[dict]:
    """Convert a real photograph into a smooth 30fps 9:16 subtle zoom-in MP4 clip (anti-jitter upscale before zoompan)."""
    raw_img = dest_mp4.with_suffix(".jpg")
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        if candidate.ping_url:
            unsplash_key = (os.environ.get("UNSPLASH_ACCESS_KEY") or "").strip()
            if unsplash_key:
                try:
                    requests.get(
                        candidate.ping_url,
                        headers={"Authorization": f"Client-ID {unsplash_key}", "User-Agent": USER_AGENT},
                        timeout=8,
                    )
                except Exception:
                    pass

        r = requests.get(candidate.download_url, headers={"User-Agent": USER_AGENT}, timeout=25)
        r.raise_for_status()
        raw_img.write_bytes(r.content)
        with Image.open(raw_img) as im:
            im.verify()
        frames = max(60, int(round(duration_sec * 30)))
        vf = (
            "scale=1280:2276:force_original_aspect_ratio=increase,"
            "crop=1280:2276,"
            f"zoompan=z='min(zoom+0.0008,1.10)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30"
        )
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(raw_img),
                "-vf", vf,
                "-t", f"{duration_sec:.2f}",
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
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
        raw_img.unlink(missing_ok=True)


_PIXABAY_KEY_RE = re.compile(r"^\d+-[0-9a-fA-F]{12,}$")
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
PIXABAY_CACHE_PATH = _BASE_DIR / "data" / "pixabay_cache.json"
PIXABAY_CACHE_TTL_SEC = 24 * 3600


def _pixabay_cache_get(cache_key: str) -> Optional[list[dict[str, Any]]]:
    import time

    if not PIXABAY_CACHE_PATH.exists():
        return None
    try:
        store = json.loads(PIXABAY_CACHE_PATH.read_text(encoding="utf-8"))
        entry = store.get(cache_key)
        if isinstance(entry, dict) and (time.time() - float(entry.get("ts", 0))) <= PIXABAY_CACHE_TTL_SEC:
            hits = entry.get("hits")
            if isinstance(hits, list):
                return hits
    except Exception:
        pass
    return None


def _pixabay_cache_set(cache_key: str, hits: list[dict[str, Any]]) -> None:
    import time

    try:
        store: dict[str, Any] = {}
        if PIXABAY_CACHE_PATH.exists():
            raw = json.loads(PIXABAY_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                now_t = time.time()
                store = {
                    k: v for k, v in raw.items()
                    if isinstance(v, dict) and (now_t - float(v.get("ts", 0))) <= PIXABAY_CACHE_TTL_SEC
                }
        store[cache_key] = {"ts": time.time(), "hits": hits}
        # Keep cache compact
        if len(store) > 120:
            oldest = sorted(store.items(), key=lambda kv: float(kv[1].get("ts", 0)))[:-120]
            for k, _ in oldest:
                store.pop(k, None)
        PIXABAY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PIXABAY_CACHE_PATH.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _resolve_pexels_api_key() -> str:
    k = (os.environ.get("PEXELS_API_KEY") or "").strip()
    if not k or _PIXABAY_KEY_RE.match(k):
        return ""
    return k


def _resolve_pixabay_api_key() -> str:
    k = (os.environ.get("PIXABAY_API_KEY") or "").strip()
    if k:
        return k
    pk = (os.environ.get("PEXELS_API_KEY") or "").strip()
    if pk and _PIXABAY_KEY_RE.match(pk):
        return pk
    return ""


def search_coverr_videos(query: str, limit: int = 6) -> list[VideoCandidate]:
    """Search Coverr Video API when COVERR_API_KEY is configured (skips silently when empty)."""
    api_key = (os.environ.get("COVERR_API_KEY") or "").strip()
    if not api_key:
        return []
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    try:
        resp = requests.get(
            "https://api.coverr.co/videos",
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
            params={"query": q_clean, "page_size": limit},
            timeout=15,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    out: list[VideoCandidate] = []
    for v in resp.json().get("hits", resp.json().get("videos", [])):
        title = str(v.get("title") or v.get("description") or q_clean).strip()
        if not _is_real_video_title(title) or not _is_family_safe_text(title):
            continue
        urls = v.get("urls") or {}
        dl_url = urls.get("mp4_download") or urls.get("mp4") or v.get("url") or ""
        if not str(dl_url).startswith("http"):
            continue
        dur = float(v.get("duration") or 10.0)
        if dur < 3.0:
            continue
        out.append(
            VideoCandidate(
                source="coverr",
                title=f"Coverr - {title}"[:120],
                download_url=str(dl_url),
                page_url=str(v.get("page_url") or v.get("url") or dl_url),
                license="Coverr Free Commercial License",
                duration_sec=dur,
                width=int(v.get("width") or 1080),
                height=int(v.get("height") or 1920),
                thumb_url=v.get("poster") or v.get("thumbnail"),
            )
        )
    return out


def search_unsplash_photos(query: str, per_page: int = 6) -> list[VideoCandidate]:
    """Search Unsplash Portrait Photography (pings download_location per Unsplash ToS; skips if key empty)."""
    api_key = (os.environ.get("UNSPLASH_ACCESS_KEY") or "").strip()
    if not api_key:
        return []
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {api_key}", "User-Agent": USER_AGENT},
            params={"query": q_clean, "orientation": "portrait", "per_page": per_page},
            timeout=15,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    out: list[VideoCandidate] = []
    for p in resp.json().get("results", []):
        urls = p.get("urls") or {}
        img_url = str(urls.get("regular") or urls.get("full") or "").strip()
        if not img_url:
            continue
        desc = str(p.get("alt_description") or p.get("description") or q_clean).strip()
        if not _is_real_video_title(desc) or not _is_family_safe_text(desc):
            continue
        links = p.get("links") or {}
        out.append(
            VideoCandidate(
                source="unsplash_photo",
                title=f"Unsplash - {desc}"[:120],
                download_url=img_url,
                page_url=str(links.get("html") or img_url),
                license="Unsplash License (Free Commercial)",
                duration_sec=7.0,
                width=int(p.get("width") or 1080),
                height=int(p.get("height") or 1920),
                thumb_url=urls.get("thumb"),
                ping_url=links.get("download_location"),
            )
        )
    return out


def search_pixabay(query: str, per_page: int = 8) -> list[VideoCandidate]:
    """Search Pixabay Video API with 24h query caching when PIXABAY_API_KEY is configured."""
    api_key = _resolve_pixabay_api_key()
    if not api_key:
        return []
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    cache_key = f"video:{q_clean.lower()}"
    hits = _pixabay_cache_get(cache_key)
    if hits is None:
        try:
            resp = requests.get(
                "https://pixabay.com/api/videos/",
                headers={"User-Agent": USER_AGENT},
                params={
                    "key": api_key,
                    "q": q_clean,
                    "per_page": per_page,
                    "safesearch": "true",
                    "video_type": "all",
                },
                timeout=20,
            )
        except requests.RequestException:
            return []
        if resp.status_code != 200:
            return []
        hits = resp.json().get("hits", [])
        _pixabay_cache_set(cache_key, hits)
    out: list[VideoCandidate] = []
    for v in hits:
        dur = float(v.get("duration") or 0)
        if dur < 3.0:
            continue
        tags = str(v.get("tags") or "")
        if not _is_real_video_title(tags) or not _is_family_safe_text(tags):
            continue
        videos = v.get("videos") or {}
        best = None
        for tier in ("medium", "large", "small"):
            cand_stream = videos.get(tier) or {}
            if cand_stream.get("url") and int(cand_stream.get("height") or 0) >= 480:
                best = cand_stream
                break
        if best is None:
            for tier in ("medium", "large", "small", "tiny"):
                cand_stream = videos.get(tier) or {}
                if cand_stream.get("url"):
                    best = cand_stream
                    break
        if not best:
            continue
        out.append(
            VideoCandidate(
                source="pixabay",
                title=f"Pixabay #{v.get('id')} - {tags or query}"[:120],
                download_url=best["url"],
                page_url=v.get("pageURL") or best["url"],
                license="Pixabay Content License (Free)",
                duration_sec=dur,
                width=int(best.get("width") or 1280),
                height=int(best.get("height") or 720),
                thumb_url=None,
            )
        )
    return out


def search_pixabay_photos(query: str, per_page: int = 6) -> list[VideoCandidate]:
    """Search Pixabay Photo API (vertical preferred, 24h cached, 100% monetization safe)."""
    api_key = _resolve_pixabay_api_key()
    if not api_key:
        return []
    q_clean = _clean_stock_query(query)
    if not q_clean:
        return []
    cache_key = f"photo:{q_clean.lower()}"
    hits = _pixabay_cache_get(cache_key)
    if hits is None:
        try:
            resp = requests.get(
                "https://pixabay.com/api/",
                headers={"User-Agent": USER_AGENT},
                params={
                    "key": api_key,
                    "q": q_clean,
                    "image_type": "photo",
                    "orientation": "vertical",
                    "per_page": per_page,
                    "safesearch": "true",
                },
                timeout=20,
            )
        except requests.RequestException:
            return []
        if resp.status_code != 200:
            return []
        hits = resp.json().get("hits", [])
        _pixabay_cache_set(cache_key, hits)
    out: list[VideoCandidate] = []
    for p in hits:
        img_url = str(p.get("largeImageURL") or p.get("webformatURL") or "").strip()
        if not img_url:
            continue
        tags = str(p.get("tags") or "")
        if not _is_real_video_title(tags) or not _is_family_safe_text(tags):
            continue
        w = int(p.get("imageWidth") or p.get("webformatWidth") or 1080)
        h = int(p.get("imageHeight") or p.get("webformatHeight") or 1920)
        if w < 480 or h < 480:
            continue
        out.append(
            VideoCandidate(
                source="pixabay_photo",
                title=f"PixabayPhoto #{p.get('id')} - {tags or q_clean}"[:120],
                download_url=img_url,
                page_url=p.get("pageURL") or img_url,
                license="Pixabay Content License (Free Real Photo)",
                duration_sec=7.0,
                width=w,
                height=h,
                thumb_url=p.get("previewURL"),
            )
        )
    return out


def _generate_tier3_ai_scene_mp4(scene: SceneSpec, dest_mp4: Path, duration_sec: float = 7.0) -> Optional[tuple[VideoCandidate, dict]]:
    """Tier 3: Generate a clean text-free 9:16 microscopic/abstract science image via Gemini/Cloudflare + Ken Burns."""
    img_bytes: Optional[bytes] = None
    prompt = (
        f"Photorealistic macro/microscopic science photography depicting: {scene.visual_query}. "
        f"Clean vertical 9:16 composition, moody cinematic studio lighting, strictly NO text, NO words, NO labels, NO watermarks."
    )
    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gemini_key:
        # Try Gemini native image generation / Imagen REST endpoint
        for url, payload in (
            (
                f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={gemini_key}",
                {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1, "aspectRatio": "9:16"}},
            ),
            (
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp-image-generation:generateContent?key={gemini_key}",
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
                },
            ),
        ):
            try:
                r = requests.post(url, json=payload, timeout=35)
                if r.status_code != 200:
                    continue
                j = r.json()
                preds = j.get("predictions") or []
                if preds and preds[0].get("bytesBase64Encoded"):
                    img_bytes = base64.b64decode(preds[0]["bytesBase64Encoded"])
                    break
                for cand in j.get("candidates", []):
                    for part in (cand.get("content") or {}).get("parts", []):
                        inline = part.get("inlineData") or {}
                        if inline.get("data") and str(inline.get("mimeType", "")).startswith("image/"):
                            img_bytes = base64.b64decode(inline["data"])
                            break
                    if img_bytes:
                        break
                if img_bytes:
                    break
            except Exception:
                continue

    if not img_bytes:
        cf_acc = (os.environ.get("CF_ACCOUNT_ID") or "").strip()
        cf_tok = (os.environ.get("CF_API_TOKEN") or "").strip()
        if cf_acc and cf_tok:
            try:
                cf_resp = requests.post(
                    f"https://api.cloudflare.com/client/v4/accounts/{cf_acc}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                    headers={"Authorization": f"Bearer {cf_tok}"},
                    json={"prompt": prompt, "num_steps": 4},
                    timeout=35,
                )
                if cf_resp.status_code == 200:
                    if cf_resp.headers.get("content-type", "").startswith("image/"):
                        img_bytes = cf_resp.content
                    else:
                        b64 = (cf_resp.json().get("result") or {}).get("image")
                        if b64:
                            img_bytes = base64.b64decode(b64)
            except Exception:
                pass

    if not img_bytes:
        return None

    raw_img = dest_mp4.with_suffix(".png")
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        raw_img.write_bytes(img_bytes)
        with Image.open(raw_img) as im:
            im.verify()
        frames = max(60, int(round(duration_sec * 30)))
        vf = (
            "scale=1280:2276:force_original_aspect_ratio=increase,"
            "crop=1280:2276,"
            f"zoompan=z='min(zoom+0.0008,1.08)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(raw_img), "-vf", vf,
                "-t", f"{duration_sec:.2f}", "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(dest_mp4),
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        info = probe_video(dest_mp4)
        if not info:
            return None
        cand = VideoCandidate(
            source="ai_scene",
            title=f"Tier 3 AI Scene - {scene.visual_query}"[:120],
            download_url="tier3://ai_scene",
            page_url="tier3://ai_scene",
            license="AI Generated (Tier 3 Microscopic Fallback)",
            duration_sec=float(info["duration"]),
            width=int(info["width"]),
            height=int(info["height"]),
        )
        return cand, info
    except Exception:
        return None
    finally:
        raw_img.unlink(missing_ok=True)


def _render_tier3_ambient_mp4(scene: SceneSpec, dest_mp4: Path, duration_sec: float = 7.0) -> Optional[tuple[VideoCandidate, dict]]:
    """Zero Hard-Crash final ambient backdrop plate so mechanistic overlays render cleanly even if all APIs are offline."""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c=#132238:s=1080x1920:d={duration_sec:.2f}:r=30",
                "-vf", "drawbox=x=0:y=0:w=1080:h=960:color=#1a3052@0.55:t=fill",
                "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                str(dest_mp4),
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        info = probe_video(dest_mp4)
        if not info:
            return None
        cand = VideoCandidate(
            source="tier3_ambient",
            title=f"Ambient Science Plate - {scene.visual_query}"[:120],
            download_url="tier3://ambient_plate",
            page_url="tier3://ambient_plate",
            license="Kimiabill Internal Plate (Zero Hard-Crash)",
            duration_sec=float(info["duration"]),
            width=1080,
            height=1920,
        )
        return cand, info
    except Exception:
        return None


_NASA_NOISE_TOKENS: tuple[str, ...] = (
    "annual highlights", "the quiet crew", "profile:", "ksc-", "jsc-", "briefing",
    "conference", "expedition", "astronaut", "press", "highlights", "suits",
    "interview", "mission overview", "space station", "payload", "hangar",
)


def _is_space_or_earth_topic(text_blob: str) -> bool:
    return bool(
        re.search(
            r"\b(?:space|planet|earth|moon|mars|solar|sun|orbit|galaxy|star|stars|"
            r"gravity|climate|atmosphere|ozone|aurora|satellite|astronomy|cosmic|nebula|"
            r"black hole|telescope|eclipse|meteor|asteroid)\b",
            (text_blob or "").lower(),
        )
    )


def search_nasa_videos(query: str, limit: int = 5) -> list[VideoCandidate]:
    """Search NASA Images & Video API (0 API key, Public Domain) strictly for space/earth/climate HD clips."""
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            headers={"User-Agent": USER_AGENT},
            params={"q": query, "media_type": "video"},
            timeout=20,
        )
    except requests.RequestException:
        return []
    if resp.status_code != 200:
        return []
    items = resp.json().get("collection", {}).get("items", [])
    out: list[VideoCandidate] = []
    for item in items[: limit * 2]:
        if len(out) >= limit:
            break
        data_list = item.get("data") or []
        if not data_list:
            continue
        data = data_list[0]
        title = str(data.get("title") or "").strip()
        desc = str(data.get("description") or "")
        nasa_id = str(data.get("nasa_id") or "").strip()
        low_td = f"{title} {nasa_id}".lower()
        if any(nt in low_td for nt in _NASA_NOISE_TOKENS):
            continue
        if not title or not _is_real_video_title(title) or not _is_family_safe_text(title, desc):
            continue
        collection_url = item.get("href")
        if not collection_url:
            continue
        try:
            col_resp = requests.get(collection_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        except requests.RequestException:
            continue
        if col_resp.status_code != 200:
            continue
        try:
            asset_urls = col_resp.json()
        except ValueError:
            continue
        mp4_urls = [u for u in asset_urls if isinstance(u, str) and u.lower().endswith(".mp4")]
        if not mp4_urls:
            continue
        mp4_url = None
        for suffix in ("~medium.mp4", "~small.mp4", "~orig.mp4", "~mobile.mp4"):
            matched = [u for u in mp4_urls if u.lower().endswith(suffix)]
            if matched:
                mp4_url = matched[0]
                break
        if mp4_url is None:
            mp4_url = mp4_urls[0]
        thumb_url = None
        for lnk in item.get("links") or []:
            if isinstance(lnk, dict) and lnk.get("rel") == "preview" and lnk.get("href"):
                thumb_url = lnk["href"]
                break
        out.append(
            VideoCandidate(
                source="nasa",
                title=f"NASA - {title}"[:120],
                download_url=mp4_url,
                page_url=f"https://images.nasa.gov/details/{nasa_id}" if nasa_id else mp4_url,
                license="NASA Public Domain (No Copyright)",
                duration_sec=10.0,
                width=1280,
                height=720,
                thumb_url=thumb_url,
            )
        )
    return out


_FLOW_BILINGUAL_SYNONYMS: dict[str, set[str]] = {
    "nanas": {"pineapple", "bromelain", "enzim", "enzyme"},
    "pineapple": {"nanas", "bromelain", "enzyme"},
    "daging": {"meat", "beef", "steak", "protein", "serat"},
    "meat": {"daging", "beef", "steak"},
    "beef": {"daging", "steak", "meat"},
    "kopi": {"coffee", "caffeine", "kafein", "seduh", "brew"},
    "coffee": {"kopi", "caffeine", "brew"},
    "air": {"water", "droplet", "h2o", "tetes", "liquid", "cairan"},
    "water": {"air", "droplet", "tetes", "liquid"},
    "minyak": {"oil", "frying", "goreng", "lipid", "lemak"},
    "oil": {"minyak", "frying", "goreng"},
    "es": {"ice", "beku", "frost", "kristal", "crystal"},
    "ice": {"es", "beku", "frost", "melting", "mencair"},
    "garam": {"salt", "nacl", "sodium", "kristal"},
    "salt": {"garam", "nacl", "sodium", "crystal"},
    "gula": {"sugar", "karamel", "caramel", "glukosa"},
    "sugar": {"gula", "caramel", "glucose"},
    "api": {"fire", "flame", "pembakaran", "nyala", "burn"},
    "fire": {"api", "flame", "burn", "combustion"},
    "karat": {"rust", "korosi", "corrosion", "iron", "besi", "oksidasi"},
    "rust": {"karat", "korosi", "corrosion", "iron", "besi"},
    "sel": {"cell", "cells", "mikroskop", "microscope", "bakteri"},
    "cell": {"sel", "microscope", "mikroskop"},
    "telur": {"egg", "albumin", "protein"},
    "egg": {"telur", "protein", "boiling"},
    "bawang": {"onion", "garlic", "tear", "mata"},
    "onion": {"bawang", "garlic"},
    "susu": {"milk", "casein", "kasein", "emulsion"},
    "milk": {"susu", "emulsion"},
    "pisang": {"banana", "buah", "fruit"},
    "banana": {"pisang", "fruit"},
    "asam": {"acid", "ph", "cuka", "vinegar", "lemon"},
    "acid": {"asam", "vinegar", "lemon"},
    "panas": {"heat", "hot", "boiling", "mendidih", "thermal"},
    "mendidih": {"boiling", "bubbles", "gelembung", "heat"},
    "boiling": {"mendidih", "bubbles", "panas"},
    "goreng": {"frying", "oil", "minyak", "pan", "wajan"},
    "frying": {"goreng", "oil", "minyak", "pan"},
    "kristal": {"crystal", "crystals", "lattice", "kisi"},
    "crystal": {"kristal", "salt", "garam"},
}


def list_flow_clip_bank() -> list[dict]:
    """Fetch available Google AI Flow MP4 clips from Kimiabill_Studio/Flow_Clip_Bank via Apps Script webhook."""
    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        return []
    try:
        from src.tools.gdrive_sync import _normalize_webhook_url

        webhook_url = _normalize_webhook_url(raw_url)
        token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
        resp = requests.post(
            webhook_url,
            json={
                "action": "list_flow_clips",
                "token": token,
                "root_folder": "Kimiabill_Studio",
                "folder_name": "Flow_Clip_Bank",
            },
            timeout=25,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("ok"):
            return []
        clips = data.get("clips") or []
        return [c for c in clips if isinstance(c, dict) and c.get("file_id") and c.get("filename")]
    except Exception:
        return []


def _score_flow_clip_for_scene(filename: str, scene: SceneSpec) -> int:
    stem = Path(filename).stem.lower()
    raw_tokens = [
        t for t in re.findall(r"[a-z0-9]+", stem)
        if len(t) >= 2 and t not in {"flow", "clip", "clips", "video", "scene", "kimiabill", "9x16", "hd", "mp4", "mov"}
    ]
    if not raw_tokens:
        return 0
    expanded_file_tokens: set[str] = set(raw_tokens)
    for tok in raw_tokens:
        expanded_file_tokens |= _FLOW_BILINGUAL_SYNONYMS.get(tok, set())

    scene_blob = " ".join([scene.visual_query, *scene.fallback_queries, scene.narration or ""]).lower()
    scene_tokens = set(re.findall(r"[a-z0-9]+", scene_blob))
    overlap = expanded_file_tokens & scene_tokens
    return len(overlap)


def _try_match_flow_bank_clip(
    scene: SceneSpec,
    flow_clips: list[dict],
    used_flow_ids: set[str],
    used_urls: set[str],
    dest_mp4: Path,
    episode_id: str,
    memory: StudioMemory,
) -> Optional[tuple[VideoCandidate, dict]]:
    if not flow_clips:
        return None
    scored: list[tuple[int, dict]] = []
    for clip in flow_clips:
        fid = str(clip.get("file_id") or "")
        wurl = str(clip.get("url") or clip.get("web_url") or f"flow://{fid}")
        if not fid or fid in used_flow_ids or wurl in used_urls:
            continue
        sc = _score_flow_clip_for_scene(str(clip.get("filename") or ""), scene)
        if sc >= 2:
            scored.append((sc, clip))
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("filename") or "")))

    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        return None
    from src.tools.gdrive_sync import _normalize_webhook_url

    webhook_url = _normalize_webhook_url(raw_url)
    token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()

    for _sc, clip in scored[:1]:
        fid = str(clip["file_id"])
        fname = str(clip["filename"])
        wurl = str(clip.get("url") or clip.get("web_url") or f"flow://{fid}")
        try:
            dl_resp = requests.post(
                webhook_url,
                json={"action": "download_drive_file", "token": token, "file_id": fid},
                timeout=90,
            )
            if dl_resp.status_code != 200:
                continue
            dl_json = dl_resp.json()
            if not dl_json.get("ok") or not dl_json.get("content_base64"):
                continue
            raw_path = dest_mp4.with_suffix(".raw_dl")
            raw_path.write_bytes(base64.b64decode(dl_json["content_base64"]))
            raw_info = probe_video(raw_path)
            if not raw_info or raw_info.get("duration", 0) < 2.0:
                raw_path.unlink(missing_ok=True)
                continue
            ok, _clean, reason = vision_screen_clip(raw_path, scene)
            print(f"[asset_scout] {scene.scene_id} Flow_Clip_Bank '{fname}': ok={ok} ({reason[:140]})")
            used_urls.add(wurl)
            if not ok:
                raw_path.unlink(missing_ok=True)
                continue
            chosen_info = _transcode_to_mp4(raw_path, raw_info, dest_mp4, source="google_flow")
            if not chosen_info:
                raw_path.unlink(missing_ok=True)
                continue
            used_flow_ids.add(fid)
            try:
                memory.mark_flow_clip_used(fid, fname, episode_id)
            except Exception:
                pass
            cand = VideoCandidate(
                source="google_flow",
                title=f"Google Flow - {fname}"[:120],
                download_url=wurl,
                page_url=wurl,
                license="Google AI Flow (User Subscription Bank)",
                duration_sec=float(chosen_info.get("duration") or raw_info.get("duration") or 6.0),
                width=int(chosen_info.get("width") or 1080),
                height=int(chosen_info.get("height") or 1920),
                thumb_url=None,
            )
            return cand, chosen_info
        except Exception as exc:
            print(f"[asset_scout] Peringatan ambil Flow_Clip_Bank '{fname}': {exc}")
    return None


def search_wikimedia_videos(query: str, limit: int = 10) -> list[VideoCandidate]:
    """Last-resort Wikimedia Commons video search with strict lecture/university/bumper filtering and <=3 min cap."""
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
        "viurlwidth": "720",
    }
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            headers=headers,
            params=params,
            timeout=20,
        )
    except requests.RequestException:
        return []
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
        # Strictly reject clips > 3 minutes (180s) or low resolution
        if dur < 3.5 or dur > 180.0 or w < 480 or h < 360:
            continue
        url = vi.get("url")
        if not url:
            continue
        meta = vi.get("extmetadata", {})
        meta_text = " ".join(
            str(meta.get(k, {}).get("value", ""))
            for k in ("Categories", "ImageDescription", "ObjectName", "Restrictions")
        )
        if not _is_real_video_title(f"{title} {meta_text}") or not _is_family_safe_text(title, meta_text):
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
    out.sort(key=lambda c: (0 if 5 <= c.duration_sec <= 60 else 1, -min(c.width, 1920)))
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


def _transcode_to_mp4(
    raw_path: Path,
    info: dict,
    dest_mp4: Path,
    *,
    source: str = "pexels",
) -> Optional[dict]:
    """Transcode approved raw clip into clean yuv420p H.264 MP4, trimming 2s start+end on all non-Pexels clips."""
    try:
        dur = float(info.get("duration") or 0.0)
        src_low = (source or "").lower()
        is_pexels_or_flow = src_low in ("pexels", "google_flow")
        if not is_pexels_or_flow and dur > 6.5:
            # Mandatory 2.0s head + 2.0s tail crop to eliminate foreign university/channel bumpers & logos
            start_at = 2.0
            slice_dur = min(22.0, max(2.5, dur - 4.0))
        elif not is_pexels_or_flow and dur > 4.2:
            trim_each = min(1.5, max(0.5, (dur - 2.5) / 2.0))
            start_at = trim_each
            slice_dur = max(2.4, dur - 2.0 * trim_each)
        else:
            start_at = 0.0
            slice_dur = min(24.0, max(2.0, dur))
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start_at:.2f}",
                "-i",
                str(raw_path),
                "-t",
                f"{slice_dur:.2f}",
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
    """Download candidate video and normalize to clean H.264 MP4 (max 24s slice) for fast rendering."""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    raw_path = dest_mp4.with_suffix(".raw_dl")
    info = _download_raw_clip(candidate, raw_path, max_download_mb=max_download_mb)
    if not info:
        return None
    return _transcode_to_mp4(raw_path, info, dest_mp4, source=candidate.source)


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
    import time

    # Gentle spacing keeps Vision screening below free-tier RPM bursts while staying fast
    time.sleep(0.25)
    try:
        from pydantic import BaseModel, Field

        from src.providers import call_structured_agent

        class ClipScreen(BaseModel):
            family_safe: bool = Field(description="False if ANY nudity, sexual/suggestive content, gore, violence, or otherwise inappropriate imagery for a kids/teen science channel")
            relevance_score: float = Field(ge=0, le=10, description="Strict score 0-10: how directly the frames visually show what the scene narration & visual_query describe (score <=3 if unrelated animal, random lab LCD/Arduino, or abstract filler)")
            has_watermark_or_ui: bool = Field(description="True if frames show ANY university/lab bumper (e.g. PennState, LIFE IN THE LAB), intro/outro logo, presentation slide, PowerPoint text, Arduino LCD readout, music player, or watermark")
            is_real_footage: bool = Field(description="True if real camera footage (not cartoon/animation/slide/title card)")
            is_scientific_visualization: bool = Field(
                default=False,
                description="True only if serious scientific visualization (molecular/protein model, microscopy) - NEVER text slides or logos",
            )
            reason: str = ""

        verdict = call_structured_agent(
            provider="auto",
            model=os.environ.get("GEMINI_SCREEN_MODEL", "gemini-3.5-flash-lite"),
            system_prompt=(
                "You are a ruthless visual safety & relevance screener for 'Kimiabill', a family-friendly "
                "Indonesian pure-science short-video channel. The attached image shows 3 frames sampled from a stock clip. "
                "REJECT immediately (`has_watermark_or_ui=true` or low relevance_score <=3) if you see ANY foreign channel/university "
                "bumper or logo, presentation text slide, Arduino LCD/breadboard unrelated to biology/DNA, or unrelated animals."
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
        err_str = str(exc)
        if "429" in err_str or "503" in err_str or "timed out" in err_str.lower():
            return False, True, f"vision rate-limit fallback eligible ({err_str[:80]})"
        return False, False, f"vision screening error (rejected for safety): {err_str[:120]}"
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


def batch_audit_broll_contact_sheet(
    shots: list[tuple[int, SceneSpec, Path]],
    out_jpg: Path,
) -> dict[int, tuple[bool, bool, float, str]]:
    """Audit all candidate B-roll clips in ONE Gemini Vision contact-sheet call.

    Returns dict mapping shot_index (1-based) -> (accepted, clean, score, reason).
    Calibrated thresholds:
      - shot_index == 1 (Hook): relevance >= 7.5
      - shot_index >= 2 (Body): relevance >= 6.0
    Never raises: falls back cleanly if Vision API or GEMINI_API_KEY is unavailable.
    """
    hook_min = float(os.environ.get("ASSET_HOOK_MIN_RELEVANCE", "7.5"))
    body_min = float(os.environ.get("ASSET_BODY_MIN_RELEVANCE", "6.0"))
    if not shots:
        return {}
    default_pass = {
        idx: (True, True, hook_min if idx == 1 else body_min, "vision batch skipped")
        for idx, _sc, _p in shots
    }
    if os.environ.get("ASSET_VISION_SCREEN", "true").lower() != "true":
        return default_pass
    if not os.environ.get("GEMINI_API_KEY"):
        return default_pass

    try:
        from PIL import Image, ImageDraw
    except Exception:
        return default_pass

    cell_w, cell_h = 300, 400
    thumbs: list[tuple[int, SceneSpec, Image.Image]] = []
    for idx, sc, clip in shots:
        info = probe_video(clip)
        if not info:
            continue
        dur = max(0.5, float(info.get("duration") or 1.0))
        fp = out_jpg.with_name(f"{out_jpg.stem}_s{idx}.jpg")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{dur * 0.50:.2f}",
                "-i",
                str(clip),
                "-frames:v",
                "1",
                "-vf",
                f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=increase,crop={cell_w}:{cell_h}",
                "-q:v",
                "4",
                str(fp),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if fp.exists():
            try:
                im = Image.open(fp).convert("RGB")
                draw = ImageDraw.Draw(im)
                draw.rectangle((0, 0, cell_w, 34), fill=(15, 23, 42))
                draw.text((10, 9), f"SHOT {idx} ({sc.scene_id})", fill=(255, 255, 255))
                thumbs.append((idx, sc, im))
            finally:
                fp.unlink(missing_ok=True)

    if not thumbs:
        return default_pass

    cols = min(3, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (10, 12, 18))
    for pos, (_idx, _sc, im) in enumerate(thumbs):
        r, c = divmod(pos, cols)
        sheet.paste(im, (c * cell_w, r * cell_h))
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_jpg, quality=85)

    try:
        from pydantic import BaseModel, Field

        from src.providers import call_structured_agent

        class ShotAuditItem(BaseModel):
            shot_index: int
            family_safe: bool = Field(default=True)
            has_watermark_or_bumper: bool = Field(
                default=False,
                description="True if shot has watermark, presentation slide, channel logo, or university bumper",
            )
            relevance_score: float = Field(ge=0, le=10)
            reason: str = ""

        class BatchBrollAudit(BaseModel):
            shots: list[ShotAuditItem]

        scene_manifest = [
            {
                "shot_index": idx,
                "scene_id": sc.scene_id,
                "visual_query": sc.visual_query,
                "narration": sc.narration,
                "min_required_score": hook_min if idx == 1 else body_min,
            }
            for idx, sc, _im in thumbs
        ]
        verdict = call_structured_agent(
            provider="auto",
            model=os.environ.get("GEMINI_SCREEN_MODEL", "gemini-3.5-flash-lite"),
            system_prompt=(
                "You audit all B-roll shots for one Indonesian pure-science Shorts episode in ONE batch. "
                "The image grid labels each frame SHOT 1, SHOT 2, ... Score each shot 0-10 on real camera relevance "
                "to its visual_query + narration. Flag `has_watermark_or_bumper=true` ONLY for obvious watermarks, "
                "presentation slides, or foreign channel bumpers. Be fair and calibrated."
            ),
            user_payload={"shots": scene_manifest},
            response_model=BatchBrollAudit,
            handoff_path=None,
            image_paths=[out_jpg],
        )
        out: dict[int, tuple[bool, bool, float, str]] = {}
        by_idx = {item.shot_index: item for item in verdict.shots}
        for idx, _sc, _p in shots:
            item = by_idx.get(idx)
            if item is None:
                thresh = hook_min if idx == 1 else body_min
                out[idx] = (True, True, thresh, "not listed by batch model; accepted")
                continue
            thresh = hook_min if idx == 1 else body_min
            clean = bool(item.family_safe and not item.has_watermark_or_bumper)
            ok = bool(clean and float(item.relevance_score) >= thresh)
            out[idx] = (
                ok,
                clean,
                float(item.relevance_score),
                f"batch safe={item.family_safe} wm={item.has_watermark_or_bumper} rel={item.relevance_score:.1f}/{thresh} {item.reason}",
            )
        return out
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:100]
        return {
            idx: (True, True, hook_min if idx == 1 else body_min, f"batch vision fallback ({err})")
            for idx, _sc, _p in shots
        }
    finally:
        out_jpg.unlink(missing_ok=True)


def _next_free_path(assets_dir: Path, scene_id: str) -> Path:
    """Versioned filenames so re-scouting never overwrites footage used by earlier render versions."""
    base = assets_dir / f"{scene_id}.mp4"
    if not base.exists():
        return base
    k = 2
    while (assets_dir / f"{scene_id}_alt{k}.mp4").exists():
        k += 1
    return assets_dir / f"{scene_id}_alt{k}.mp4"


def resolve_scene_sfx_cues(
    scene_idx: int,
    total_scenes: int,
    scene: SceneSpec,
    memory: StudioMemory,
    force_transition: bool = True,
    force_animation: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    """Assign fitting 100% copyright-free transition SFX + optional animation pop/spark SFX per scene."""
    raw_cue = str(getattr(scene, "sfx_cue", "") or "").strip().lower()
    if raw_cue in ("whoosh", "pop", "riser", "chime", "ding", "spark"):
        trans_cue: Optional[str] = raw_cue
    elif force_transition:
        if scene_idx == 0:
            trans_cue = "riser"
        elif total_scenes >= 3 and scene_idx == total_scenes - 1:
            trans_cue = "chime"
        elif total_scenes >= 4 and scene_idx == total_scenes - 2:
            trans_cue = "ding"
        else:
            trans_cue = "whoosh" if scene_idx % 2 == 1 else "pop"
    else:
        trans_cue = None

    anim_cue: Optional[str] = None
    has_visual_anim = bool(
        getattr(scene, "mechanism", None) is not None
        or getattr(scene, "overlay_badge", None)
    )
    if force_animation and has_visual_anim:
        if trans_cue == "spark":
            anim_cue = "pop"
        elif trans_cue == "pop":
            anim_cue = "spark"
        else:
            anim_cue = "spark" if scene_idx % 2 == 1 else "pop"

    return (
        memory.get_sfx_path(trans_cue) if trans_cue else None,
        memory.get_sfx_path(anim_cue) if anim_cue else None,
    )


_FILLER_AND_ADJECTIVES: frozenset[str] = frozenset({
    "the", "and", "with", "from", "into", "that", "this", "for", "are", "was",
    "in", "on", "of", "to", "a", "an", "at", "by", "or", "as", "is", "be",
    "has", "had", "can", "not", "but", "out", "off", "all", "its", "one", "two",
    "big", "low", "high", "use", "used", "using", "show", "showing", "video",
    "clip", "clips", "footage", "close", "closeup", "macro", "shot", "view",
    "real", "screen", "monitor", "computer", "dark", "light", "bright", "cold",
    "hot", "warm", "tiny", "small", "large", "fast", "slow", "hard", "soft",
    "red", "green", "blue", "white", "black", "orange", "yellow", "background",
    "human", "person", "people", "hand", "hands", "finger", "fingers", "inside",
    "surface", "object", "thing", "modern", "clean", "simple", "everyday", "next",
})

_DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    # Kitchen & Everyday Chemistry (Kimiabill core)
    "pineapple": ("fruit", "bromelain", "enzyme", "juice", "kitchen", "slice"),
    "meat": ("beef", "steak", "cooking", "grill", "pan", "protein", "kitchen"),
    "steak": ("meat", "beef", "pan", "cooking", "searing", "kitchen"),
    "vinegar": ("acid", "acetic", "bottle", "liquid", "kitchen", "glass", "solution"),
    "lemon": ("citrus", "acid", "fruit", "juice", "kitchen", "slice"),
    "acid": ("chemical", "beaker", "liquid", "laboratory", "reaction", "vinegar", "lemon"),
    "rust": ("iron", "metal", "corrosion", "oxidation", "steel", "nail"),
    "iron": ("rust", "metal", "steel", "magnet", "corrosion"),
    "soap": ("foam", "bubbles", "washing", "water", "cleaning", "surfactant"),
    "oil": ("frying", "pan", "water", "liquid", "droplet", "cooking", "emulsion"),
    "egg": ("cooking", "pan", "boiling", "shell", "kitchen", "frying"),
    "ice": ("freezing", "cold", "water", "melting", "crystal", "frost"),
    "salt": ("crystal", "sodium", "water", "dissolve", "grains", "mineral"),
    "sugar": ("crystal", "sweet", "water", "caramel", "dissolve", "kitchen"),
    "coffee": ("caffeine", "brew", "cup", "roast", "water", "beans"),
    "tea": ("leaf", "cup", "brew", "hot", "water", "herbal"),
    "pan": ("frying", "cooking", "skillet", "kitchen", "stove", "heat"),
    "teflon": ("pan", "nonstick", "skillet", "cooking", "coating", "egg"),
    "nonstick": ("pan", "frying", "skillet", "cooking", "kitchen"),
    # Laboratory & Pure Science
    "beaker": ("laboratory", "glassware", "chemistry", "liquid", "flask", "experiment"),
    "reaction": ("chemical", "bubbles", "beaker", "laboratory", "experiment", "color"),
    "crystal": ("microscope", "salt", "mineral", "structure", "ice"),
    "microscope": ("cells", "bacteria", "lab", "science", "lens", "macro"),
    "flame": ("fire", "heat", "combustion", "burner", "burn"),
    "boiling": ("water", "steam", "bubbles", "kettle", "pot", "heat"),
    "charcoal": ("carbon", "filter", "black", "burn", "adsorption"),
    # Everyday Physics & Tech
    "cooktop": ("stove", "induction", "kitchen", "cooking", "pan", "pot", "burner"),
    "stove": ("cooktop", "kitchen", "cooking", "pan", "burner"),
    "induction": ("cooktop", "stove", "coil", "pan", "magnetic", "cooking"),
    "smartphone": ("phone", "mobile", "iphone", "android", "screen", "touchscreen"),
    "phone": ("smartphone", "mobile", "iphone", "display", "screen", "charger"),
    "oled": ("display", "screen", "smartphone", "pixel", "led", "tv"),
    "microwave": ("oven", "kitchen", "food", "cooking", "heating"),
    "fridge": ("refrigerator", "freezer", "cooling", "ice", "kitchen"),
    "refrigerator": ("fridge", "freezer", "ice", "cooling", "kitchen"),
    "battery": ("charging", "charger", "power", "energy", "electric", "lithium"),
    "solar": ("panel", "photovoltaic", "sunlight", "renewable", "energy"),
    "filter": ("water", "purifier", "clean", "charcoal", "glass"),
    "plastic": ("bottle", "polymer", "packaging", "recycling"),
}

_IRRELEVANT_STOCK_NOISE: frozenset[str] = frozenset({
    "jellyfish", "cow", "cows", "penguin", "penguins", "butterfly", "butterflies",
    "snake", "python", "squirrel", "kitten", "puppy", "cat", "dog", "moose",
    "alpaca", "sheep", "goat", "horse", "flamingo", "deer", "duck", "bird",
    "fireworks", "skateboard", "skater", "wedding", "bride", "tulip", "tulips",
    "poppy", "snowdrop", "daisy", "lavender", "bacon", "beer", "cocktail",
    "pilates", "yoga", "ballet", "marbles", "antiquariat", "amethyst",
    "cream", "chocolate", "cake", "dessert", "candy",
})


def _extract_scene_topic_terms(scene: SceneSpec) -> set[str]:
    raw_blob = " ".join([scene.visual_query, *scene.fallback_queries]).lower()
    base_tokens = [
        t for t in re.findall(r"[a-z]{3,}", raw_blob)
        if t not in _FILLER_AND_ADJECTIVES
    ]
    expanded: set[str] = set(base_tokens)
    for t in base_tokens:
        for syn in _DOMAIN_SYNONYMS.get(t, ()):
            expanded.add(syn)
    return expanded


def _metadata_relevance_score(cand: VideoCandidate, scene: SceneSpec, topic_terms: set[str]) -> int:
    title_low = cand.title.lower()
    meta_clean = re.sub(r"^(?:pixabay|pexels|coverr|unsplash|openverse)\s*#\d+\s*-\s*", "", title_low)
    meta_tokens = set(re.findall(r"[a-z]{3,}", meta_clean))
    scene_raw_low = " ".join([scene.visual_query, *scene.fallback_queries, scene.narration or ""]).lower()
    for noise_tok in _IRRELEVANT_STOCK_NOISE:
        if noise_tok in meta_tokens and noise_tok not in scene_raw_low:
            return -10
    for nk in getattr(scene, "negative_keywords", ()) or ():
        nk_low = str(nk).strip().lower()
        if nk_low and nk_low not in scene_raw_low and re.search(rf"\b{re.escape(nk_low)}\b", meta_clean):
            return -10
    if not topic_terms:
        return 1
    meta_stems = meta_tokens | {t.rstrip("s") for t in meta_tokens if len(t) > 4}
    topic_stems = topic_terms | {t.rstrip("s") for t in topic_terms if len(t) > 4}
    return len(meta_stems & topic_stems)


def _build_scene_search_plan(
    scene: SceneSpec,
    owner_visual_bias: str = "",
) -> tuple[list[str], list[str], set[str], str, bool]:
    raw_queries = [scene.visual_query] + list(scene.fallback_queries)
    if owner_visual_bias:
        raw_queries = [f"{scene.visual_query} {owner_visual_bias}".strip()] + raw_queries
    broadened: list[str] = []
    noun_anchors: list[str] = []
    for q in raw_queries:
        words = [w for w in re.findall(r"[A-Za-z]+", q.lower()) if len(w) >= 3 and w not in _FILLER_AND_ADJECTIVES]
        if len(words) >= 2:
            broadened.append(" ".join(words[:2]))
            broadened.append(" ".join(words[-2:]))
        for w in words:
            if w in _DOMAIN_SYNONYMS and w not in noun_anchors:
                noun_anchors.append(w)

    scene_text_blob = " ".join([scene.visual_query, *scene.fallback_queries, scene.narration or ""]).lower()
    is_kitchen = bool(
        re.search(
            r"\b(?:pineapple|meat|beef|steak|chicken|pork|fish|onion|garlic|oil|frying|fry|"
            r"coffee|tea|ice|egg|bread|rice|milk|salt|sugar|vinegar|lemon|banana|kitchen|"
            r"cooking|cook|fruit|food|dish|plate|bowl|pan|knife|chopping|slicing|marinat\w*|"
            r"tenderiz\w*|nanas|daging|goreng|kopi|teh|telur|roti|nasi|susu|garam|gula|"
            r"dapur|masak|buah|bawang|minyak|panci|wajan)\b",
            scene_text_blob,
        )
    )
    if is_kitchen:
        generic_fallbacks = [
            "cooking frying pan",
            "chopping food kitchen",
            "fresh fruit slicing",
            "pouring water glass",
            "boiling water kitchen",
        ]
    else:
        generic_fallbacks = [
            "chemistry laboratory experiment",
            "chemical reaction beaker",
            "optical microscope cells",
            "physics laboratory experiment",
            "crystal growth microscope",
            "science laboratory glassware",
        ]

    seen_q: set[str] = set()
    queries: list[str] = []
    for q in [*raw_queries, *broadened, *noun_anchors[:2], *generic_fallbacks]:
        q_norm = _clean_stock_query(q)
        if q_norm and q_norm.lower() not in seen_q:
            seen_q.add(q_norm.lower())
            queries.append(q_norm)

    return queries, generic_fallbacks, {g.lower() for g in generic_fallbacks}, scene_text_blob, is_kitchen


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
    """Scout visual footage via 3-Tier Waterfall + 1-call batch contact-sheet audit + zero hard-crash."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    used_urls: set[str] = set(exclude_urls or ())
    used_stems: set[str] = set()
    max_screens = int(os.environ.get("ASSET_MAX_SCREENS_PER_SCENE", "4"))
    non_generic_cap = max(2, max_screens - 2)
    batch_audit_enabled = os.environ.get("ASSET_BATCH_CONTACT_AUDIT", "true").lower() == "true"
    flow_bank_clips = list_flow_clip_bank()
    used_flow_ids: set[str] = set()
    try:
        from src.tools.telegram_notify import get_owner_runtime_overrides

        _ro = get_owner_runtime_overrides()
    except Exception:
        _ro = {}
    force_trans_sfx = bool(_ro.get("force_sfx_every_transition", True))
    force_anim_sfx = bool(_ro.get("force_sfx_every_animation", True))
    owner_visual_bias = str(_ro.get("visual_style_bias") or "").strip()
    try:
        raw_used = memory.get_used_flow_clip_ids()
        if isinstance(raw_used, set):
            used_flow_ids = set(raw_used)
    except Exception:
        pass
    episode_id = assets_dir.parent.name if assets_dir.parent else ""

    source_rank = {"pixabay": 0, "coverr": 1, "pexels": 2, "nasa": 3, "wikimedia_commons": 4}
    photo_rank = {"unsplash_photo": 0, "pixabay_photo": 1, "openverse_photo": 2, "pexels_photo": 3, "wikimedia_photo": 4}

    entries: list[dict] = []
    total_scenes = len(scenes)

    # Phase 1: Resolve overrides / Flow Bank, or collect 1 best Tier-1 raw clip per scene
    for scene_idx, scene in enumerate(scenes):
        trans_sfx_path, anim_sfx_path = resolve_scene_sfx_cues(
            scene_idx=scene_idx,
            total_scenes=total_scenes,
            scene=scene,
            memory=memory,
            force_transition=force_trans_sfx,
            force_animation=force_anim_sfx,
        )
        if target_scene_ids is not None and scene.scene_id not in target_scene_ids and existing_assets and scene.scene_id in existing_assets:
            used_urls.add(existing_assets[scene.scene_id].source_url)
            entries.append({"scene_idx": scene_idx, "scene": scene, "prebuilt_asset": existing_assets[scene.scene_id]})
            continue

        dest_mp4 = _next_free_path(assets_dir, scene.scene_id)
        chosen_cand: Optional[VideoCandidate] = None
        chosen_info: Optional[dict] = None
        topic_terms = _extract_scene_topic_terms(scene)

        if manual_overrides and scene.scene_id in manual_overrides:
            cand = manual_overrides[scene.scene_id]
            info = download_and_normalize_clip(cand, dest_mp4)
            if info:
                chosen_cand, chosen_info = cand, info

        if chosen_cand is None and flow_bank_clips:
            flow_hit = _try_match_flow_bank_clip(
                scene=scene,
                flow_clips=flow_bank_clips,
                used_flow_ids=used_flow_ids,
                used_urls=used_urls,
                dest_mp4=dest_mp4,
                episode_id=episode_id,
                memory=memory,
            )
            if flow_hit:
                chosen_cand, chosen_info = flow_hit

        queries, generic_fallbacks, generic_set, scene_text_blob, is_kitchen = _build_scene_search_plan(
            scene, owner_visual_bias=owner_visual_bias
        )
        init_cand: Optional[VideoCandidate] = None
        init_info: Optional[dict] = None
        init_raw: Optional[Path] = None
        init_mscore = 0
        extra_tier1: list[tuple[int, VideoCandidate, bool]] = []

        if chosen_cand is None:
            for q_idx, q in enumerate(queries):
                is_generic_q = q.lower() in generic_set
                if not is_generic_q and q_idx >= max(non_generic_cap + 2, 4):
                    continue
                if init_cand is not None and len(extra_tier1) >= 2:
                    break
                # Tier 1 Waterfall: Pixabay -> Coverr -> Pexels (if working) -> NASA -> Wikimedia
                stock_cands = search_pixabay(q)
                if len(stock_cands) < 4:
                    stock_cands += search_coverr_videos(q)
                if len(stock_cands) < 4:
                    stock_cands += search_pexels(q, orientation="portrait")
                if len(stock_cands) < 4:
                    stock_cands += search_pexels(q, orientation="landscape")
                nasa_cands = (
                    search_nasa_videos(q, limit=3)
                    if (_is_space_or_earth_topic(scene_text_blob) and not is_kitchen and len(stock_cands) < 3)
                    else []
                )
                wiki_cands: list[VideoCandidate] = []
                if len(stock_cands) == 0:
                    wiki_cands = search_wikimedia_videos(q)
                    q_terms = [w for w in re.findall(r"[a-z]+", q.lower()) if len(w) >= 3 and w not in _FILLER_AND_ADJECTIVES]
                    if wiki_cands and not is_generic_q and q_terms:
                        wiki_cands = [wc for wc in wiki_cands if any(t in wc.title.lower() for t in q_terms)]

                scored_cands: list[tuple[int, VideoCandidate]] = []
                for cand in (stock_cands + nasa_cands + wiki_cands):
                    m_score = _metadata_relevance_score(cand, scene, topic_terms)
                    if m_score < 0:
                        continue
                    scored_cands.append((m_score, cand))

                scored_cands.sort(
                    key=lambda pair: (
                        -pair[0],
                        0 if pair[1].height > pair[1].width else 1,
                        source_rank.get(pair[1].source, 9),
                        0 if pair[1].width >= 720 else 1,
                        -min(pair[1].width, 1920),
                    )
                )

                for m_score, cand in scored_cands:
                    stem = cand.title.lower()[:32]
                    if cand.download_url in used_urls or cand.page_url in used_urls or stem in used_stems:
                        continue
                    if init_cand is None:
                        raw_path = dest_mp4.with_suffix(".raw_dl")
                        raw_info = _download_raw_clip(cand, raw_path)
                        used_urls.add(cand.download_url)
                        used_urls.add(cand.page_url)
                        used_stems.add(stem)
                        if not raw_info:
                            continue
                        init_cand, init_info, init_raw, init_mscore = cand, raw_info, raw_path, m_score
                    elif len(extra_tier1) < 2:
                        extra_tier1.append((m_score, cand, is_generic_q))
                if init_cand is not None and not batch_audit_enabled:
                    break

        entries.append(
            {
                "scene_idx": scene_idx,
                "scene": scene,
                "dest_mp4": dest_mp4,
                "trans_sfx_path": trans_sfx_path,
                "anim_sfx_path": anim_sfx_path,
                "topic_terms": topic_terms,
                "queries": queries,
                "generic_fallbacks": generic_fallbacks,
                "chosen_cand": chosen_cand,
                "chosen_info": chosen_info,
                "init_cand": init_cand,
                "init_info": init_info,
                "init_raw": init_raw,
                "init_mscore": init_mscore,
                "extra_tier1": extra_tier1,
                "fallback_cand": None,
                "fallback_info": None,
                "fallback_raw": None,
                "fallback_meta_score": 0,
            }
        )

    # Phase 2: 1-call Batch Contact-Sheet Vision Audit across all initial Tier-1 clips
    batch_shots: list[tuple[int, SceneSpec, Path]] = [
        (ent["scene_idx"] + 1, ent["scene"], ent["init_raw"])
        for ent in entries
        if "prebuilt_asset" not in ent and ent["chosen_cand"] is None and ent["init_raw"] is not None
    ]
    batch_verdicts: dict[int, tuple[bool, bool, float, str]] = {}
    if batch_shots and batch_audit_enabled:
        batch_verdicts = batch_audit_broll_contact_sheet(batch_shots, assets_dir / "_broll_contact_sheet.jpg")

    for ent in entries:
        if "prebuilt_asset" in ent or ent["chosen_cand"] is not None or ent["init_raw"] is None:
            continue
        shot_idx = ent["scene_idx"] + 1
        scene = ent["scene"]
        cand = ent["init_cand"]
        raw_path = ent["init_raw"]
        raw_info = ent["init_info"]
        m_score = ent["init_mscore"]
        dest_mp4 = ent["dest_mp4"]

        if batch_audit_enabled and shot_idx in batch_verdicts:
            ok, clean, _sc_score, reason = batch_verdicts[shot_idx]
        else:
            ok, clean, reason = vision_screen_clip(raw_path, scene)
            if not ok and "rate-limit" in reason.lower() and m_score >= 1:
                ok = True
        print(f"[asset_scout] {scene.scene_id} audit (shot={shot_idx}, meta={m_score}, src={cand.source}) '{cand.title[:48]}': ok={ok} ({reason[:125]})")

        if ok:
            norm = _transcode_to_mp4(raw_path, raw_info, dest_mp4, source=cand.source)
            if norm:
                ent["chosen_cand"], ent["chosen_info"] = cand, norm
                ent["init_raw"] = None
                continue
        eligible_fb = clean and (m_score >= 1 or cand.source in ("pixabay", "coverr", "pexels", "wikimedia_commons"))
        if eligible_fb:
            fb_raw = dest_mp4.with_name(f"{dest_mp4.stem}_fb.raw")
            raw_path.replace(fb_raw)
            ent["fallback_cand"], ent["fallback_info"], ent["fallback_raw"], ent["fallback_meta_score"] = (
                cand, raw_info, fb_raw, m_score
            )
        else:
            raw_path.unlink(missing_ok=True)
        ent["init_raw"] = None

    # Phase 3: Only failed or Tier-1-empty scenes escalate cleanly (Alt Tier 1 -> Tier 2 Photo KB -> Tier 3 AI -> Ambient)
    results: list[SceneAsset] = []
    for ent in entries:
        if "prebuilt_asset" in ent:
            results.append(ent["prebuilt_asset"])
            continue

        scene: SceneSpec = ent["scene"]
        dest_mp4: Path = ent["dest_mp4"]
        chosen_cand: Optional[VideoCandidate] = ent["chosen_cand"]
        chosen_info: Optional[dict] = ent["chosen_info"]
        fallback_cand: Optional[VideoCandidate] = ent["fallback_cand"]
        fallback_info: Optional[dict] = ent["fallback_info"]
        fallback_raw: Optional[Path] = ent["fallback_raw"]
        fallback_meta_score: int = ent["fallback_meta_score"]
        topic_terms: set[str] = ent["topic_terms"]
        queries: list[str] = ent["queries"]
        generic_fallbacks: list[str] = ent["generic_fallbacks"]
        raw_scene_dur = getattr(scene, "duration_sec", None) or getattr(scene, "target_duration_sec", None) or getattr(scene, "duration", None) or 7.0
        clip_dur = max(5.5, min(10.0, float(raw_scene_dur)))

        # 3A: Try at most 1 alternate Tier-1 candidate if Phase 2 failed
        if chosen_cand is None and ent["extra_tier1"]:
            for m_score, cand, is_generic_q in ent["extra_tier1"][:1]:
                stem = cand.title.lower()[:32]
                if cand.download_url in used_urls or cand.page_url in used_urls or stem in used_stems:
                    continue
                raw_path = dest_mp4.with_suffix(".raw_dl")
                raw_info = _download_raw_clip(cand, raw_path)
                used_urls.add(cand.download_url)
                used_urls.add(cand.page_url)
                used_stems.add(stem)
                if not raw_info:
                    continue
                ok, clean, reason = vision_screen_clip(raw_path, scene)
                print(f"[asset_scout] {scene.scene_id} alt-T1 '{cand.title[:48]}': ok={ok} ({reason[:120]})")
                if not ok and "rate-limit" in reason.lower() and m_score >= 1:
                    ok = True
                if ok:
                    norm = _transcode_to_mp4(raw_path, raw_info, dest_mp4, source=cand.source)
                    if norm:
                        chosen_cand, chosen_info = cand, norm
                        break
                elif clean and (fallback_cand is None or m_score > fallback_meta_score):
                    if fallback_raw is not None:
                        fallback_raw.unlink(missing_ok=True)
                    fb_raw = dest_mp4.with_name(f"{dest_mp4.stem}_fb.raw")
                    raw_path.replace(fb_raw)
                    fallback_cand, fallback_info, fallback_raw, fallback_meta_score = cand, raw_info, fb_raw, m_score
                else:
                    raw_path.unlink(missing_ok=True)

        # 3B: Tier 2 Real Photography (Unsplash -> Pixabay Photo -> Openverse -> Pexels Photo -> Wikimedia Photo) + Ken Burns
        photo_fb_cand: Optional[VideoCandidate] = None
        photo_fb_info: Optional[dict] = None
        photo_fb_path: Optional[Path] = None
        if chosen_cand is None:
            photo_screens = 0
            for pq in (queries[:3] + generic_fallbacks[:2]):
                if photo_screens >= 2:
                    break
                p_cands = (
                    search_unsplash_photos(pq, per_page=4)
                    + search_pixabay_photos(pq, per_page=4)
                    + search_openverse_photos(pq, limit=4)
                    + search_pexels_photos(pq, per_page=4)
                )
                if not p_cands:
                    p_cands = search_wikimedia_photos(pq, limit=4)
                p_cands.sort(
                    key=lambda pc: (
                        -_metadata_relevance_score(pc, scene, topic_terms),
                        photo_rank.get(pc.source, 9),
                    )
                )
                for pc in p_cands[:2]:
                    if photo_screens >= 2:
                        break
                    if pc.download_url in used_urls or pc.page_url in used_urls:
                        continue
                    if _metadata_relevance_score(pc, scene, topic_terms) < 0:
                        continue
                    cand_mp4 = dest_mp4.with_name(f"{dest_mp4.stem}_kb{photo_screens}.mp4")
                    kb_info = _render_photo_ken_burns_mp4(pc, cand_mp4, duration_sec=clip_dur)
                    used_urls.add(pc.download_url)
                    used_urls.add(pc.page_url)
                    if not kb_info:
                        cand_mp4.unlink(missing_ok=True)
                        continue
                    photo_screens += 1
                    ok, clean, reason = vision_screen_clip(cand_mp4, scene)
                    print(f"[asset_scout] {scene.scene_id} Tier-2 photo-KB ({pc.source}) '{pc.title[:45]}': ok={ok} ({reason[:110]})")
                    if ok:
                        cand_mp4.replace(dest_mp4)
                        chosen_cand, chosen_info = pc, kb_info
                        if photo_fb_path and photo_fb_path.exists():
                            photo_fb_path.unlink(missing_ok=True)
                        break
                    if clean and photo_fb_cand is None:
                        photo_fb_cand, photo_fb_info, photo_fb_path = pc, kb_info, cand_mp4
                    else:
                        cand_mp4.unlink(missing_ok=True)
                if chosen_cand:
                    break

        # Reclaim clean Tier-1 fallback if meta score >= 1, else clean Tier-2 Photo Ken Burns fallback
        if chosen_cand is None and fallback_cand is not None and fallback_raw is not None and fallback_meta_score >= 1:
            norm_info = _transcode_to_mp4(fallback_raw, fallback_info or {}, dest_mp4, source=fallback_cand.source)
            if norm_info:
                chosen_cand, chosen_info = fallback_cand, norm_info
                fallback_raw = None
                print(f"[asset_scout] {scene.scene_id}: memakai Tier-1 video fallback aman (meta={fallback_meta_score})")

        if chosen_cand is None and photo_fb_cand is not None and photo_fb_path and photo_fb_path.exists():
            photo_fb_path.replace(dest_mp4)
            chosen_cand, chosen_info = photo_fb_cand, photo_fb_info
            print(f"[asset_scout] {scene.scene_id}: memakai Tier-2 Real Photo Ken Burns fallback ('{chosen_cand.title[:45]}')")

        if chosen_cand is None and fallback_cand is not None and fallback_raw is not None and fallback_raw.exists():
            norm_info = _transcode_to_mp4(fallback_raw, fallback_info or {}, dest_mp4, source=fallback_cand.source)
            if norm_info:
                chosen_cand, chosen_info = fallback_cand, norm_info
                fallback_raw = None
                print(f"[asset_scout] {scene.scene_id}: memakai Tier-1 fallback bersih")

        if fallback_raw is not None and fallback_raw.exists():
            fallback_raw.unlink(missing_ok=True)
        if photo_fb_path is not None and photo_fb_path.exists() and chosen_cand != photo_fb_cand:
            photo_fb_path.unlink(missing_ok=True)

        # 3C: Tier 3 AI Scene Generation (Cloudflare Workers AI Flux / Gemini Imagen 9:16 + Ken Burns)
        if chosen_cand is None:
            ai_hit = _generate_tier3_ai_scene_mp4(scene, dest_mp4, duration_sec=clip_dur)
            if ai_hit:
                chosen_cand, chosen_info = ai_hit
                print(f"[asset_scout] {scene.scene_id}: memakai Tier-3 AI Scene ({chosen_cand.source})")

        # 3D: Zero Hard-Crash Safety Fallback (reuse previous footage if rerender, else rendered editorial ambient scene)
        if (chosen_cand is None or chosen_info is None) and existing_assets and scene.scene_id in existing_assets:
            print(f"[asset_scout] {scene.scene_id}: memakai footage sebelumnya")
            results.append(existing_assets[scene.scene_id])
            continue

        if chosen_cand is None or chosen_info is None:
            amb_hit = _render_tier3_ambient_mp4(scene, dest_mp4, duration_sec=clip_dur)
            if amb_hit:
                chosen_cand, chosen_info = amb_hit
                print(f"[asset_scout] {scene.scene_id}: zero-crash fallback ke Tier-3 Ambient Scene")

        if chosen_cand is None or chosen_info is None:
            raise RuntimeError(
                f"Agent 6 could not synthesize fallback visual for {scene.scene_id} "
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
                sfx_path=ent["trans_sfx_path"],
                animation_sfx_path=ent["anim_sfx_path"],
                notes=chosen_cand.title,
            )
        )

    if tried_urls_out is not None:
        tried_urls_out.update(used_urls)
    return Agent6Output(assets=results, bgm_path=memory.get_bgm_path())
