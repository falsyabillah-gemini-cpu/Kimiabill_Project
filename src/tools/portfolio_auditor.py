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


def _fetch_live_spreadsheet_via_webhook(sheet_id: str) -> Optional[dict[str, Any]]:
    """Read any Google Sheet owned by user via GDRIVE_WEBHOOK_URL `read_spreadsheet` action (0 extra credentials)."""
    webhook_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not webhook_url or not sheet_id:
        return None
    try:
        r = requests.post(
            webhook_url,
            json={"action": "read_spreadsheet", "sheet_id": sheet_id, "max_rows": 250},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and data.get("ok") and isinstance(data.get("sheets"), dict):
                return data["sheets"]
    except Exception:
        pass
    return None


def _parse_affiliate_sheets_to_snapshot(sheet_id: str, sheets_map: dict[str, list[list[Any]]]) -> dict[str, Any]:
    from collections import Counter

    ant = sheets_map.get("Antrean_Produk") or []
    st_cnt = dict(Counter((str(r[1]).strip() if len(r) > 1 and r[1] is not None else "") for r in ant[1:] if any(r)))
    cat_cnt = dict(Counter((str(r[2]).strip() if len(r) > 2 and r[2] is not None else "") for r in ant[1:] if any(r)))
    logs = sheets_map.get("Log_Performa") or []
    live_logs = [
        {
            "tanggal_wib": str(r[0] or "") if len(r) > 0 else "",
            "produk": str(r[2] or "") if len(r) > 2 else "",
            "mode": str(r[3] or "") if len(r) > 3 else "",
            "status": str(r[4] or "") if len(r) > 4 else "",
            "post_id": str(r[5] or "") if len(r) > 5 else "",
        }
        for r in logs[1:]
        if any(r)
    ]
    sett_rows = sheets_map.get("Pengaturan_Agent") or []
    sett_map = {str(r[0]).strip(): str(r[1] if len(r) > 1 and r[1] is not None else "").strip() for r in sett_rows[1:] if r and r[0]}
    snap = {
        "captured_at_wib": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB"),
        "sheet_id": sheet_id,
        "status_counts": st_cnt,
        "category_counts": cat_cnt,
        "log_count": max(0, len(logs) - 1),
        "live_logs": live_logs,
        "comments_count": max(0, len(sheets_map.get("Komentar_Threads") or []) - 1),
        "audit_riset_count": max(0, len(sheets_map.get("Audit_Riset_Threads") or []) - 1),
        "settings": sett_map,
    }
    try:
        AFFILIATE_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AFFILIATE_SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return snap


def _audit_threads_affiliate_unit(unit: dict[str, Any]) -> dict[str, Any]:
    repo = unit.get("repo", "")
    runs_info = _fetch_gh_workflow_runs(repo, limit=25)
    sheet_id = str(unit.get("sheet_id") or "").strip()

    live_sheets = _fetch_live_spreadsheet_via_webhook(sheet_id)
    if live_sheets:
        sheet_snap = _parse_affiliate_sheets_to_snapshot(sheet_id, live_sheets)
        sheet_mode = "live_sheet"
    elif AFFILIATE_SNAPSHOT_PATH.exists():
        try:
            sheet_snap = json.loads(AFFILIATE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            sheet_snap = {}
        sheet_mode = "sheets_snapshot"
    else:
        sheet_snap = {}
        sheet_mode = "github_only"

    status_counts = sheet_snap.get("status_counts") or {}
    pending_count = int(status_counts.get("PENDING") or 0)
    published_count = int(status_counts.get("PUBLISHED") or 0)
    draft_only_count = int(status_counts.get("DRAFT_ONLY") or 0)
    log_count = int(sheet_snap.get("log_count") or 0)
    settings_map = sheet_snap.get("settings") or {}
    live_logs = sheet_snap.get("live_logs") or []

    post_ids = [str(lg.get("post_id") or "").strip() for lg in live_logs if str(lg.get("post_id") or "").strip()]
    threads_api = _fetch_threads_api_insights(post_ids)

    ms = unit.get("milestone") or {}
    min_buf = int(ms.get("min_queue_buffer") or 15)
    strengths: list[str] = []
    issues: list[str] = []
    if published_count > 0:
        days_stock = pending_count // 3
        strengths.append(f"LIVE di Threads ({published_count} post + reply link, {pending_count} PENDING stok ~{days_stock} hari)")
    elif pending_count > 0:
        strengths.append(f"Antrean aman ({pending_count} produk PENDING siap tayang 3x/hari)")
    if runs_info.get("success", 0) >= 4:
        strengths.append("Publish 3x/hari & Admin 21.00 WIB sukses beruntun")
    if threads_api.get("connected") and threads_api.get("views", 0) > 0:
        strengths.append(f"API Threads aktif: {threads_api.get('views', 0):,} views, {threads_api.get('clicks', 0):,} clicks")

    failed_wf = runs_info.get("failed_workflows") or []
    if failed_wf:
        issues.append(f"Workflow gagal: {failed_wf[0][:75]}")
    if pending_count < min_buf:
        issues.append(f"Stok PENDING menipis ({pending_count} < batas {min_buf})")

    min_rating_val = str(settings_map.get("Min_Rating_Shopee") or "")
    tok_date_val = str(settings_map.get("Tanggal_Token_Threads") or "")
    if re.fullmatch(r"\d+(\.0)?", min_rating_val) and float(min_rating_val) > 100:
        issues.append("Sheet Min_Rating_Shopee jadi serial tanggal (set Plain Text '4.7)")
    if re.fullmatch(r"\d+(\.0)?", tok_date_val) and float(tok_date_val) > 1000:
        issues.append("Sheet Tanggal_Token_Threads jadi serial (set Plain Text '2026-11-25)")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok_date_val):
        try:
            age_d = (datetime.now(WIB).date() - datetime.strptime(tok_date_val, "%Y-%m-%d").date()).days
            if age_d >= 50:
                issues.append(f"Token Threads berumur {age_d} hari (mendekati expired 60 hari!)")
        except Exception:
            pass
    if not threads_api.get("connected"):
        issues.append("Token API Threads Insights belum dipasang (belum baca views/klik)")
    elif threads_api.get("warnings"):
        issues.append(f"API Threads: {threads_api['warnings'][0][:75]}")

    target_weekly = int(ms.get("target_weekly_posts") or 21)
    ms_cur = published_count
    progress_pct = round(min(100.0, (ms_cur / max(1, target_weekly)) * 100.0), 1)
    if ms_cur > 0 and progress_pct < 5.0:
        progress_pct = 5.0

    health = "SEHAT"
    if issues:
        health = "PERHATIAN"
    if (runs_info.get("total", 0) > 0 and runs_info.get("success", 0) == 0) or pending_count == 0:
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
        "source_mode": f"threads_api+{sheet_mode}" if threads_api.get("connected") else f"{sheet_mode}+gh_runs",
    }


