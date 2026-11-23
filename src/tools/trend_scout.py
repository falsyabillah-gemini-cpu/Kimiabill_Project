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
        "kimia", "reaksi", "molekul", "atom", "oksida", "asam", "basa", "korosi",
        "chemistry", "chemical", "molecule", "catalyst", "battery", "element", "polymer", "material",
    ),
    "fisika": (
        "fisika", "kuantum", "gravitasi", "cahaya", "gerhana", "komet", "magnet", "plasma", "superkonduktor",
        "physics", "quantum", "gravity", "black hole", "laser", "photon", "fusion", "astrophysics", "eclipse",
    ),
    "matematika": (
        "matematika", "bilangan prima", "fraktal", "probabilitas", "paradoks", "geometri", "algoritma",
        "math", "mathematics", "prime number", "geometry", "theorem", "topology", "fibonacci", "pi ",
    ),
    "medisinal": (
        "medis", "obat", "vaksin", "antibiotik", "kanker", "otak", "tidur", "kafein", "imun", "terapi",
        "medicine", "drug", "clinical", "vaccine", "neuroscience", "immune", "cancer", "pharmacology", "antibiotic",
    ),
    "biologi": (
        "biologi", "dna", "genetik", "sel", "bakteri", "hewan", "spesies", "evolusi", "jamur", "laut dalam",
        "biology", "species", "evolution", "genetics", "cell", "microbe", "deep sea", "organism", "ecosystem",
    ),
}

FEEDS: list[dict[str, str]] = [
    {
        "name": "Google News ID - Sains Viral",
        "url": "https://news.google.com/rss/search?q=fenomena+sains+OR+penemuan+kimia+OR+fisika+OR+biologi+OR+medis+when:3d&hl=id&gl=ID&ceid=ID:id",
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


def fetch_viral_science_topics(
    lookback_hours: int = 96,
    max_items: int = 10,
    weekend_paper_mode: bool = False,
) -> list[dict[str, Any]]:
    """Fetch fresh trending pure-science headlines & papers from live RSS feeds (0 LLM tokens)."""
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
                disc, hits = _classify_discipline(combined)
                if not disc or hits < 1:
                    continue

                seen_titles.add(title.lower())
                recency_bonus = 2.0 if (pub_dt and (now - pub_dt).total_seconds() < 36 * 3600) else 1.0
                lang_bonus = 1.5 if feed["lang"] == "id" else 1.0
                paper_bonus = 2.5 if (
                    weekend_paper_mode and any(k in feed["name"] for k in ("Nature", "Science", "Phys.org", "ScienceDaily"))
                ) else 0.0
                viral_score = round(hits * 2.0 + recency_bonus + lang_bonus + paper_bonus, 2)

                results.append(
                    {
                        "source": feed["name"],
                        "lang": feed["lang"],
                        "discipline": disc,
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
) -> dict[str, Any]:
    """Build the viral-first brief for Agent 1, including Weekend KARAT research paper focus."""
    memory = StudioMemory(base_dir)
    past = memory.list_past_topics(limit=50)
    past_titles = {p["title"].lower() for p in past}

    effective_lookback = 72 if weekend_paper_mode else lookback_hours
    viral_raw = fetch_viral_science_topics(
        lookback_hours=effective_lookback,
        max_items=14,
        weekend_paper_mode=weekend_paper_mode,
    )
    viral_fresh = [v for v in viral_raw if v["title"].lower() not in past_titles]

    fallback_ideas = memory.get_balanced_unused_ideas(count=5)

    if weekend_paper_mode:
        mode = "karat_weekend_research_paper"
    elif len(viral_fresh) >= min_viral_matches:
        mode = "viral_trend"
    elif len(viral_fresh) > 0:
        mode = "hybrid"
    else:
        mode = "idea_bank_fallback"

    return {
        "mode_recommended": mode,
        "section": "KARAT" if weekend_paper_mode else "REGULER",
        "section_tagline": "KARAT — Kabar Riset Terkini • Dari Saintis untuk Masyarakat" if weekend_paper_mode else "",
        "viral_count": len(viral_fresh),
        "viral_candidates": viral_fresh[:8],
        "idea_bank_fallback_candidates": fallback_ideas,
        "discipline_balance_so_far": memory.discipline_counts(),
        "past_topics_to_avoid": past[:15],
    }
