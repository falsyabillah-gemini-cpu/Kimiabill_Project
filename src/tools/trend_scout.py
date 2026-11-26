"""Agent 1 Pre-Scout (0-Token): Viral Pure-Science Trend Detector + Idea Bank Fallback.

Priority 1 (Viral-First):
  Scans live RSS/news feeds for trending pure-science phenomena across:
  Kimia, Fisika, Matematika, Medisinal, and Biologi.

Priority 2 (Automatic Fallback):
  If no fresh viral pure-science topic passes the relevance filter (or all are already covered),
  automatically pulls balanced unused ideas from `data/idea_bank.json` via StudioMemory.
"""
from __future__ import annotations

import email.utils
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import urllib3.util.connection as _urllib3_conn

from src.memory import StudioMemory

_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

USER_AGENT = "KimiabillStudio/1.0 (science-trend-scout)"

DISCIPLINE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kimia": (
        "kimia", "reaksi", "molekul", "atom", "oksida", "asam", "basa", "korosi", "minyak", "garam", "sabun", "asap",
        "chemistry", "chemical", "molecule", "catalyst", "battery", "element", "polymer", "material", "cooking", "flavor",
    ),
    "fisika": (
        "fisika", "kuantum", "gravitasi", "cahaya", "gerhana", "komet", "magnet", "plasma", "superkonduktor", "panas", "suara",
        "physics", "quantum", "gravity", "black hole", "laser", "photon", "fusion", "astrophysics", "eclipse", "heat", "sound",
    ),
    "matematika": (
        "matematika", "bilangan prima", "fraktal", "probabilitas", "paradoks", "geometri", "algoritma", "peluang", "pola",
        "math", "mathematics", "prime number", "geometry", "theorem", "topology", "fibonacci", "probability", "paradox",
    ),
    "medisinal": (
        "medis", "obat", "vaksin", "antibiotik", "kanker", "otak", "tidur", "kafein", "imun", "terapi", "nyeri", "usus", "jantung",
        "medicine", "drug", "clinical", "vaccine", "neuroscience", "immune", "cancer", "pharmacology", "antibiotic", "sleep", "pain", "gut",
    ),
    "biologi": (
        "biologi", "dna", "genetik", "sel", "bakteri", "hewan", "spesies", "evolusi", "jamur", "laut dalam", "kulit", "mata", "lidah",
        "biology", "species", "evolution", "genetics", "cell", "microbe", "deep sea", "organism", "ecosystem", "aging", "skin",
    ),
}

EVERYDAY_RELATABILITY_KEYWORDS: tuple[str, ...] = (
    "tidur", "sleep", "makan", "food", "diet", "kopi", "coffee", "caffeine", "kafein",
    "otak", "brain", "nyeri", "pain", "usus", "gut", "jantung", "heart", "kulit", "skin",
    "mata", "eye", "nanas", "bawang", "minyak", "panas", "heat", "dingin", "cold",
    "plastik", "plastic", "microplastic", "mikroplastik", "air", "water", "udara", "air pollution",
    "stres", "stress", "aging", "penuaan", "bakteri", "virus", "gula", "sugar", "obat", "drug",
    "dapur", "kitchen", "rumah", "house", "handphone", "screen", "suhu", "weather", "cuaca",
)

ID_INSPIRATION_YT_CHANNELS: list[dict[str, str]] = [
    {"name": "@KokBisa", "channel_id": "UCu0yQD7NFMyLu_-TmKa4Hqg"},
    {"name": "@KacamataPenjelajah", "channel_id": "UCKUEP6DQfvqBdI3AzA04T0A"},
    {"name": "@FajrulFx", "channel_id": "UCUYgCukQfUN2DImPYrBiV4g"},
]

VIRAL_STYLE_BENCHMARKS: list[str] = [
    "@KokBisa (2.5M views, 196K likes): 'Kenapa Arah Asap Bakaran Ngikutin Kita Terus?!' -> momen keseharian semua orang + personifikasi 'ngejar'",
    "@KacamataPenjelajah (11M views, 417K likes): 'KUAT dan TAHAN LAMA' + cover 2-3 kata kontras -> judul 4 kata tanpa spoiler, memicu bingung & penasaran",
    "@KacamataPenjelajah (568K / 157K views): 'Beton Bakteri, Jago Regenerasi' | 'Terlihat Sehat padahal Sekarat' -> kontras aneh & ringkas",
    "Marbel (4.56M views): 'GUNUNG ITU BERASAP! Apakah ia sakit?!' -> personifikasi fenomena alam dengan bahasa manusia sehari-hari",
    "@NileRed / @veritasium (3.5M-10M views): tebakan eksperimen di awal video ('What happens if...') & metafora emosional ('Extracting the soul from coffee')",
]