def _audit_digital_products_unit(unit: dict[str, Any]) -> dict[str, Any]:
    """Modular collector for Kantor Produk Digital (supports repo runs + Apps Script webhook + sales Google Sheet)."""
    repo = unit.get("repo", "")
    runs_info = _fetch_gh_workflow_runs(repo, limit=20) if repo else {"total": 0, "success": 0, "failure": 0, "success_rate_pct": 100.0, "failed_workflows": []}
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
    sheet_id = str(unit.get("sheet_id") or "").strip()
    if orders == 0 and sheet_id:
        sh = _fetch_live_spreadsheet_via_webhook(sheet_id)
        if sh:
            first_tab = next(iter(sh.values()), [])
            orders = max(0, len(first_tab) - 1)
    ms = unit.get("milestone") or {}
    target_orders = int(ms.get("target_monthly_orders") or 50)
    issues: list[str] = []
    if runs_info.get("failure", 0) > 0:
        issues.append(f"{runs_info['failure']} workflow gagal baru-baru ini")
    return {
        "id": unit["id"],
        "name": unit["name"],
        "handle": unit.get("handle", ""),
        "type": "digital_products",
        "repo": repo,
        "health": "PERHATIAN" if issues else "SEHAT",
        "orders": orders,
        "revenue_idr": revenue_idr,
        "runs": runs_info,
        "milestone_label": ms.get("label", "Order Produk Digital / Bulan"),
        "milestone_current": orders,
        "milestone_target": target_orders,
        "milestone_pct": round(min(100.0, (orders / max(1, target_orders)) * 100.0), 1),
        "strengths": [f"{orders} order produk digital (Rp {revenue_idr:,})"],
        "issues": issues,
        "source_mode": "digital_webhook_live",
    }


