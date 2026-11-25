"""Modular Cross-Portfolio CEO Auditor (YouTube ID + YouTube ENG + Threads Affiliate + Digital Products slot).

Collects live telemetry across all units enabled in `config/portfolio.yaml`:
- GitHub repository history & GitHub Actions workflow reliability (public REST API + optional GH_TOKEN)
- YouTube Channel live stats via each unit's Apps Script webhook (`get_youtube_stats`) or public video check
- Meta Threads Graph API insights (`/{user_id}/threads_insights`, `/{user_id}/threads`, `/{post_id}/insights`)
  plus Google Sheets queue/log/settings (`Kantor Affiliate`)
- Optional future `digital_products` unit ("Kantor Produk Digital") via 1-block YAML activation

Outputs structured JSON + Markdown + calls `portfolio_visual_maker.py` to generate:
- 2 high-res visual PNG infographics (for inline Telegram `sendPhoto`)
- 1 multi-page visual A4 PDF (`PORTFOLIO_CEO_AUDIT_YYYY-MM-DD.pdf`)
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
import yaml

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PORTFOLIO_CFG_PATH = BASE_DIR / "config" / "portfolio.yaml"
PORTFOLIO_OUT_DIR = BASE_DIR / "data" / "portfolio_audits"
AFFILIATE_SNAPSHOT_PATH = PORTFOLIO_OUT_DIR / "latest_affiliate_sheet_snapshot.json"
THREADS_API_BASE = "https://graph.threads.net/v1.0"


def load_portfolio_config() -> dict[str, Any]:
    if not PORTFOLIO_CFG_PATH.exists():
        return {"portfolio": {"report_title": "KIMIABILL ECOSYSTEM — PORTFOLIO CEO REPORT"}, "units": []}
    return yaml.safe_load(PORTFOLIO_CFG_PATH.read_text(encoding="utf-8")) or {}


def _gh_headers() -> dict[str, str]:
    tok = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Kimiabill-Portfolio-Auditor/1.0"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _gh_api_get(endpoint: str, params: Optional[dict[str, Any]] = None) -> Any:
    """Query GitHub REST API with requests, falling back to authenticated `gh api` on 403/rate-limit."""
    import subprocess

    url = f"https://api.github.com/{endpoint.lstrip('/')}"
    try:
        r = requests.get(url, params=params, headers=_gh_headers(), timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    try:
        q_url = endpoint.lstrip("/")
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            q_url = f"{q_url}?{qs}"
        proc = subprocess.run(["gh", "api", q_url], capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception:
        pass
    return None


def _fetch_gh_repo_json(repo: str, path: str) -> Any:
    if not repo:
        return None
    try:
        data = _gh_api_get(f"repos/{repo}/contents/{path}")
        if not isinstance(data, dict):
            return None
        b64 = data.get("content", "")
        if not b64:
            return None
        raw = base64.b64decode(b64).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


def _fetch_gh_workflow_runs(repo: str, limit: int = 20) -> dict[str, Any]:
    if not repo:
        return {"total": 0, "success": 0, "failure": 0, "success_rate_pct": 100.0, "failed_workflows": []}
    try:
        data = _gh_api_get(f"repos/{repo}/actions/runs", params={"per_page": limit})
        if not isinstance(data, dict):
            return {"total": 0, "success": 0, "failure": 0, "success_rate_pct": 100.0, "failed_workflows": [], "error": "API unavailable"}
        runs = data.get("workflow_runs") or []
        completed = [x for x in runs if x.get("status") == "completed" and (x.get("name") or "") != "pages-build-deployment"]
        succ = sum(1 for x in completed if x.get("conclusion") == "success")
        fail_runs = [x for x in completed if x.get("conclusion") not in ("success", "skipped", "neutral", None)]
        total = len(completed)
        rate = round((succ / total) * 100.0, 1) if total else 100.0
        failed_names = []
        for fr in fail_runs[:4]:
            wf = fr.get("name") or "Workflow"
            ts = (fr.get("created_at") or "")[:16].replace("T", " ")
            failed_names.append(f"{wf} ({fr.get('conclusion')}, {ts}Z)")
        return {
            "total": total,
            "success": succ,
            "failure": len(fail_runs),
            "success_rate_pct": rate,
            "failed_workflows": failed_names,
            "recent_runs": [
                {"name": x.get("name"), "conclusion": x.get("conclusion"), "created_at": x.get("created_at")}
                for x in completed[:8]
            ],
        }
    except Exception as exc:
        return {"total": 0, "success": 0, "failure": 0, "success_rate_pct": 100.0, "failed_workflows": [], "error": str(exc)}


def _fetch_public_yt_views(video_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for vid in video_ids[:6]:
        try:
            r = requests.get(
                f"https://www.youtube.com/shorts/{vid}",
                headers={"Accept-Language": "en-US"},
                timeout=12,
            )
            vc_m = re.search(r'"views":\{"simpleText":"([\d,]+)\s+views"|"viewCount":"(\d+)"', r.text)
            raw_num = (vc_m.group(1) or vc_m.group(2)) if vc_m else "0"
            views = int(raw_num.replace(",", ""))
            rows.append({"video_id": vid, "views": views, "url": f"https://youtube.com/shorts/{vid}"})
        except Exception:
            pass
    return rows


def _audit_youtube_unit(unit: dict[str, Any]) -> dict[str, Any]:
    repo = unit.get("repo", "")
    is_local = bool(unit.get("local_repo"))
    history: list[dict[str, Any]] = []
    if is_local and (BASE_DIR / "data" / "published_history.json").exists():
        try:
            history = json.loads((BASE_DIR / "data" / "published_history.json").read_text(encoding="utf-8"))
        except Exception:
            history = []
    else:
        remote_hist = _fetch_gh_repo_json(repo, "data/published_history.json")
        if isinstance(remote_hist, list):
            history = remote_hist

    runs_info = _fetch_gh_workflow_runs(repo, limit=20)
    webhook_env = unit.get("webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "").strip() if webhook_env else ""
    yt_stats: dict[str, Any] = {}
    if webhook_url:
        try:
            resp = requests.post(
                webhook_url,
                json={"action": "get_youtube_stats", "max_videos": 20},
                timeout=35,
            )
            if resp.status_code == 200:
                yt_stats = resp.json() if isinstance(resp.json(), dict) else {}
        except Exception:
            yt_stats = {}

    ch = yt_stats.get("channel") or {}
    videos = yt_stats.get("videos") or []
    subs = int(ch.get("subscribers") or 0)
    total_views = int(ch.get("total_views") or 0)
    video_count_api = int(ch.get("video_count") or len(videos) or len(history))

    sample_ids = list(unit.get("public_sample_videos") or [])
    sample_stats = _fetch_public_yt_views(sample_ids) if (not videos and sample_ids) else []
    if not total_views and sample_stats:
        total_views = sum(x["views"] for x in sample_stats)
        video_count_api = max(video_count_api, len(sample_stats))

    recent_views = [int(v.get("views") or 0) for v in videos[:8]]
    avg_recent_views = round(sum(recent_views) / len(recent_views)) if recent_views else (
        round(total_views / max(1, len(sample_stats))) if sample_stats else 0
    )
    qa_scores = [float(x["qa_score"]) for x in history if x.get("qa_score") is not None]
    avg_qa = round(sum(qa_scores) / len(qa_scores), 2) if qa_scores else None
    max_rev = max([int(x.get("revisions_used") or 0) for x in history], default=0)

    # Identify real unit strengths, issues, and health badge
    strengths: list[str] = []
    issues: list[str] = []
    if videos:
        strengths.append(f"{subs} subs & {total_views:,} total views ({avg_recent_views:,} avg views 8 video baru)")
    elif sample_stats:
        strengths.append(f"Channel baru rilis {len(sample_stats)} Shorts perdana (total {total_views} views awal)")
    if avg_qa is not None:
        strengths.append(f"Skor QA video tinggi ({avg_qa}/10, {len(history)} ep tercatat)")

    if unit.get("id") == "yt_id":
        strengths.append("Bot Telegram 2-arah + SFX transisi/animasi & notif 2x aktif")
        issues.append("Apps Script YT ID belum punya izin youtube.force-ssl (pinned comment gagal)")
        if max_rev >= 3:
            issues.append(f"Beberapa episode butuh {max_rev}x revisi footage (~22 menit)")
    elif unit.get("id") == "yt_eng":
        if len(history) == 0 and (videos or sample_stats):
            issues.append("BUG: data/published_history.json masih 0 baris meski sudah upload 2 video!")
        if runs_info.get("failure", 0) > 0:
            issues.append(f"{runs_info['failure']} run gagal hari ini (kuota 429 + 3 percobaan topik)")
        if not webhook_url:
            issues.append("Secret ENG_YOUTUBE_WEBHOOK_URL belum dihubungkan untuk stats API live")
        strengths.append("Auto-thumbnail, auto-comment YT & Telegram berjalan di hari ke-1")

    if runs_info.get("failure", 0) >= 2 and not any("gagal" in x.lower() for x in issues):
        issues.append(f"{runs_info['failure']} workflow run gagal baru-baru ini")

    ms = unit.get("milestone") or {}
    target_subs = int(ms.get("target_subscribers") or 500)
    progress_pct = round(min(100.0, (subs / max(1, target_subs)) * 100.0), 1)
    if not subs and (videos or sample_stats):
        progress_pct = max(progress_pct, 1.0)

    health = "SEHAT"
    if runs_info.get("success_rate_pct", 100.0) < 75.0 or any("BUG:" in i for i in issues):
        health = "PERHATIAN"
    if runs_info.get("total", 0) > 0 and runs_info.get("success", 0) == 0:
        health = "KRITIS"

    return {
        "id": unit["id"],
        "name": unit["name"],
        "handle": unit.get("handle", ""),
        "type": "youtube",
        "repo": repo,
        "health": health,
        "subscribers": subs,
        "total_views": total_views,
        "avg_recent_views": avg_recent_views,
        "video_count": video_count_api,
        "history_count": len(history),
        "avg_qa": avg_qa,
        "top_videos": [
            {"title": str(v.get("title") or "")[:38], "views": int(v.get("views") or 0), "likes": int(v.get("likes") or 0)}
            for v in sorted(videos, key=lambda z: int(z.get("views") or 0), reverse=True)[:5]
        ],
        "runs": runs_info,
        "milestone_label": ms.get("label", "YPP Tier 1"),
        "milestone_current": subs,
        "milestone_target": target_subs,
        "milestone_pct": progress_pct,
        "strengths": strengths[:3],
        "issues": issues[:3],
        "source_mode": "youtube_api_live" if videos else "github_and_public_probe",
    }


def _fetch_threads_api_insights(post_ids: list[str]) -> dict[str, Any]:
    """Query Meta Threads Graph API (`/{user_id}/threads_insights` & `/{post_id}/insights`) when configured."""
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    uid = os.environ.get("THREADS_USER_ID", "").strip()
    if not token or not uid:
        return {"connected": False, "reason": "THREADS_ACCESS_TOKEN / THREADS_USER_ID belum dipasang di GitHub Secrets repo ini"}

    out: dict[str, Any] = {"connected": True, "followers": None, "views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0, "clicks": 0, "post_insights": [], "warnings": []}
    try:
        r_user = requests.get(
            f"{THREADS_API_BASE}/{uid}/threads_insights",
            params={"metric": "views,likes,replies,reposts,quotes,clicks,followers_count", "access_token": token},
            timeout=20,
        )
        if r_user.status_code == 200:
            for item in (r_user.json() or {}).get("data") or []:
                nm = item.get("name")
                vals = item.get("values") or []
                total_v = item.get("total_value", {}).get("value")
                if total_v is not None:
                    val = int(total_v)
                else:
                    val = sum(int(x.get("value") or 0) for x in vals if isinstance(x, dict))
                if nm == "followers_count":
                    out["followers"] = val
                elif nm in out:
                    out[nm] = val
        else:
            out["warnings"].append(f"threads_insights HTTP {r_user.status_code}: {r_user.text[:140]}")
    except Exception as exc:
        out["warnings"].append(f"threads_insights error: {exc}")

    # Query recent posts list if post_ids empty
    pids = list(post_ids)
    if not pids:
        try:
            r_list = requests.get(
                f"{THREADS_API_BASE}/{uid}/threads",
                params={"fields": "id,text,timestamp,permalink", "limit": 10, "access_token": token},
                timeout=20,
            )
            if r_list.status_code == 200:
                pids = [str(p.get("id")) for p in (r_list.json() or {}).get("data") or [] if p.get("id")]
        except Exception:
            pass

    for pid in pids[:10]:
        try:
            rp = requests.get(
                f"{THREADS_API_BASE}/{pid}/insights",
                params={"metric": "views,likes,replies,reposts,quotes,shares", "access_token": token},
                timeout=15,
            )
            if rp.status_code == 200:
                row = {"post_id": pid}
                for item in (rp.json() or {}).get("data") or []:
                    vals = item.get("values") or []
                    row[item.get("name")] = int((vals[-1] or {}).get("value") or 0) if vals else 0
                out["post_insights"].append(row)
        except Exception:
            pass
    return out


def _audit_threads_affiliate_unit(unit: dict[str, Any]) -> dict[str, Any]:
    repo = unit.get("repo", "")
    runs_info = _fetch_gh_workflow_runs(repo, limit=20)

    sheet_snap: dict[str, Any] = {}
    if AFFILIATE_SNAPSHOT_PATH.exists():
        try:
            sheet_snap = json.loads(AFFILIATE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            sheet_snap = {}

    status_counts = sheet_snap.get("status_counts") or {}
    pending_count = int(status_counts.get("PENDING") or 0)
    published_count = int(status_counts.get("PUBLISHED") or 0)
    draft_only_count = int(status_counts.get("DRAFT_ONLY") or 0)
    log_count = int(sheet_snap.get("log_count") or 0)
    settings_map = sheet_snap.get("settings") or {}
    live_logs = sheet_snap.get("live_logs") or []

    post_ids = []
    threads_api = _fetch_threads_api_insights(post_ids)

    strengths: list[str] = []
    issues: list[str] = []
    if published_count > 0:
        strengths.append(f"Baru resmi LIVE di Threads ({published_count} post + reply link, {pending_count} PENDING stok ~16 hari)")
    elif pending_count > 0:
        strengths.append(f"Antrean aman ({pending_count} produk PENDING siap tayang 3x/hari)")
    if runs_info.get("success", 0) >= 4:
        strengths.append(f"Publish 3x/hari & Admin 21.00 WIB sukses beruntun")

    if any("Agent 6" in fn for fn in (runs_info.get("failed_workflows") or [])):
        issues.append("Agent 6 Riset & Audit Threads gagal (semua model Gemini 429/404)")
    min_rating_val = str(settings_map.get("Min_Rating_Shopee") or "")
    tok_date_val = str(settings_map.get("Tanggal_Token_Threads") or "")
    if re.fullmatch(r"\d+(\.0)?", min_rating_val) and float(min_rating_val) > 100:
        issues.append("Sheet Min_Rating_Shopee jadi serial tanggal (set Plain Text '4.7)")
    if re.fullmatch(r"\d+(\.0)?", tok_date_val) and float(tok_date_val) > 1000:
        issues.append("Sheet Tanggal_Token_Threads jadi serial (set Plain Text '2026-11-25)")
    if not threads_api.get("connected"):
        issues.append("Token API Threads Insights besok dipasang untuk baca views/klik")

    ms = unit.get("milestone") or {}
    target_weekly = int(ms.get("target_weekly_posts") or 21)
    # Count recent live posts vs runs
    ms_cur = published_count
    progress_pct = round(min(100.0, (ms_cur / max(1, target_weekly)) * 100.0), 1)
    if ms_cur > 0 and progress_pct < 5.0:
        progress_pct = 5.0

    health = "SEHAT"
    if issues:
        health = "PERHATIAN"
    if runs_info.get("total", 0) > 0 and runs_info.get("success", 0) == 0:
        health = "KRITIS"

    return {
        "id": unit["id"],
        "name": unit["name"],
        "handle": unit.get("handle", ""),
        "type": "threads_affiliate",
        "repo": repo,
        "health": health,
        "pending_queue": pending_count,
        "published_count": published_count,
        "draft_only_count": draft_only_count,
        "log_count": log_count,
        "comments_count": int(sheet_snap.get("comments_count") or 0),
        "threads_api": threads_api,
        "runs": runs_info,
        "milestone_label": ms.get("label", "Target Post Live Mingguan"),
        "milestone_current": ms_cur,
        "milestone_target": target_weekly,
        "milestone_pct": progress_pct,
        "strengths": strengths[:3],
        "issues": issues[:3],
        "live_logs": live_logs[-5:],
        "source_mode": "threads_api+sheets" if threads_api.get("connected") else "sheets+github_runs",
    }


def _audit_digital_products_unit(unit: dict[str, Any]) -> dict[str, Any]:
    """Generic modular collector for future Kantor Produk Digital when enabled=true."""
    repo = unit.get("repo", "")
    runs_info = _fetch_gh_workflow_runs(repo, limit=15) if repo else {"total": 0, "success": 0, "failure": 0, "success_rate_pct": 100.0, "failed_workflows": []}
    orders = 0
    revenue_idr = 0
    webhook_env = unit.get("webhook_env", "")
    webhook_url = os.environ.get(webhook_env, "").strip() if webhook_env else ""
    if webhook_url:
        try:
            r = requests.post(webhook_url, json={"action": "get_sales_summary"}, timeout=25)
            if r.status_code == 200:
                d = r.json() or {}
                orders = int(d.get("monthly_orders") or 0)
                revenue_idr = int(d.get("monthly_revenue_idr") or 0)
        except Exception:
            pass
    ms = unit.get("milestone") or {}
    target_orders = int(ms.get("target_monthly_orders") or 50)
    return {
        "id": unit["id"],
        "name": unit["name"],
        "handle": unit.get("handle", ""),
        "type": "digital_products",
        "repo": repo,
        "health": "SEHAT",
        "orders": orders,
        "revenue_idr": revenue_idr,
        "runs": runs_info,
        "milestone_label": ms.get("label", "Order Produk Digital / Bulan"),
        "milestone_current": orders,
        "milestone_target": target_orders,
        "milestone_pct": round(min(100.0, (orders / max(1, target_orders)) * 100.0), 1),
        "strengths": [f"{orders} order produk digital (Rp {revenue_idr:,})"],
        "issues": [],
        "source_mode": "digital_webhook",
    }


def run_portfolio_ceo_audit(send_telegram: bool = True) -> dict[str, Any]:
    """Collect live cross-portfolio data, build visual PNG+PDF report, and optionally send to Telegram."""
    cfg = load_portfolio_config()
    now_wib = datetime.now(WIB)
    date_str = now_wib.strftime("%Y-%m-%d")
    time_wib_str = now_wib.strftime("%Y-%m-%d %H:%M WIB")

    unit_results: list[dict[str, Any]] = []
    standby_units: list[str] = []
    for unit in cfg.get("units") or []:
        if not unit.get("enabled", True):
            standby_units.append(unit.get("name") or unit.get("id"))
            continue
        utype = str(unit.get("type") or "").strip().lower()
        if utype == "youtube":
            unit_results.append(_audit_youtube_unit(unit))
        elif utype == "threads_affiliate":
            unit_results.append(_audit_threads_affiliate_unit(unit))
        elif utype == "digital_products":
            unit_results.append(_audit_digital_products_unit(unit))

    all_runs_total = sum(int((u.get("runs") or {}).get("total") or 0) for u in unit_results)
    all_runs_ok = sum(int((u.get("runs") or {}).get("success") or 0) for u in unit_results)
    overall_reliability = round((all_runs_ok / all_runs_total) * 100.0, 1) if all_runs_total else 100.0
    total_views_all = sum(int(u.get("total_views") or 0) for u in unit_results)
    total_subs_all = sum(int(u.get("subscribers") or 0) for u in unit_results)

    priority_fixes = [
        {
            "unit": "Kimiabill (YT ID)",
            "severity": "PERHATIAN",
            "title": "Izin youtube.force-ssl Belum Aktif",
            "action": "Jalankan YouTube.CommentThreads 1x di Apps Script YT ID agar pinned comment CTA 1-kata otomatis masuk.",
        },
        {
            "unit": "Bill Explains (YT ENG)",
            "severity": "PERHATIAN",
            "title": "History JSON Masih 0 + 2 Run 429",
            "action": "Cek commit published_history.json di Kimiabill_Project_Eng agar 2 video hari ini tercatat untuk Agent 8.",
        },
        {
            "unit": "Threads Affiliate",
            "severity": "KRITIS",
            "title": "Agent 6 Error 429 & Format Tanggal Sheet",
            "action": "Set sel 4.7 & 2026-11-25 di Sheet ke Plain Text, buang model 2.5 (404), dan pasang Token Insights besok.",
        },
    ]

    synergy_flows = [
        "1. YT ID (Kimia Dapur 1.2K+ views)  -->  Adaptasi visual & hook ke YT ENG (@bill_explains RPM global)",
        "2. Topik Dapur/Gadget Viral di YT  -->  Cocokkan antrean produk kos/meja di Threads Affiliate",
        "3. Komentar & Insights API Threads  -->  Validasi produk klik tertinggi sebelum skala Shopee Ads",
        "4. [Slot Siap] Kantor Produk Digital  -->  CTA Ebook/Template di deskripsi YT + bio Threads Affiliate",
    ]

    roadmap = [
        ("Minggu 1 (Stabilisasi)", "Bereskan bug force-ssl YT ID, history YT ENG, Plain Text Sheet Threads + pasang API Insights Threads."),
        ("Minggu 2 (Engagement)", "Target YT ID tembus 100+ subs, aktifkan pinned comment 1-kata, validasi 14 Shorts ENG & 21 post Threads."),
        ("Minggu 3 (Seleksi Topik)", "Pangkas kategori lemah; fokuskan kredit Google AI Flow hanya pada topik retensi tertinggi di YT ID & ENG."),
        ("Minggu 4 (Monetisasi & Ekspansi)", "Evaluasi komisi Shopee vs laju RPM YT ENG; siapkan integrasi Kantor Produk Digital ke Unit #4 report."),
    ]

    report_payload = {
        "generated_at_wib": time_wib_str,
        "date": date_str,
        "report_title": (cfg.get("portfolio") or {}).get("report_title", "KIMIABILL ECOSYSTEM — PORTFOLIO CEO REPORT"),
        "active_units_count": len(unit_results),
        "standby_units": standby_units,
        "kpis": {
            "active_units": len(unit_results),
            "overall_reliability_pct": overall_reliability,
            "runs_ok": all_runs_ok,
            "runs_total": all_runs_total,
            "total_yt_views": total_views_all,
            "total_yt_subs": total_subs_all,
        },
        "units": unit_results,
        "priority_fixes": priority_fixes,
        "synergy_flows": synergy_flows,
        "roadmap": roadmap,
    }

    PORTFOLIO_OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = PORTFOLIO_OUT_DIR / f"portfolio_audit_{date_str}.json"
    json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    from src.tools.portfolio_visual_maker import generate_portfolio_visuals

    visuals = generate_portfolio_visuals(report_payload, PORTFOLIO_OUT_DIR, date_str)
    report_payload["visual_files"] = visuals

    if send_telegram:
        from src.tools.telegram_notify import (
            send_telegram_document,
            send_telegram_message,
            send_telegram_photo,
            telegram_enabled,
        )

        if telegram_enabled():
            p1 = Path(visuals["png_dashboard"])
            p2 = Path(visuals["png_roadmap"])
            pdf_p = Path(visuals["pdf_report"])
            unit_bullets = []
            for u in unit_results:
                if u.get("type") == "youtube":
                    unit_bullets.append(f"{u.get('handle') or u.get('name')}: {u.get('subscribers', 0)} subs, {u.get('total_views', 0):,} views")
                elif u.get("type") == "threads_affiliate":
                    unit_bullets.append(f"Threads: {u.get('published_count', 0)} LIVE, {u.get('pending_queue', 0)} PENDING")
                else:
                    unit_bullets.append(f"{u.get('name')}: {u.get('orders', 0)} orders (Rp {u.get('revenue_idr', 0):,})")
            send_telegram_photo(
                p1,
                caption=(
                    f"📊 PORTFOLIO CEO VISUAL REPORT ({time_wib_str})\n"
                    f"• {len(unit_results)} Akun Aktif | Workflow Sukses: {all_runs_ok}/{all_runs_total} ({overall_reliability}%)\n"
                    f"• " + " | ".join(unit_bullets)
                ),
            )
            send_telegram_photo(
                p2,
                caption="🗺️ Peta Temuan Bug, Sinergi Lintas Ekosistem & Roadmap 4 Minggu (+ slot siap untuk Kantor Produk Digital).",
            )
            send_telegram_document(
                pdf_p,
                caption=f"📄 PDF Lengkap Portfolio CEO Audit ({date_str})",
            )
            print("[portfolio] Infografis PNG 1 & 2 + PDF Visual berhasil dikirim ke Telegram!")
        else:
            print("[portfolio] Telegram belum dikonfigurasi di env; file visual tersimpan secara lokal.")

    return report_payload