FEEDS: list[dict[str, str]] = [
    {
        "name": "Google News ID - Sains Viral",
        "url": "https://news.google.com/rss/search?q=fenomena+sains+OR+penemuan+kimia+OR+fisika+OR+biologi+OR+medis+when:3d&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "Google News ID - Kesehatan & Riset Sehari-hari",
        "url": "https://news.google.com/rss/search?q=riset+kesehatan+OR+penelitian+tidur+OR+makanan+OR+otak+tubuh+when:4d&hl=id&gl=ID&ceid=ID:id",
        "lang": "id",
    },
    {
        "name": "ScienceDaily Top Science",
        "url": "https://www.sciencedaily.com/rss/top/science.xml",
        "lang": "en",
    },
    {
        "name": "Phys.org Breaking Science",
        "url": "https://phys.org/rss-feed/breaking/",
        "lang": "en",
    },
    {
        "name": "Quanta Magazine (Math/Physics/Bio)",
        "url": "https://api.quantamagazine.org/feed/",
        "lang": "en",
    },
    {
        "name": "Nature Latest Research Papers",
        "url": "https://www.nature.com/nature.rss",
        "lang": "en",
    },
    {
        "name": "Science / AAAS Breaking Research",
        "url": "https://www.science.org/rss/news_current.xml",
        "lang": "en",
    },
]


def _classify_discipline(text: str) -> tuple[str | None, int]:
    low = text.lower()
    best_disc = None
    best_hits = 0
    for disc, kws in DISCIPLINE_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in low)
        if hits > best_hits:
            best_hits = hits
            best_disc = disc
    return best_disc, best_hits


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_indonesian_science_inspiration(max_per_channel: int = 4) -> list[dict[str, str]]:
    """Fetch recent titles from high-performing Indonesian science YT channels via 0-key public Atom RSS."""
    out: list[dict[str, str]] = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for ch in ID_INSPIRATION_YT_CHANNELS:
        try:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['channel_id']}"
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            for entry in root.findall("atom:entry", ns)[:max_per_channel]:
                t = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
                if t:
                    out.append({"channel": ch["name"], "title": t})
        except Exception:
            continue
    return out