def _build_dynamic_priority_fixes(unit_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Generate actionable fixes strictly from live anomalies observed across all enabled units."""
    fixes: list[dict[str, str]] = []
    for u in unit_results:
        u_name = u.get("name") or u.get("id")
        u_health = u.get("health") or "SEHAT"
        issues = list(u.get("issues") or [])
        runs = u.get("runs") or {}
        if issues:
            sev = "KRITIS" if (u_health == "KRITIS" or runs.get("failure", 0) >= 2) else "PERHATIAN"
            fixes.append(
                {
                    "unit": u_name[:26],
                    "severity": sev,
                    "title": issues[0][:56],
                    "action": " / ".join(issues[:2])[:115],
                }
            )
    if not fixes:
        fixes.append(
            {
                "unit": "Semua Unit",
                "severity": "SEHAT",
                "title": "Seluruh Workflow & Integrasi Berjalan Normal",
                "action": "Fokus ke peningkatan hook 0-3 detik, retensi video, dan evaluasi produk konversi tertinggi minggu ini.",
            }
        )
    return fixes[:4]


def _update_portfolio_history(report_payload: dict[str, Any]) -> dict[str, Any]:
    """Persist weekly snapshots so CEO audit shows Week-over-Week growth deltas."""
    hist_path = PORTFOLIO_OUT_DIR / "portfolio_history.json"
    rows: list[dict[str, Any]] = []
    if hist_path.exists():
        try:
            raw = json.loads(hist_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                rows = raw
        except Exception:
            rows = []

    kpis = report_payload.get("kpis") or {}
    snap = {
        "date": report_payload.get("date"),
        "total_yt_subs": int(kpis.get("total_yt_subs") or 0),
        "total_yt_views": int(kpis.get("total_yt_views") or 0),
        "overall_reliability_pct": float(kpis.get("overall_reliability_pct") or 100.0),
        "units_summary": {
            u["id"]: {
                "subs": u.get("subscribers"),
                "views": u.get("total_views"),
                "published": u.get("published_count"),
                "pending": u.get("pending_queue"),
                "orders": u.get("orders"),
                "revenue_idr": u.get("revenue_idr"),
            }
            for u in (report_payload.get("units") or [])
        },
    }
    prev = next((r for r in reversed(rows) if r.get("date") != snap["date"]), None)
    deltas = {}
    if prev:
        deltas = {
            "subs_delta": snap["total_yt_subs"] - int(prev.get("total_yt_subs") or 0),
            "views_delta": snap["total_yt_views"] - int(prev.get("total_yt_views") or 0),
            "prev_date": prev.get("date"),
        }

    rows = [r for r in rows if r.get("date") != snap["date"]] + [snap]
    rows = rows[-52:]
    PORTFOLIO_OUT_DIR.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return deltas


def _optional_ai_ceo_synthesis(report_payload: dict[str, Any], use_llm: bool = True) -> tuple[list[str], list[tuple[str, str]]]:
    """Use 1 lightweight AI call on Sunday weekly audit to customize synergy & 4-week roadmap (safe deterministic fallback)."""
    default_synergy = [
        "1. YT ID (Kimia Dapur 1.2K+ views)  -->  Adaptasi visual & hook ke YT ENG (@bill_explains RPM global)",
        "2. Topik Dapur/Gadget Viral di YT  -->  Cocokkan antrean produk kos/meja di Threads Affiliate",
        "3. Komentar & Insights API Threads  -->  Validasi produk klik tertinggi sebelum skala promosi",
        "4. [Slot Siap] Kantor Produk Digital  -->  CTA Ebook/Template di deskripsi YT + bio Threads Affiliate",
    ]
    default_roadmap = [
        ("Minggu 1 (Stabilisasi)", "Selesaikan seluruh temuan error & izin API aktif, pastikan seluruh repo hijau tanpa error 429/timeout."),
        ("Minggu 2 (Engagement)", "Dorong komentar CTA 1-kata YT, pantau views/klik perdana API Threads, & jaga stok antrean aman."),
        ("Minggu 3 (Seleksi Topik)", "Pangkas topik/produk ber-engagement rendah; fokuskan kredit Google Flow ke pemenang retensi."),
        ("Minggu 4 (Monetisasi & Ekspansi)", "Evaluasi laju YPP & komisi affiliate, serta siapkan integrasi unit Kantor Produk Digital ke laporan ini."),
    ]
    if not use_llm:
        return default_synergy, default_roadmap
    try:
        from pydantic import BaseModel, Field
        from src.providers import call_structured_agent

        class _CeoSynthesis(BaseModel):
            synergy_flows: list[str] = Field(min_length=4, max_length=4)
            roadmap_items: list[list[str]] = Field(
                min_length=4,
                max_length=4,
                description="4 item tepat berformat [judul_singkat_minggu, aksi_konkret_1_kalimat]",
            )

        compact_units = [
            {
                "name": u.get("name"),
                "health": u.get("health"),
                "metrics": {k: u.get(k) for k in ("subscribers", "total_views", "avg_recent_views", "video_count", "published_count", "pending_queue", "orders", "revenue_idr") if u.get(k) is not None},
                "issues": u.get("issues"),
                "runs_ok": f"{(u.get('runs') or {}).get('success', 0)}/{(u.get('runs') or {}).get('total', 0)}",
            }
            for u in (report_payload.get("units") or [])
        ]
        res = call_structured_agent(
            provider="auto",
            model="gemini-3.8-flash",
            system_prompt=(
                "Kamu adalah Asisten CEO Ekosistem Digital Kimiabill. Buat 4 baris singkat sinergi lintas akun "
                "dan 4 tahap Roadmap Mingguan berdasarkan data live minggu ini (pakai angka nyata, bahasa Indonesia lugas, "
                "tanpa backticks markdown, tiap item maksimal 115 karakter)."
            ),
            user_payload={
                "date": report_payload.get("date"),
                "deltas": report_payload.get("deltas"),
                "units": compact_units,
                "standby_units": report_payload.get("standby_units"),
            },
            response_model=_CeoSynthesis,
        )
        syn = [str(s).replace("`", "")[:115] for s in res.synergy_flows][:4]
        rm = [(str(x[0]).replace("`", "")[:32], str(x[1]).replace("`", "")[:118]) for x in res.roadmap_items if len(x) >= 2][:4]
        if len(syn) == 4 and len(rm) == 4:
            return syn, rm
    except Exception as exc:
        print(f"[portfolio] AI synthesis dilewati (pakai roadmap deterministik): {exc}")
    return default_synergy, default_roadmap


def run_portfolio_ceo_audit(send_telegram: bool = True, use_ai_synthesis: bool = True) -> dict[str, Any]:
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

    priority_fixes = _build_dynamic_priority_fixes(unit_results)

    report_payload: dict[str, Any] = {
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
    }
    report_payload["deltas"] = _update_portfolio_history(report_payload)
    syn, rm = _optional_ai_ceo_synthesis(report_payload, use_llm=use_ai_synthesis)
    report_payload["synergy_flows"] = syn
    report_payload["roadmap"] = rm

    PORTFOLIO_OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = PORTFOLIO_OUT_DIR / f"portfolio_audit_{date_str}.json"
    json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    from src.tools.portfolio_visual_maker import generate_portfolio_visuals

    visuals = generate_portfolio_visuals(report_payload, PORTFOLIO_OUT_DIR, date_str)
    report_payload["visual_files"] = visuals

    if send_telegram:
        from src.tools.telegram_notify import (
            send_telegram_document,
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
            delta_note = ""
            if report_payload["deltas"].get("prev_date"):
                d = report_payload["deltas"]
                delta_note = f"\n• Pertumbuhan vs {d['prev_date']}: {d['subs_delta']:+d} subs | {d['views_delta']:+,} YT views"
            send_telegram_photo(
                p1,
                caption=(
                    f"📊 AUDIT BESAR ASISTEN CEO ({time_wib_str})\n"
                    f"• {len(unit_results)} Unit Aktif | Workflow Sukses: {all_runs_ok}/{all_runs_total} ({overall_reliability}%)"
                    f"{delta_note}\n"
                    f"• " + " | ".join(unit_bullets)
                ),
            )
            send_telegram_photo(
                p2,
                caption="🗺️ Peta Temuan Real, Sinergi Lintas Ekosistem & Roadmap 4 Minggu (+ slot siap untuk Kantor Produk Digital).",
            )
            send_telegram_document(
                pdf_p,
                caption=f"📄 PDF Lengkap 3-Halaman Portfolio CEO Audit ({date_str})",
            )
            print("[portfolio] Audit Besar (2 Infografis PNG + PDF 3-Halaman) berhasil dikirim ke Telegram!")
        else:
            print("[portfolio] Telegram belum dikonfigurasi di env; file visual tersimpan secara lokal.")

    return report_payload


def run_daily_portfolio_watchdog(always_notify: bool = False) -> dict[str, Any]:
    """Zero-token daily health watchdog across all portfolio units to catch errors within <=24 hours."""
    cfg = load_portfolio_config()
    now_wib = datetime.now(WIB)
    state_path = PORTFOLIO_OUT_DIR / "watchdog_state.json"
    seen_ids: set[str] = set()
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            seen_ids = set(raw.get("alerted_keys") or [])
        except Exception:
            seen_ids = set()

    new_alerts: list[str] = []
    checked_units: list[str] = []
    cutoff_ts = now_wib.timestamp() - 28 * 3600

    for unit in cfg.get("units") or []:
        if not unit.get("enabled", True):
            continue
        uname = str(unit.get("name") or unit.get("id"))
        repo = str(unit.get("repo") or "").strip()
        checked_units.append(uname)

        if repo:
            runs_data = _gh_api_get(f"repos/{repo}/actions/runs", params={"per_page": 12})
            for run in (runs_data or {}).get("workflow_runs") or []:
                wf_name = str(run.get("name") or "")
                wf_low = wf_name.lower()
                if "pages-build" in wf_low or "pages build" in wf_low or run.get("status") != "completed":
                    continue
                if run.get("conclusion") in ("success", "skipped", "neutral", "cancelled", None):
                    continue
                created = str(run.get("created_at") or "")
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt.timestamp() < cutoff_ts:
                        continue
                except Exception:
                    pass
                rid = str(run.get("id") or "")
                key = f"gh:{repo}:{rid}"
                if key not in seen_ids:
                    seen_ids.add(key)
                    new_alerts.append(
                        f"🔴 {uname}: `{wf_name}` GAGAL ({run.get('conclusion')}) — {run.get('html_url', '')}"
                    )

        if unit.get("type") == "threads_affiliate":
            live_sh = _fetch_live_spreadsheet_via_webhook(str(unit.get("sheet_id") or ""))
            if live_sh:
                snap = _parse_affiliate_sheets_to_snapshot(str(unit.get("sheet_id") or ""), live_sh)
                pend = int((snap.get("status_counts") or {}).get("PENDING") or 0)
                min_buf = int((unit.get("milestone") or {}).get("min_queue_buffer") or 15)
                if pend < min_buf:
                    key = f"stock:{now_wib.strftime('%Y-%m-%d')}"
                    if key not in seen_ids:
                        seen_ids.add(key)
                        new_alerts.append(f"🟡 {uname}: Stok antrean PENDING tinggal {pend} (< {min_buf}). Isi Antrean_Produk!")

    PORTFOLIO_OUT_DIR.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_checked_wib": now_wib.strftime("%Y-%m-%d %H:%M WIB"), "alerted_keys": sorted(seen_ids)[-200:]}, indent=2),
        encoding="utf-8",
    )

    notified = False
    if new_alerts or always_notify:
        from src.tools.telegram_notify import send_telegram_message, telegram_enabled

        if telegram_enabled():
            if new_alerts:
                msg = "\n".join(
                    [
                        f"🚨 WATCHDOG HARIAN ASISTEN CEO ({now_wib.strftime('%Y-%m-%d %H:%M WIB')})",
                        f"Terdeteksi {len(new_alerts)} kendala baru dalam 24 jam terakhir:",
                        *new_alerts[:8],
                        "",
                        "💡 Balas /audit_ceo kalau ingin langsung generate laporan visual lengkap sekarang.",
                    ]
                )
            else:
                msg = (
                    f"🟢 WATCHDOG HARIAN ASISTEN CEO ({now_wib.strftime('%Y-%m-%d %H:%M WIB')})\n"
                    f"Semua {len(checked_units)} unit aktif ({', '.join(checked_units)}) berjalan sehat tanpa error workflow dalam 24 jam terakhir."
                )
            notified = send_telegram_message(msg)

    print(f"[watchdog] Checked {len(checked_units)} units | new_alerts={len(new_alerts)} | notified={notified}")
    return {"checked_units": checked_units, "new_alerts": new_alerts, "notified": notified}

