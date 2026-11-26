"""Persistent topic history, rejection blacklist, strict concept deduplication, and 70/30 ratio tracking."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PUBLISHED_HISTORY_PATH = BASE_DIR / "data" / "published_history.json"
REJECTED_TOPICS_PATH = BASE_DIR / "data" / "rejected_topics.json"

STOPWORDS: frozenset[str] = frozenset({
    "yang", "dan", "di", "ke", "dari", "ini", "itu", "bisa", "kok", "kenapa", "mengapa",
    "ternyata", "pada", "dalam", "untuk", "dengan", "atau", "saat", "ketika", "karena",
    "jadi", "akan", "tidak", "bukan", "ada", "hal", "fakta", "sains", "rahasia", "misteri",
    "unik", "aneh", "bikin", "kalau", "lebih", "paling", "justru", "padahal", "lagi", "terus",
    "the", "and", "for", "with", "that", "this", "why", "how", "what", "when", "from", "into",
    "are", "was", "new", "study", "scientists", "researchers", "research", "shows", "finds",
    "kimiabill", "video", "shorts", "episode", "everyday", "science",
})

BILINGUAL_CONCEPT_ALIASES: dict[str, str] = {
    "sunscreen": "tabir_surya",
    "tabir": "tabir_surya",
    "surya": "tabir_surya",
    "ultraviolet": "uv",
    "radiasi": "uv",
    "radiation": "uv",
    "purba": "ancient",
    "ancient": "ancient",
    "dna": "dna",
    "genetic": "dna",
    "genetik": "dna",
    "sleep": "tidur",
    "tidur": "tidur",
    "insomnia": "tidur",
    "glymphatic": "glimfatik",
    "glimfatik": "glimfatik",
    "nanas": "nanas",
    "pineapple": "nanas",
    "bromelain": "bromelain",
    "daging": "daging",
    "meat": "daging",
    "capsaicin": "kapsaisin",
    "kapsaikin": "kapsaisin",
    "kapsaisin": "kapsaisin",
    "pedas": "pedas",
    "spicy": "pedas",
    "chili": "pedas",
    "cabai": "pedas",
    "sambal": "pedas",
    "karat": "karat",
    "berkarat": "karat",
    "korosi": "karat",
    "rust": "karat",
    "corrosion": "karat",
    "besi": "besi",
    "iron": "besi",
    "keriput": "keriput",
    "wrinkle": "keriput",
    "wrinkled": "keriput",
    "osmosis": "osmosis",
    "tembakau": "tembakau",
    "tobacco": "tembakau",
    "kanker": "kanker",
    "cancer": "kanker",
    "perak": "perak",
    "silver": "perak",
    "radioaktif": "radioaktif",
    "radioactive": "radioaktif",
    "nyeri": "nyeri",
    "pain": "nyeri",
    "obat": "obat",
    "drug": "obat",
    "usus": "usus",
    "gut": "usus",
    "sabun": "sabun",
    "soap": "sabun",
    "minyak": "minyak",
    "oil": "minyak",
    "asap": "asap",
    "smoke": "asap",
    "kopi": "kopi",
    "coffee": "kopi",
    "kafein": "kafein",
    "caffeine": "kafein",
    "microplastic": "mikroplastik",
    "mikroplastik": "mikroplastik",
    "microplastics": "mikroplastik",
    "bakteri": "bakteri",
    "bacteria": "bakteri",
    "hidrogel": "hidrogel",
    "hydrogel": "hidrogel",
    "tekuk": "tekuk",
    "ditekuk": "tekuk",
    "bend": "tekuk",
}


def slugify_topic(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:72] or "topik-sains"


def normalize_source_url(url: Optional[str]) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        clean_path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), clean_path, "", "", ""))
    except Exception:
        return raw.lower().rstrip("/")


def extract_topic_keywords(text: str, extra: Optional[list[str]] = None) -> list[str]:
    blob = " ".join([text or "", *(extra or [])]).lower()
    raw_tokens = re.findall(r"[a-z0-9]{2,}", blob)
    seen: list[str] = []
    for tok in raw_tokens:
        if tok in STOPWORDS or tok.isdigit():
            continue
        mapped = BILINGUAL_CONCEPT_ALIASES.get(tok, tok)
        if mapped not in STOPWORDS and mapped not in seen:
            seen.append(mapped)
    return seen[:20]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_published_history(base_dir: Path = BASE_DIR) -> list[dict[str, Any]]:
    return _load_json_list(base_dir / "data" / "published_history.json")


def load_rejected_topics(base_dir: Path = BASE_DIR) -> list[dict[str, Any]]:
    return _load_json_list(base_dir / "data" / "rejected_topics.json")


def record_rejected_topic(
    *,
    episode_id: str,
    title: str,
    topic_slug: str = "",
    source_url: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    reason: str = "Ditolak saat audit manual",
    base_dir: Path = BASE_DIR,
) -> dict[str, Any]:
    path = base_dir / "data" / "rejected_topics.json"
    items = load_rejected_topics(base_dir)
    slug = topic_slug or slugify_topic(title)
    kw = keywords or extract_topic_keywords(title)
    entry = {
        "episode_id": episode_id,
        "title": title,
        "topic_slug": slug,
        "source_url": normalize_source_url(source_url) or None,
        "keywords": kw,
        "reason": reason,
        "rejected_at": datetime.now(WIB).isoformat(timespec="seconds"),
    }
    items = [x for x in items if x.get("episode_id") != episode_id and x.get("topic_slug") != slug]
    items.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items[-365:], ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def check_topic_duplication(
    title: str,
    *,
    source_url: Optional[str] = None,
    phenomenon: str = "",
    keywords: Optional[list[str]] = None,
    avoid_titles: Optional[list[str]] = None,
    base_dir: Path = BASE_DIR,
) -> tuple[bool, str]:
    """Return (is_duplicate, reason) checking published_history.json, rejected_topics.json, and avoid_titles."""
    norm_url = normalize_source_url(source_url)
    cand_slug = slugify_topic(title)
    cand_kws = set(extract_topic_keywords(f"{title} {phenomenon}", extra=keywords))

    corpus: list[tuple[str, dict[str, Any]]] = []
    for row in load_published_history(base_dir):
        corpus.append(("published_history", row))
    for row in load_rejected_topics(base_dir):
        corpus.append(("rejected_topics", row))
    for row in _load_json_list(base_dir / "data" / "backup_queue.json"):
        corpus.append(("backup_queue", row))
    for av in avoid_titles or []:
        if (av or "").strip():
            corpus.append(("avoid_titles", {"title": av, "topic_slug": slugify_topic(av), "keywords": extract_topic_keywords(av)}))

    for src_name, row in corpus:
        row_url = normalize_source_url(row.get("source_url") or row.get("viral_source_url"))
        if norm_url and row_url and norm_url == row_url:
            return True, f"Duplicate source_url already in {src_name}: {row_url}"

        row_title = str(row.get("title") or "").strip()
        row_slug = str(row.get("topic_slug") or slugify_topic(row_title))
        if cand_slug and row_slug and cand_slug == row_slug:
            return True, f"Identical topic_slug '{cand_slug}' in {src_name}"

        row_kws = set(row.get("keywords") or extract_topic_keywords(f"{row_title} {row.get('phenomenon', '')} {row.get('hook_line', '')}"))
        if not cand_kws or not row_kws:
            continue
        shared = cand_kws & row_kws
        union = cand_kws | row_kws
        jaccard = len(shared) / max(1, len(union))
        overlap_ratio = len(shared) / max(1, min(len(cand_kws), len(row_kws)))
        if len(shared) >= 3 or (len(shared) >= 2 and (jaccard >= 0.35 or overlap_ratio >= 0.50)):
            return True, (
                f"High concept similarity with '{row_title}' in {src_name} "
                f"(shared={sorted(shared)}, jaccard={jaccard:.2f})"
            )

    return False, ""


def compute_topic_bucket_guidance(
    base_dir: Path = BASE_DIR,
    window: int = 10,
) -> dict[str, Any]:
    """Compute recent everyday (70% target) vs research (30% target) topic split."""
    hist = load_published_history(base_dir)[-window:]
    everyday_cnt = 0
    research_cnt = 0
    for row in hist:
        bucket = str(row.get("topic_bucket") or "").lower().strip()
        if not bucket:
            sec = str(row.get("section") or "").upper()
            bucket = "research" if sec == "KARAT" else "everyday"
        if bucket == "research":
            research_cnt += 1
        else:
            everyday_cnt += 1

    total = max(1, everyday_cnt + research_cnt)
    ev_pct = round(everyday_cnt / total, 2)
    res_pct = round(research_cnt / total, 2)
    preferred_bucket = "everyday" if ev_pct < 0.70 else "research"
    return {
        "window_episodes": len(hist),
        "everyday_count": everyday_cnt,
        "research_count": research_cnt,
        "everyday_ratio": ev_pct,
        "research_ratio": res_pct,
        "target_everyday_ratio": 0.70,
        "target_research_ratio": 0.30,
        "preferred_next_bucket": preferred_bucket,
    }