def fetch_viral_science_topics(
    lookback_hours: int = 96,
    max_items: int = 10,
    weekend_paper_mode: bool = False,
) -> list[dict[str, Any]]:
    """Fetch fresh trending pure-science headlines & papers from live RSS feeds (0 LLM tokens), prioritizing everyday relatability."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    results: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for feed in FEEDS:
        try:
            resp = requests.get(
                feed["url"],
                headers={"User-Agent": USER_AGENT},
                timeout=12,
            )
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = re.sub(r"<[^>]+>", " ", (item.findtext("description") or "")).strip()
                pub_dt = _parse_pub_date(item.findtext("pubDate"))
                if not title or title.lower() in seen_titles:
                    continue
                if pub_dt and pub_dt < cutoff:
                    continue

                combined = f"{title} {desc}"
                low_comb = combined.lower()
                disc, hits = _classify_discipline(combined)
                if not disc or hits < 1:
                    continue

                seen_titles.add(title.lower())
                recency_bonus = 2.0 if (pub_dt and (now - pub_dt).total_seconds() < 36 * 3600) else 1.0
                lang_bonus = 1.5 if feed["lang"] == "id" else 1.0
                everyday_hits = sum(1 for kw in EVERYDAY_RELATABILITY_KEYWORDS if kw in low_comb)
                everyday_bonus = min(6.0, everyday_hits * 2.0)
                paper_bonus = 2.5 if (
                    weekend_paper_mode and any(k in feed["name"] for k in ("Nature", "Science", "Phys.org", "ScienceDaily"))
                ) else 0.0
                viral_score = round(hits * 2.0 + recency_bonus + lang_bonus + everyday_bonus + paper_bonus, 2)

                results.append(
                    {
                        "source": feed["name"],
                        "lang": feed["lang"],
                        "discipline": disc,
                        "topic_bucket": "everyday" if everyday_hits > 0 else "research",
                        "everyday_relatable": everyday_hits > 0,
                        "title": title,
                        "summary": desc[:320],
                        "url": link,
                        "published_at": pub_dt.isoformat() if pub_dt else None,
                        "viral_score": viral_score,
                    }
                )
        except Exception:
            continue

    results.sort(key=lambda x: x["viral_score"], reverse=True)
    return results[:max_items]


def build_agent1_brief(
    base_dir: Path,
    lookback_hours: int = 96,
    min_viral_matches: int = 2,
    weekend_paper_mode: bool = False,
    kitchen_chemistry_mode: bool = False,
    extra_avoid_titles: list[str] | None = None,
) -> dict[str, Any]:
    """Build the viral-first brief for Agent 1 with strict history/blacklist deduplication and 70/30 ratio guidance."""
    from src.tools.topic_guard import (
        check_topic_duplication,
        compute_topic_bucket_guidance,
        load_published_history,
        load_rejected_topics,
    )

    memory = StudioMemory(base_dir)
    past = memory.list_past_topics(limit=50)
    past_titles = [p["title"] for p in past if p.get("title")] + list(extra_avoid_titles or [])

    effective_lookback = 72 if weekend_paper_mode else lookback_hours
    viral_raw = fetch_viral_science_topics(
        lookback_hours=effective_lookback,
        max_items=24,
        weekend_paper_mode=weekend_paper_mode,
    )
    viral_fresh: list[dict[str, Any]] = []
    for cand in viral_raw:
        is_dup, _reason = check_topic_duplication(
            cand.get("title", ""),
            source_url=cand.get("url"),
            phenomenon=cand.get("summary", ""),
            avoid_titles=past_titles,
            base_dir=base_dir,
        )
        if not is_dup:
            viral_fresh.append(cand)
    viral_fresh = viral_fresh[:10]

    id_inspo = fetch_indonesian_science_inspiration(max_per_channel=3)

    fallback_ideas = (
        memory.get_kitchen_chemistry_ideas(count=5, avoid_titles=past_titles)
        if kitchen_chemistry_mode
        else memory.get_balanced_unused_ideas(count=5, avoid_titles=past_titles)
    )

    if weekend_paper_mode:
        mode = "karat_weekend_research_paper"
    elif kitchen_chemistry_mode:
        mode = "kitchen_chemistry_daily"
    elif len(viral_fresh) >= min_viral_matches:
        mode = "viral_trend"
    elif len(viral_fresh) > 0:
        mode = "hybrid"
    else:
        mode = "idea_bank_fallback"

    section = "KARAT" if weekend_paper_mode else ("DAPUR" if kitchen_chemistry_mode else "REGULER")
    tagline = (
        "KARAT — Kabar Riset Terkini • Riset Baru yang Dekat dengan Hidup Kita"
        if weekend_paper_mode
        else ("KIMIA DAPUR • Sains Sehari-hari di Rumah" if kitchen_chemistry_mode else "")
    )
    ratio_guidance = compute_topic_bucket_guidance(base_dir)
    recent_hist_titles = [str(r.get("title") or "") for r in load_published_history(base_dir)[-20:] if r.get("title")]
    rejected_titles = [str(r.get("title") or "") for r in load_rejected_topics(base_dir)[-20:] if r.get("title")]

    return {
        "mode_recommended": mode,
        "section": section,
        "section_tagline": tagline,
        "topic_ratio_guidance": ratio_guidance,
        "viral_count": len(viral_fresh),
        "viral_candidates": viral_fresh[:8],
        "idea_bank_fallback_candidates": fallback_ideas,
        "indonesian_viral_style_benchmarks": VIRAL_STYLE_BENCHMARKS,
        "inspiration_channel_titles": id_inspo[:9],
        "discipline_balance_so_far": memory.discipline_counts(),
        "past_topics_to_avoid": past[:15],
        "recent_published_titles": recent_hist_titles,
        "rejected_topics_blacklist": rejected_titles,
    }
