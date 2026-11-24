"""Agent 8: Weekly Social Media Engagement & Cross-Agent Growth Auditor.

Runs every Sunday at 20:00 WIB (after Sunday's 19:00 WIB video is uploaded):
1. Collects this week's published episodes + live YouTube views/likes/engagement signals.
2. Performs a deep engagement & retention audit (hook strength, discipline performance,
   pacing, visual clarity, CTA effectiveness).
3. Writes concrete next-week improvement directives to `data/weekly_agent_directives.json`
   which are automatically injected into Agents 1, 2, 3, and 5 so quality improves every week.
4. Saves the weekly audit report to Google Drive (`Kimiabill_Studio/Weekly_Audits`) and emails
   the full Indonesian report to `researchfalsyabillah@gmail.com`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
import yaml

from src.providers import call_structured_agent
from src.state import Agent8WeeklyAuditOutput, AudienceRequestItem, NextWeekTopicMapOutput
from src.tools.gdrive_sync import _normalize_webhook_url

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
HISTORY_PATH = BASE_DIR / "data" / "published_history.json"
DIRECTIVES_PATH = BASE_DIR / "data" / "weekly_agent_directives.json"
AUDITS_DIR = BASE_DIR / "data" / "weekly_audits"
REQUESTS_PATH = BASE_DIR / "data" / "audience_requests.json"
MASTER_CSV_PATH = BASE_DIR / "data" / "KIMIABILL_MASTER_DATABASE.csv"


def load_audience_requests() -> list[dict[str, Any]]:
    if not REQUESTS_PATH.exists():
        return []
    try:
        data = json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def queue_audience_requests(
    new_requests: list[AudienceRequestItem],
    week_ending: str,
) -> list[dict[str, Any]]:
    """Persist new pure-science viewer requests into data/audience_requests.json without duplicates."""
    existing = load_audience_requests()
    seen_ids = {x.get("source_comment_id") for x in existing if x.get("source_comment_id")}
    seen_sigs = {
        (str(x.get("requester_name") or "").strip().lower(), str(x.get("source_comment") or "").strip().lower()[:80])
        for x in existing
    }
    added: list[dict[str, Any]] = []
    for req in new_requests:
        comment_clean = req.source_comment.strip()
        topic_clean = req.topic_title.strip()
        requester_clean = (req.requester_name or "Penonton").strip()
        if not comment_clean or not topic_clean:
            continue
        sig = (requester_clean.lower(), comment_clean.lower()[:80])
        if (req.source_comment_id and req.source_comment_id in seen_ids) or sig in seen_sigs:
            continue
        seed = req.source_comment_id or f"{requester_clean}:{comment_clean}"
        req_id = req.request_id.strip() or f"req_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]}"
        entry = {
            "request_id": req_id,
            "requester_name": requester_clean,
            "source_comment": comment_clean[:260],
            "source_video_id": req.source_video_id,
            "source_comment_id": req.source_comment_id,
            "topic_title": topic_clean,
            "discipline": req.discipline,
            "status": "pending",
            "created_week": week_ending,
            "fulfilled_episode_id": None,
            "fulfilled_youtube_url": None,
        }
        existing.append(entry)
        added.append(entry)
        if req.source_comment_id:
            seen_ids.add(req.source_comment_id)
        seen_sigs.add(sig)

    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_PATH.write_text(json.dumps(existing[-120:], ensure_ascii=False, indent=2), encoding="utf-8")
    return added


def get_next_pending_audience_request() -> Optional[dict[str, Any]]:
    for item in load_audience_requests():
        if item.get("status") == "pending":
            return item
    return None


def mark_audience_request_fulfilled(
    request_id: str,
    episode_id: str,
    youtube_url: Optional[str] = None,
) -> None:
    if not request_id or not REQUESTS_PATH.exists():
        return
    items = load_audience_requests()
    changed = False
    for it in items:
        if it.get("request_id") == request_id:
            it["status"] = "published"
            it["fulfilled_episode_id"] = episode_id
            if youtube_url:
                it["fulfilled_youtube_url"] = youtube_url
            changed = True
    if changed:
        REQUESTS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def record_published_episode(
    *,
    episode_id: str,
    title: str,
    discipline: str,
    section: str = "REGULER",
    youtube_url: Optional[str] = None,
    gdrive_folder_url: Optional[str] = None,
    qa_score: Optional[float] = None,
    duration_sec: Optional[float] = None,
    hook_line: str = "",
    revisions_used: int = 0,
    topic_attempts_used: int = 1,
    production_seconds: Optional[float] = None,
) -> None:
    """Persist each published episode + 0-token efficiency telemetry into data/published_history.json."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    if HISTORY_PATH.exists():
        try:
            items = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            items = []

    try:
        from src.providers import get_model_usage_summary

        agent_models = get_model_usage_summary()
    except Exception:
        agent_models = {}

    record = {
        "episode_id": episode_id,
        "published_at_wib": datetime.now(WIB).isoformat(timespec="seconds"),
        "title": title,
        "discipline": discipline,
        "section": section,
        "youtube_url": youtube_url,
        "gdrive_folder_url": gdrive_folder_url,
        "qa_score": qa_score,
        "duration_sec": duration_sec,
        "hook_line": hook_line[:160],
        "revisions_used": revisions_used,
        "topic_attempts_used": topic_attempts_used,
        "production_seconds": round(production_seconds, 1) if production_seconds is not None else None,
        "publish_mode": os.environ.get("PUBLISH_MODE", "public"),
        "agent_models": agent_models,
    }
    items = [x for x in items if x.get("episode_id") != episode_id]
    items.append(record)
    HISTORY_PATH.write_text(json.dumps(items[-365:], ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from src.memory import StudioMemory

        StudioMemory(BASE_DIR).upsert_episode_analytics([record])
    except Exception:
        pass


def load_latest_agent_directives() -> dict[str, Any]:
    """Load the latest weekly improvement directives generated by Agent 8 (if available)."""
    if not DIRECTIVES_PATH.exists():
        return {}
    try:
        return json.loads(DIRECTIVES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_video_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"(?:/shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _fetch_public_video_stats(youtube_url: str) -> dict[str, Any]:
    """Fetch public view/like counts from YouTube watch page HTML (no extra credentials needed)."""
    vid = _extract_video_id(youtube_url)
    if not vid:
        return {}
    try:
        resp = requests.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            timeout=20,
        )
        if resp.status_code != 200:
            return {}
        html = resp.text
        stats: dict[str, Any] = {"video_id": vid}
        m_views = re.search(r'"viewCount"\s*:\s*"(\d+)"', html) or re.search(
            r'itemprop="interactionCount"\s+content="(\d+)"', html
        )
        if m_views:
            stats["views"] = int(m_views.group(1))
        m_likes = re.search(r'"likeCount"\s*:\s*(\d+)', html)
        if m_likes:
            stats["likes"] = int(m_likes.group(1))
        return stats
    except Exception:
        return {}


def _fetch_webhook_youtube_stats() -> dict[str, Any]:
    """Ask Apps Script webhook for YouTube Data API v3 channel/video stats if supported."""
    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        return {}
    webhook_url = _normalize_webhook_url(raw_url)
    token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
    try:
        resp = requests.post(
            webhook_url,
            json={"action": "get_youtube_stats", "token": token, "max_results": 25},
            timeout=40,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                ch = data.get("channel") or {}
                print(
                    f"[agent8] YouTube webhook OK: channel='{ch.get('title', '-')}', "
                    f"analytics_enabled={data.get('analytics_enabled')}, "
                    f"has_recent_comments={'recent_comments' in data}, "
                    f"recent_comments_count={len(data.get('recent_comments') or [])}, "
                    f"videos={len(data.get('videos') or [])}"
                )
                return data
            print(f"[agent8] YouTube webhook returned ok=False: {data}")
        else:
            print(f"[agent8] YouTube webhook HTTP {resp.status_code}")
    except Exception as exc:
        print(f"[agent8] YouTube webhook exception: {exc}")
    return {}


def _format_monetization_section(channel: dict[str, Any]) -> list[str]:
    """Compute exact progress toward YouTube Partner Program (YPP) Shorts monetization (0 tokens)."""
    subs = int(channel.get("subscribers") or 0)
    views = int(channel.get("total_views") or 0)

    def _pct(val: int, target: int) -> str:
        return f"{min(100.0, (val / target) * 100.0):.2f}%"

    sub_500_left = max(0, 500 - subs)
    sub_1k_left = max(0, 1000 - subs)
    views_3m_left = max(0, 3_000_000 - views)
    views_10m_left = max(0, 10_000_000 - views)

    return [
        "## Progres Menuju Monetisasi YouTube (YPP Shorts)",
        f"- **Status Channel Saat Ini:** {subs:,} Subscriber | {views:,} Total Views",
        f"- **Tahap 1 — Fan Funding (500 Subs + 3 Juta Views Shorts 90 hari):**",
        f"  - Subscriber: {subs:,} / 500 ({_pct(subs, 500)}) — kurang **{sub_500_left:,} subscriber lagi**",
        f"  - Views: {views:,} / 3.000.000 ({_pct(views, 3_000_000)}) — kurang **{views_3m_left:,} views lagi**",
        f"- **Tahap 2 — Monetisasi Penuh Iklan Shorts (1.000 Subs + 10 Juta Views Shorts 90 hari):**",
        f"  - Subscriber: {subs:,} / 1.000 ({_pct(subs, 1000)}) — kurang **{sub_1k_left:,} subscriber lagi**",
        f"  - Views: {views:,} / 10.000.000 ({_pct(views, 10_000_000)}) — kurang **{views_10m_left:,} views lagi**",
    ]


def _build_international_summary(
    countries: list[dict[str, Any]],
    viewer_comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize non-Indonesian YouTube Analytics views & foreign-language comments (0 AI tokens)."""
    total_c_views = sum(int(c.get("views") or 0) for c in countries)
    id_views = sum(int(c.get("views") or 0) for c in countries if str(c.get("country") or "").upper() == "ID")
    non_id_views = max(0, total_c_views - id_views)
    non_id_pct = round((non_id_views / total_c_views) * 100.0, 2) if total_c_views > 0 else 0.0

    intl_comments: list[dict[str, Any]] = []
    id_stop = ("yang", "dan", "ini", "itu", "apa", "kok", "kenapa", "bagaimana", "tolong", "bahas", "dong", "kak", "bang", "min", "keren", "mantap", "sains")
    en_hints = ("why", "how", "what", "when", "explain", "science", "chemistry", "physics", "video", "please", "cool", "amazing", "english", "subtitles", "can you", "thank", "wow")
    for c in viewer_comments:
        txt = str(c.get("text") or "").strip()
        low = f" {txt.lower()} "
        has_id = any(f" {w} " in low for w in id_stop)
        has_en = any(f" {w} " in low for w in en_hints)
        has_non_ascii_intl = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff]", txt))
        if (has_en and not has_id) or has_non_ascii_intl:
            intl_comments.append(c)

    return {
        "top_countries": countries[:10],
        "analytics_total_views": total_c_views,
        "id_views": id_views,
        "non_id_views": non_id_views,
        "non_id_views_pct": non_id_pct,
        "intl_comments": intl_comments[:10],
    }


def _is_legacy_non_science(title: str) -> bool:
    """Detect non-science legacy channel uploads (e.g. old funny/soccer fail clips) so they don't skew science ranking."""
    low = (title or "").lower()
    legacy_markers = ("#viralnest", "#funny", "soccer fails", "sports fails", "try not to laugh", "biggest fails", "went wrong")
    return any(m in low for m in legacy_markers)


def _infer_science_discipline(title: str, raw_disc: str = "") -> str:
    """Return normalized science discipline across the 5 pillars using raw value or keyword hints (0 tokens)."""
    disc = str(raw_disc or "").strip().lower()
    valid = {"kimia", "fisika", "biologi", "medisinal", "matematika"}
    if disc in valid:
        return disc
    low = (title or "").lower()
    if any(k in low for k in ("obat", "usus", "kanker", "farma", "antibiotik", "medis", "vitamin", "dokter", "imun")):
        return "medisinal"
    if any(k in low for k in ("dna", "sel ", "bakteri", "virus", "tanaman", "tembakau", "hewan", "otak", "tidur", "purba")):
        return "biologi"
    if any(k in low for k in ("kuantum", "gravitasi", "magnet", "listrik", "cahaya", "laser", "tekuk", "suara", "bunyi", "panas", "api ", "es ")):
        return "fisika"
    if any(k in low for k in ("matematika", "prima", "angka", "fraktal", "peluang", "geometri", "paradoks", "fibonacci")):
        return "matematika"
    if any(k in low for k in ("kimia", "logam", "perak", "besi", "asam", "basa", "garam", "sabun", "nanas", "bawang", "kopi", "teh", "molekul", "reaksi", "radioaktif", "dapur")):
        return "kimia"
    return "kimia"


def _build_topic_landscape(
    archived_rows: list[dict[str, Any]],
    enriched_videos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build complete 0-token topic inventory, top vs bottom topic rankings, and discipline/section breakdown."""
    merged: dict[str, dict[str, Any]] = {}
    for src_row in list(archived_rows) + list(enriched_videos):
        title = str(src_row.get("title") or "").strip()
        if not title or _is_legacy_non_science(title):
            continue
        vid = _extract_video_id(src_row.get("youtube_url") or "") or src_row.get("video_id")
        ep_id = str(src_row.get("episode_id") or "").strip()
        norm_title = re.sub(r"[^a-z0-9]+", "", title.lower())[:48]
        # Merge by normalized topic title so an unpublished/unlinked local record pairs cleanly with its live YouTube video
        key = f"title:{norm_title}" if norm_title else (f"vid:{vid}" if vid else f"ep:{ep_id}")
        cur = merged.get(key, {})
        views = max(int(cur.get("views") or 0), int(src_row.get("views") or 0))
        likes = max(int(cur.get("likes") or 0), int(src_row.get("likes") or 0))
        comments = max(int(cur.get("comments") or 0), int(src_row.get("comments") or 0))
        raw_sec = str(src_row.get("section") or cur.get("section") or "").upper()
        if "KARAT" in raw_sec or "karat" in title.lower():
            sec = "KARAT"
        elif "DAPUR" in raw_sec or "dapur" in title.lower():
            sec = "DAPUR"
        else:
            sec = "REGULER"
        raw_disc = str(src_row.get("discipline") or cur.get("discipline") or "").strip().lower()
        disc = _infer_science_discipline(title, raw_disc if raw_disc not in ("", "-", "none") else "")
        pub_at = str(src_row.get("published_at_wib") or src_row.get("published_at") or cur.get("published_at") or "")
        ret = src_row.get("avg_view_percentage") if src_row.get("avg_view_percentage") is not None else cur.get("avg_view_percentage")
        qa = src_row.get("qa_score") if src_row.get("qa_score") is not None else cur.get("qa_score")
        merged[key] = {
            "key": key,
            "episode_id": ep_id or cur.get("episode_id") or vid or "-",
            "video_id": vid or cur.get("video_id") or "",
            "title": title if len(title) >= len(cur.get("title") or "") else cur["title"],
            "discipline": disc,
            "section": sec,
            "published_at": pub_at[:10] if pub_at else "-",
            "views": views,
            "likes": likes,
            "comments": comments,
            "avg_view_percentage": ret,
            "qa_score": qa,
            "youtube_url": src_row.get("youtube_url") or cur.get("youtube_url") or "",
        }

    all_topics = list(merged.values())
    all_topics.sort(key=lambda x: (int(x["views"]), int(x["likes"]), int(x["comments"])), reverse=True)

    # Dynamic performance tiering
    max_views = max((int(t["views"]) for t in all_topics), default=0)
    non_zero_views = sorted(int(t["views"]) for t in all_topics if int(t["views"]) > 0)
    median_nz = non_zero_views[len(non_zero_views) // 2] if non_zero_views else 0
    for idx, t in enumerate(all_topics):
        v = int(t["views"])
        if max_views == 0:
            t["tier"] = "SEPI"
        elif idx < 2 or v >= max(150, int(max_views * 0.45)):
            t["tier"] = "RAME"
        elif v >= max(20, int(median_nz * 0.5)) if median_nz > 0 else v > 10:
            t["tier"] = "SEDANG"
        else:
            t["tier"] = "SEPI"

    top_videos = all_topics[:5]
    if len(all_topics) >= 10:
        exclude_keys = {t["key"] for t in top_videos}
    else:
        win_count = min(3, sum(1 for t in top_videos if int(t["views"]) > 0))
        exclude_keys = {t["key"] for t in top_videos[:win_count]}
    remaining_asc = sorted(
        [t for t in all_topics if t["key"] not in exclude_keys] if len(all_topics) > 4 else all_topics,
        key=lambda x: (int(x["views"]), int(x["likes"]), x.get("published_at") or ""),
    )
    bottom_videos = remaining_asc[:5]

    pillars = ["kimia", "medisinal", "biologi", "fisika", "matematika"]
    discipline_breakdown: list[dict[str, Any]] = []
    for p in pillars:
        items = [t for t in all_topics if t["discipline"] == p]
        tot_v = sum(int(i["views"]) for i in items)
        avg_v = round(tot_v / len(items), 1) if items else 0.0
        best = max(items, key=lambda x: int(x["views"])) if items else None
        if not items:
            status_lbl = "BELUM DIGARAP (GAP)"
        elif avg_v >= max(100, median_nz):
            status_lbl = "RAME / UNGGULAN"
        elif avg_v > 10:
            status_lbl = "STABIL"
        else:
            status_lbl = "PERLU BOOST HOOK"
        discipline_breakdown.append({
            "discipline": p,
            "count": len(items),
            "total_views": tot_v,
            "avg_views": avg_v,
            "max_views": int(best["views"]) if best else 0,
            "top_topic": best["title"] if best else "-",
            "status_label": status_lbl,
            "topics": items,
        })

    section_breakdown: list[dict[str, Any]] = []
    for sec_name in ("REGULER", "DAPUR", "KARAT"):
        items = [t for t in all_topics if t["section"] == sec_name]
        tot_v = sum(int(i["views"]) for i in items)
        avg_v = round(tot_v / len(items), 1) if items else 0.0
        section_breakdown.append({
            "section": sec_name,
            "count": len(items),
            "total_views": tot_v,
            "avg_views": avg_v,
        })

    return {
        "total_science_videos": len(all_topics),
        "all_topics": all_topics,
        "top_videos": top_videos,
        "bottom_videos": bottom_videos,
        "discipline_breakdown": discipline_breakdown,
        "section_breakdown": section_breakdown,
    }


def _format_audit_markdown(
    audit: Agent8WeeklyAuditOutput,
    enriched_videos: list[dict[str, Any]],
    channel_stats: Optional[dict[str, Any]] = None,
    countries_summary: Optional[dict[str, Any]] = None,
    milestone_stats: Optional[dict[str, Any]] = None,
    topic_landscape: Optional[dict[str, Any]] = None,
) -> str:
    lines = [
        f"# Laporan Audit Mingguan Sosial Media Kimiabill ({audit.week_ending})",
        "",
        "## Ringkasan Performa Minggu Ini",
        audit.summary,
        "",
    ]
    if channel_stats:
        lines.extend(_format_monetization_section(channel_stats))
        lines.append("")
    lines.append("## Data Video yang Diaudit")
    if enriched_videos:
        for v in enriched_videos:
            views = v.get("views", "N/A")
            likes = v.get("likes", "N/A")
            comments = v.get("comments", "N/A")
            ret = v.get("avg_view_percentage")
            avg_s = v.get("avg_view_duration_sec")
            subs = v.get("subscribers_gained")
            qa = v.get("qa_score", "N/A")
            extra = ""
            if ret is not None or avg_s is not None:
                extra = f" | Retensi: {ret}% ({avg_s}s) | Subs+: {subs}"
            lines.append(
                f"- **{v.get('title', '-')}** ({v.get('discipline', '-')}) | "
                f"Views: {views} | Likes: {likes} | Komen: {comments}{extra} | QA: {qa} | {v.get('youtube_url') or '-'}"
            )
    else:
        lines.append("- Belum ada histori video publik yang tercatat minggu ini (audit berbasis baseline channel).")

    def _sec(title: str, items: list[str]) -> None:
        lines.append("")
        lines.append(f"## {title}")
        for it in items:
            lines.append(f"- {it}")

    _sec("Pola yang Paling Berhasil", audit.top_performing_patterns)
    _sec("Kelemahan & Hambatan Engagement", audit.bottlenecks_and_weaknesses)

    tl = topic_landscape or {}
    lines.append("")
    lines.append("## Peta Topik Video & Analisis Rame vs Sepi")
    lines.append(f"- **Total Video Sains Terpetakan:** {tl.get('total_science_videos', 0)} topik")
    if tl.get("top_videos"):
        lines.append("- **Topik Paling Rame (Top Performers):**")
        for idx, tv in enumerate(tl["top_videos"][:5], 1):
            lines.append(f"  {idx}. **{tv['title']}** ({tv['discipline'].upper()} • {tv['section']}) — {tv['views']:,} views | {tv['likes']} likes")
    if tl.get("bottom_videos"):
        lines.append("- **Topik Paling Sepi (Butuh Evaluasi Hook/Timing):**")
        for idx, bv in enumerate(tl["bottom_videos"][:5], 1):
            lines.append(f"  {idx}. **{bv['title']}** ({bv['discipline'].upper()} • {bv['section']}) — {bv['views']:,} views | {bv['likes']} likes")
    if tl.get("discipline_breakdown"):
        dist_txt = " | ".join(
            f"{d['discipline'].capitalize()}: {d['count']} vid (avg {d['avg_views']} views)"
            for d in tl["discipline_breakdown"]
        )
        lines.append(f"- **Sebaran 5 Disiplin Sains:** {dist_txt}")
    if getattr(audit, "topic_performance_highlights", None):
        for hl in audit.topic_performance_highlights:
            lines.append(f"- **Analisis Agent 8:** {hl}")

    if getattr(audit, "next_week_topic_map", None):
        lines.append("")
        lines.append("## Peta Rencana Topik Seminggu Ke Depan (14 Slot • 7 Hari)")
        lines.append("| Hari | 12:00 WIB (KIMIA DAPUR) | 19:00 WIB (REGULER / KARAT) |")
        lines.append("|---|---|---|")
        days_order = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        by_day: dict[str, dict[str, Any]] = {}
        for slot in audit.next_week_topic_map:
            d_low = slot.day_label.strip().lower()
            matched_day = next((d for d in days_order if d.lower().startswith(d_low[:4])), slot.day_label)
            by_day.setdefault(matched_day, {})[slot.slot_time_wib] = slot
        for d in days_order:
            s12 = by_day.get(d, {}).get("12:00")
            s19 = by_day.get(d, {}).get("19:00")
            txt12 = f"**{s12.planned_topic}** ({s12.discipline}) — _{s12.rationale}_" if s12 else "-"
            txt19 = f"**{s19.planned_topic}** [{s19.section} • {s19.discipline}] — _{s19.rationale}_" if s19 else "-"
            lines.append(f"| {d} | {txt12} | {txt19} |")

    lines.append("")
    lines.append("## Audit Efisiensi Workflow & Penggunaan Token")
    lines.append(audit.workflow_efficiency_summary or "-")
    _sec("Optimasi Token, Kuota & Kecepatan Produksi", audit.token_and_cost_optimizations)

    cs = countries_summary or {}
    ms = milestone_stats or {}
    lines.append("")
    lines.append("## Potensi Penonton Internasional & Database Milestone")
    if cs.get("top_countries"):
        c_txt = ", ".join(f"{c.get('country')}: {c.get('views'):,} views" for c in cs["top_countries"][:6])
        lines.append(f"- **Sebaran Negara Penonton (28 hari):** {c_txt} (**Luar Indonesia: {cs.get('non_id_views_pct', 0.0):.2f}%**)")
    else:
        lines.append("- **Sebaran Negara Penonton:** Menunggu data negara aktif dari Apps Script / volume views cukup.")
    if audit.international_potential:
        lines.append(f"- **Analisis Potensi Global Agent 8:** {audit.international_potential}")
    if ms:
        lines.append(
            f"- **Arsip Database Kumulatif:** {ms.get('total_videos_archived', 0)} video ({ms.get('total_weeks_audited', 0)} minggu audit) | "
            f"Reguler avg: {ms.get('regular_avg_views') if ms.get('regular_avg_views') is not None else '-'} views vs "
            f"KARAT avg: {ms.get('karat_avg_views') if ms.get('karat_avg_views') is not None else '-'} views | "
            f"Disiplin terkuat: {ms.get('top_discipline', '-')}"
        )

    _sec("Instruksi Upgrade ke Agent 1 (Trend & Ideasi)", audit.agent_1_directives)
    _sec("Instruksi Upgrade ke Agent 2 (Editor & Hook)", audit.agent_2_directives)
    _sec("Instruksi Upgrade ke Agent 3 (Skrip & Animasi Mekanistik)", audit.agent_3_directives)
    _sec("Instruksi Upgrade ke Agent 5 (Video QA Auditor)", audit.agent_5_directives)
    _sec("Instruksi Upgrade ke Agent 6 (Pencari Footage Real)", audit.agent_6_directives)
    if audit.karat_weekend_directives:
        _sec("Rekomendasi Topik & Riset Edisi Weekend KARAT Minggu Depan", audit.karat_weekend_directives)
    if getattr(audit, "audience_requests", None):
        _sec(
            "Antrean Request Konten dari Komentar Penonton (Siap Tayang Minggu Depan)",
            [
                f"**{r.requester_name}** ({r.discipline}) → *{r.topic_title}* — Komentar: \"{r.source_comment}\""
                for r in audit.audience_requests
            ],
        )
    _sec("Eksperimen Minggu Depan", audit.next_week_experiments)
    if audit.recommended_weekend_hours_wib:
        _sec(
            "Rekomendasi Jam Rame Edisi Weekend KARAT (Otomatis Diterapkan)",
            [f"{', '.join(audit.recommended_weekend_hours_wib)} WIB"],
        )
    return "\n".join(lines) + "\n"


def _get_tracking_sheet_id() -> str:
    env_id = os.environ.get("KIMIABILL_SHEET_ID", "").strip()
    if env_id:
        return env_id
    brand_path = BASE_DIR / "config" / "brand.yaml"
    if brand_path.exists():
        try:
            brand_cfg = yaml.safe_load(brand_path.read_text(encoding="utf-8")) or {}
            sid = str(brand_cfg.get("publishing", {}).get("tracking_sheet_id", "")).strip()
            if sid:
                return sid
        except Exception:
            pass
    return ""


def _deliver_audit_to_drive_and_email(
    audit: Agent8WeeklyAuditOutput,
    report_md: str,
    report_path: Path,
    pdf_path: Optional[Path] = None,
    channel_stats: Optional[dict[str, Any]] = None,
    recent_videos: Optional[list[dict[str, Any]]] = None,
    csv_path: Optional[Path] = None,
) -> None:
    """Upload weekly audit PDF, Markdown, & single cumulative Master CSV to Google Drive, & email research inbox."""
    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        print("[agent8] GDRIVE_WEBHOOK_URL tidak diset; melewati upload Drive & email.")
        return
    webhook_url = _normalize_webhook_url(raw_url)
    token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
    notify_to = os.environ.get("NOTIFY_EMAIL_TO", "researchfalsyabillah@gmail.com").strip()

    folder_url = ""
    pdf_url = ""
    file_url = ""
    csv_url = ""
    try:
        init_resp = requests.post(
            webhook_url,
            json={
                "action": "init_episode",
                "token": token,
                "root_folder": "Kimiabill_Studio",
                "episode_name": "Weekly_Audits",
            },
            timeout=45,
        ).json()
        if init_resp.get("ok"):
            folder_id = init_resp["folder_id"]
            folder_url = init_resp.get("folder_url", "")
            if pdf_path and pdf_path.exists():
                up_pdf = requests.post(
                    webhook_url,
                    json={
                        "action": "upload_file",
                        "token": token,
                        "folder_id": folder_id,
                        "filename": pdf_path.name,
                        "mime_type": "application/pdf",
                        "content_base64": base64.b64encode(pdf_path.read_bytes()).decode("ascii"),
                    },
                    timeout=90,
                ).json()
                if up_pdf.get("ok"):
                    pdf_url = up_pdf.get("file_url", "")
            if csv_path and csv_path.exists():
                up_csv = requests.post(
                    webhook_url,
                    json={
                        "action": "upload_file",
                        "token": token,
                        "folder_id": folder_id,
                        "filename": csv_path.name,
                        "mime_type": "text/csv",
                        "content_base64": base64.b64encode(csv_path.read_bytes()).decode("ascii"),
                    },
                    timeout=60,
                ).json()
                if up_csv.get("ok"):
                    csv_url = up_csv.get("file_url", "")
            up_resp = requests.post(
                webhook_url,
                json={
                    "action": "upload_file",
                    "token": token,
                    "folder_id": folder_id,
                    "filename": report_path.name,
                    "mime_type": "text/markdown",
                    "content_base64": base64.b64encode(report_path.read_bytes()).decode("ascii"),
                },
                timeout=60,
            ).json()
            if up_resp.get("ok"):
                file_url = up_resp.get("file_url", "")
    except Exception as exc:
        print(f"[agent8] Peringatan upload Drive laporan mingguan: {exc}")

    sheet_id = _get_tracking_sheet_id()
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit" if sheet_id else ""
    if sheet_id:
        try:
            ch = channel_stats or {}
            vids = recent_videos or []
            subs = int(ch.get("subscribers", 0) or 0)
            views = int(ch.get("total_views", 0) or 0)
            qa_scores = [float(v.get("qa_score")) for v in vids if v.get("qa_score") is not None]
            avg_qa: Any = round(sum(qa_scores) / len(qa_scores), 1) if qa_scores else "-"
            karat_line = "; ".join(audit.karat_weekend_directives[:2]) if audit.karat_weekend_directives else "-"
            summary_cell = f"{audit.summary} | KARAT: {karat_line}"[:900]
            audit_row = [
                audit.week_ending,
                subs,
                views,
                f"{subs / 500 * 100:.2f}%",
                f"{views / 3_000_000 * 100:.2f}%",
                f"{subs / 1000 * 100:.2f}%",
                f"{views / 10_000_000 * 100:.2f}%",
                len(vids),
                avg_qa,
                summary_cell,
            ]
            request_rows = [
                [
                    req.get("request_id", "-"),
                    req.get("created_week", "-"),
                    req.get("requester_name", "-"),
                    str(req.get("source_comment", "-"))[:220],
                    req.get("topic_title", "-"),
                    req.get("discipline", "-"),
                    req.get("status", "pending"),
                    req.get("fulfilled_youtube_url") or req.get("fulfilled_episode_id") or "-",
                ]
                for req in load_audience_requests()
            ]
            s_resp = requests.post(
                webhook_url,
                json={
                    "action": "sync_audit_sheet",
                    "token": token,
                    "sheet_id": sheet_id,
                    "audit_row": audit_row,
                    "request_rows": request_rows,
                },
                timeout=45,
            ).json()
            if s_resp.get("ok"):
                print(f"[agent8] Google Sheet tracker diperbarui (Audit Mingguan & Request Penonton): {sheet_url}")
            else:
                print(f"[agent8] Peringatan sync Google Sheet audit: {s_resp}")
        except Exception as exc:
            print(f"[agent8] Peringatan gagal sync Google Sheet audit: {exc}")

    header_links = []
    if pdf_url:
        header_links.append(f"📄 BUKA LAPORAN PDF VISUAL (5 HALAMAN • GRAFIK, PETA TOPIK & ROADMAP MINGGU DEPAN): {pdf_url}")
    if csv_url:
        header_links.append(f"📊 BUKA / DOWNLOAD MASTER DATABASE CSV (1 File Update Mingguan): {csv_url}")
    if sheet_url:
        header_links.append(f"📊 BUKA GOOGLE SHEET TRACKER LIVE (Upload & Audit YPP): {sheet_url}")
    if folder_url:
        header_links.append(f"📁 Folder Arsip Audit Drive: {folder_url}")
    try:
        from src.providers import format_model_usage_summary

        a8_model_note = format_model_usage_summary("[MODEL AI YANG DIPAKAI AUDIT MINGGUAN]")
        if a8_model_note:
            header_links.append(a8_model_note)
    except Exception:
        pass
    email_body = ("\n\n".join(header_links) + "\n\n" if header_links else "") + report_md

    # Email full report via send_custom_email (new Apps Script) or fallback to notify_email
    subject = f"[Kimiabill] Laporan Audit Mingguan Visual PDF ({audit.week_ending})"
    try:
        r1 = requests.post(
            webhook_url,
            json={
                "action": "send_custom_email",
                "token": token,
                "to": notify_to,
                "subject": subject,
                "body": email_body,
            },
            timeout=45,
        ).json()
        if r1.get("ok"):
            print(f"[agent8] Laporan audit mingguan (PDF: {pdf_url}) terkirim ke {notify_to}")
            return
    except Exception:
        pass

    try:
        r2 = requests.post(
            webhook_url,
            json={
                "action": "notify_email",
                "token": token,
                "to": notify_to,
                "episode_name": f"weekly_audit_{audit.week_ending}",
                "title": f"LAPORAN AUDIT MINGGUAN VISUAL PDF ({audit.week_ending})",
                "folder_url": folder_url,
                "video_url": pdf_url or file_url,
                "captions_preview": email_body,
            },
            timeout=45,
        ).json()
        if r2.get("ok"):
            print(f"[agent8] Laporan audit mingguan terkirim ke {notify_to} (via notify_email)")
        else:
            print(f"[agent8] Gagal mengirim email audit mingguan: {r2}")
    except Exception as exc:
        print(f"[agent8] Error mengirim email audit mingguan: {exc}")


def run_agent_8_weekly_audit() -> Agent8WeeklyAuditOutput:
    """Execute Sunday Agent 8 Weekly Social Media & Cross-Agent Growth Audit."""
    from src.pipeline import load_dotenv
    from src.providers import reset_model_usage_log

    load_dotenv()
    reset_model_usage_log()
    agents_cfg = yaml.safe_load((BASE_DIR / "config" / "agents.yaml").read_text(encoding="utf-8"))
    a8_cfg = agents_cfg.get("agents", {}).get(
        "agent_8_growth_auditor",
        {"provider": "auto", "recommended_prod_model": "gemini-3.8-flash"},
    )

    history: list[dict[str, Any]] = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            history = []

    recent = history[-14:]
    webhook_stats = _fetch_webhook_youtube_stats()
    api_stats_by_id = {v.get("video_id"): v for v in webhook_stats.get("videos", []) if v.get("video_id")}

    enriched: list[dict[str, Any]] = []
    for item in recent:
        row = dict(item)
        vid = _extract_video_id(row.get("youtube_url") or "")
        if vid and vid in api_stats_by_id:
            row.update(api_stats_by_id[vid])
        elif row.get("youtube_url"):
            row.update(_fetch_public_video_stats(row["youtube_url"]))
        enriched.append(row)

    # Also include any channel videos from YouTube API not in local history
    known_ids = {_extract_video_id(x.get("youtube_url") or "") for x in enriched}
    for v in webhook_stats.get("videos", []):
        if v.get("video_id") and v["video_id"] not in known_ids:
            enriched.append(v)

    prev_directives = load_latest_agent_directives()
    today_wib = datetime.now(WIB).strftime("%Y-%m-%d")
    existing_requests = load_audience_requests()

    # Merge channel-wide recent comments + per-video comment items/top_comments
    viewer_comments: list[dict[str, Any]] = []
    seen_c_keys: set[str] = set()
    for c in webhook_stats.get("recent_comments", []):
        cid = c.get("comment_id") or f"{c.get('author')}:{c.get('text', '')[:60]}"
        if cid not in seen_c_keys and c.get("text"):
            seen_c_keys.add(cid)
            viewer_comments.append(c)
    for v in enriched:
        for ci in v.get("comment_items", []) or []:
            cid = ci.get("comment_id") or f"{ci.get('author')}:{ci.get('text', '')[:60]}"
            if cid not in seen_c_keys and ci.get("text"):
                seen_c_keys.add(cid)
                viewer_comments.append(ci)
        for tc in v.get("top_comments", []) or []:
            if isinstance(tc, str) and tc.strip():
                key = f"legacy:{tc[:60]}"
                if key not in seen_c_keys:
                    seen_c_keys.add(key)
                    viewer_comments.append(
                        {"video_id": v.get("video_id"), "author": "Penonton", "text": tc.strip()}
                    )

    from src.memory import StudioMemory

    memory = StudioMemory(BASE_DIR)
    # Archive full history + newly enriched YouTube API videos permanently into SQLite
    all_seed_rows = list(history) + list(enriched)
    memory.upsert_episode_analytics(all_seed_rows, week_ending=today_wib)
    milestone_stats = memory.get_milestone_summary()
    archived_rows = memory.get_all_episode_analytics()
    topic_landscape = _build_topic_landscape(archived_rows, enriched)
    countries_summary = _build_international_summary(
        webhook_stats.get("countries") or [],
        viewer_comments,
    )

    topic_landscape_brief = {
        "total_science_videos": topic_landscape["total_science_videos"],
        "top_videos": [
            {"title": t["title"], "discipline": t["discipline"], "section": t["section"], "views": t["views"], "likes": t["likes"]}
            for t in topic_landscape["top_videos"]
        ],
        "bottom_videos": [
            {"title": t["title"], "discipline": t["discipline"], "section": t["section"], "views": t["views"], "likes": t["likes"]}
            for t in topic_landscape["bottom_videos"]
        ],
        "discipline_breakdown": [
            {k: d[k] for k in ("discipline", "count", "total_views", "avg_views", "max_views", "status_label")}
            for d in topic_landscape["discipline_breakdown"]
        ],
        "section_breakdown": topic_landscape["section_breakdown"],
        "all_produced_titles": [t["title"] for t in topic_landscape["all_topics"][:40]],
    }

    audit = call_structured_agent(
        provider=a8_cfg.get("provider", "auto"),
        model=a8_cfg.get("model", a8_cfg.get("recommended_prod_model", "gemini-3.8-flash")),
        system_prompt=(
            "You are Agent 8 (Chief Weekly System, Engagement & Workflow Efficiency Auditor) for Kimiabill, an Indonesian "
            "pure-science YouTube Shorts studio (Kimia, Fisika, Matematika, Medisinal, Biologi). "
            "Perform FIVE deep audits in clear Indonesian from this week's data:\n"
            "1. SOCIAL MEDIA ENGAGEMENT & MONETIZATION AUDIT: Analyze views, likes, comments, retention %, avg view duration, "
            "QA scores, hooks, topics, and durations. Also state clearly in `summary` how far the channel is from YouTube "
            "Partner Program Shorts monetization (500 subs / 3M views and 1,000 subs / 10M views) and what will accelerate it.\n"
            "2. WORKFLOW & TOKEN EFFICIENCY AUDIT: Analyze production_seconds, revisions_used, and topic_attempts_used "
            "to spot wasted Gemini/ElevenLabs tokens, slow footage queries, or unnecessary revisions, and recommend "
            "concrete efficiency improvements.\n"
            "3. AUDIENCE COMMENT & CONTENT REQUEST EXTRACTION: Read `viewer_comments` carefully! Extract up to 5 valid "
            "pure-science content requests, curious 'why/how' science questions, or requested discussion topics from viewers "
            "into `audience_requests` (preserving `requester_name` e.g. @handle, `source_comment`, `source_video_id`, "
            "`source_comment_id`, a crisp Indonesian `topic_title`, and `discipline`). Ignore spam, greetings, or non-science asks, "
            "and never duplicate anything already in `existing_requests_queue`.\n"
            "4. INTERNATIONAL VIEWER POTENTIAL & MILESTONE READINESS: Evaluate `countries_summary` (non-ID view share & top countries), "
            "foreign-language comments, and `cumulative_milestone_stats` (Reguler vs KARAT & total database) in `international_potential`, "
            "noting which science topics/visuals show global appeal for a future English channel.\n"
            "5. TOPIC PERFORMANCE DIAGNOSIS: Inspect `topic_landscape` (all produced science topics, `top_videos`, "
            "`bottom_videos`, and `discipline_breakdown` gaps across 5 pillars). Populate `topic_performance_highlights` (3-5 crisp "
            "Indonesian findings naming exact highest-views vs lowest-views topics and WHY they succeeded/stalled). "
            "Leave `next_week_topic_map` as `[]` — Agent 1 (Trend Scout & Planner) will draft the 14-slot weekly titles right after your audit!\n"
            "Produce sharp, concrete next-week upgrade directives for Agents 1, 2, 3, 5, and 6 so the entire studio "
            "becomes higher quality, faster, and more token-efficient every single week. "
            "Also populate `recommended_weekend_hours_wib` with 2 optimal HH:MM WIB upload times (1 midday slot around 11:30-13:30 "
            "and 1 evening slot around 18:30-20:30) and populate `karat_weekend_directives` with 2-3 specific research-paper "
            "themes/angles derived from this week's highest-views/highest-retention topics & viewer comments for next Saturday/Sunday's 'KARAT — Kabar Riset Terkini'."
        ),
        user_payload={
            "week_ending": today_wib,
            "studio_target_duration": "~1 menit (50-72 detik, 6-7 adegan per Shorts)",
            "channel_stats": webhook_stats.get("channel", {}),
            "ypp_targets": {"tier1_subs": 500, "tier1_shorts_views_90d": 3000000, "full_subs": 1000, "full_shorts_views_90d": 10000000},
            "recent_videos": enriched,
            "topic_landscape": topic_landscape_brief,
            "countries_summary": countries_summary,
            "cumulative_milestone_stats": milestone_stats,
            "viewer_comments": viewer_comments[:60],
            "existing_requests_queue": existing_requests[-25:],
            "previous_week_directives": prev_directives,
        },
        response_model=Agent8WeeklyAuditOutput,
        handoff_path=AUDITS_DIR / f"agent8_audit_{today_wib}.json",
    )

    queued_new = queue_audience_requests(audit.audience_requests, today_wib)
    if queued_new:
        print(f"[agent8] Berhasil mencatat {len(queued_new)} request penonton baru ke antrean minggu depan!")

    # Delegate 14-slot next-week creative topic roadmapping to Agent 1 (Trend Scout & Ideation)
    a1_cfg = agents_cfg.get("agents", {}).get(
        "agent_1_ideation",
        {"provider": "auto", "recommended_prod_model": "gemini-3.8-flash"},
    )
    try:
        pending_queue = [r for r in load_audience_requests() if r.get("status") == "pending"][:7]
        kitchen_cands = memory.get_kitchen_chemistry_ideas(count=12)
        topic_map_res = call_structured_agent(
            provider=a1_cfg.get("provider", "auto"),
            model=a1_cfg.get("model", a1_cfg.get("recommended_prod_model", "gemini-3.8-flash")),
            system_prompt=(
                "You are Agent 1 (Viral Pure-Science Trend Scout & Weekly Topic Strategist) for Kimiabill. "
                "Using Agent 8's strategic audit findings (`agent8_strategy`), design `next_week_topic_map` with EXACTLY 14 slots "
                "covering Senin through Minggu (two slots per day: `12:00` and `19:00` WIB):\n"
                "- EVERY day `12:00` WIB MUST be `section: 'DAPUR'` (Kimia Dapur / Sains Sehari-hari di rumah/makanan/minuman, "
                "prioritizing `kitchen_chemistry_candidates` or fresh filmable kitchen-science concepts).\n"
                "- Senin-Jumat `19:00` WIB MUST be `section: 'REGULER'`: prioritize `pending_audience_requests`, fill 0-video "
                "discipline gaps (`fisika`, `matematika`, `biologi`), and double down on winning topic patterns from Agent 8.\n"
                "- Sabtu & Minggu `19:00` WIB MUST be `section: 'KARAT'`: breaking research-paper angles aligned with Agent 8's `karat_weekend_directives`.\n"
                "Apply Veritasium misconception-first hooks, concrete filmable visuals, and NEVER repeat any title in `avoid_titles`."
            ),
            user_payload={
                "week_ending": today_wib,
                "agent8_strategy": {
                    "topic_performance_highlights": audit.topic_performance_highlights,
                    "agent_1_directives": audit.agent_1_directives,
                    "karat_weekend_directives": audit.karat_weekend_directives,
                    "top_performing_patterns": audit.top_performing_patterns,
                },
                "discipline_gaps": topic_landscape_brief["discipline_breakdown"],
                "pending_audience_requests": [
                    {"requester": r.get("requester_name"), "topic": r.get("topic_title"), "discipline": r.get("discipline")}
                    for r in pending_queue
                ],
                "kitchen_chemistry_candidates": [
                    {"title": k.get("title"), "discipline": k.get("discipline"), "phenomenon": k.get("phenomenon")}
                    for k in kitchen_cands
                ],
                "avoid_titles": topic_landscape_brief["all_produced_titles"],
            },
            response_model=NextWeekTopicMapOutput,
            handoff_path=AUDITS_DIR / f"agent1_topic_map_{today_wib}.json",
        )
        if topic_map_res.next_week_topic_map:
            audit.next_week_topic_map = list(topic_map_res.next_week_topic_map)
            print(f"[agent8->agent1] Agent 1 menyusun {len(audit.next_week_topic_map)} slot peta topik minggu depan!")
    except Exception as exc:
        print(f"[agent8->agent1] Peringatan delegasi peta topik ke Agent 1 gagal ({exc}); memakai peta cadangan visual PDF.")

    DIRECTIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIRECTIVES_PATH.write_text(audit.model_dump_json(indent=2), encoding="utf-8")

    ch_stats = webhook_stats.get("channel", {})
    qa_scores = [float(v["qa_score"]) for v in enriched if v.get("qa_score") is not None]
    ret_scores = [float(v["avg_view_percentage"]) for v in enriched if v.get("avg_view_percentage") is not None]
    memory.upsert_weekly_snapshot({
        "week_ending": today_wib,
        "subscribers": int(ch_stats.get("subscribers") or 0),
        "total_views": int(ch_stats.get("total_views") or 0),
        "video_count_audited": len(enriched),
        "avg_qa": round(sum(qa_scores) / len(qa_scores), 2) if qa_scores else None,
        "avg_retention_pct": round(sum(ret_scores) / len(ret_scores), 2) if ret_scores else None,
        "karat_avg_views": milestone_stats.get("karat_avg_views"),
        "regular_avg_views": milestone_stats.get("regular_avg_views"),
        "non_id_views_pct": countries_summary.get("non_id_views_pct", 0.0),
        "top_countries": countries_summary.get("top_countries", []),
        "international_potential": audit.international_potential,
        "karat_directives": audit.karat_weekend_directives,
        "summary": audit.summary,
    })
    milestone_stats = memory.get_milestone_summary()
    csv_rows = memory.export_master_csv(MASTER_CSV_PATH)
    print(f"[agent8] Master Database CSV tunggal diperbarui ({csv_rows} baris): {MASTER_CSV_PATH}")

    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    report_md = _format_audit_markdown(
        audit,
        enriched,
        channel_stats=ch_stats,
        countries_summary=countries_summary,
        milestone_stats=milestone_stats,
        topic_landscape=topic_landscape,
    )
    try:
        from src.tools.foundation_auditor import run_weekly_foundation_audit

        foundation_res = run_weekly_foundation_audit(BASE_DIR, today_wib)
        (AUDITS_DIR / f"foundation_audit_{today_wib}.json").write_text(
            json.dumps(foundation_res, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if foundation_res.get("markdown_block"):
            report_md = report_md.rstrip() + "\n\n" + foundation_res["markdown_block"].strip() + "\n"
        print(f"[agent8->foundation] Audit pondasi mingguan selesai: status={foundation_res.get('overall_status')} ({len(foundation_res.get('findings') or [])} temuan)")
    except Exception as exc:
        print(f"[agent8->foundation] Peringatan gagal audit pondasi mingguan: {exc}")

    report_path = AUDITS_DIR / f"weekly_audit_{today_wib}.md"
    report_path.write_text(report_md, encoding="utf-8")

    pdf_path = AUDITS_DIR / f"WEEKLY_AUDIT_{today_wib}.pdf"
    try:
        from src.tools.audit_pdf_maker import generate_weekly_audit_pdf

        generate_weekly_audit_pdf(
            audit,
            enriched,
            ch_stats,
            pdf_path,
            countries_summary=countries_summary,
            milestone_stats=milestone_stats,
            topic_landscape=topic_landscape,
        )
        print(f"[agent8] PDF visual berhasil dibuat: {pdf_path}")
    except Exception as exc:
        print(f"[agent8] Peringatan gagal membuat PDF visual: {exc}")
        pdf_path = None

    _deliver_audit_to_drive_and_email(
        audit,
        report_md,
        report_path,
        pdf_path=pdf_path,
        channel_stats=ch_stats,
        recent_videos=enriched,
        csv_path=MASTER_CSV_PATH,
    )
    print(f"[agent8] Audit mingguan selesai ({today_wib}). Instruksi terbaru disimpan di {DIRECTIVES_PATH}")
    return audit
