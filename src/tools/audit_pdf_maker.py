"""Executive Visual PDF Report Generator for Agent 8 Weekly Audit (Pillow-only, 0 tokens).

Generates a crisp 2-page A4 visual PDF (`WEEKLY_AUDIT_YYYY-MM-DD.pdf`) with:
- Page 1: Branded header, 4 KPI cards, YouTube Partner Program (YPP) monetization
          progress bars, horizontal bar chart of recent video views, executive summary
          & workflow/token efficiency summary.
- Page 2: Top patterns vs weaknesses, token/cost optimizations, color-coded upgrade
          directives for Agents 1, 2, 3, 5, 6, and next-week experiments.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from src.state import Agent8WeeklyAuditOutput

A4_W, A4_H = 1240, 1754  # A4 @ 150 DPI
BG = (245, 247, 251)
WHITE = (255, 255, 255)
NAVY = (17, 24, 39)
SLATE = (71, 85, 105)
MUTED = (148, 163, 184)
BORDER = (226, 232, 240)
BLUE = (37, 99, 235)
CYAN = (8, 145, 178)
GREEN = (22, 163, 74)
AMBER = (217, 119, 6)
RED = (220, 38, 38)
PURPLE = (124, 58, 237)
YELLOW = (250, 204, 21)

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_REG_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _pick_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "h1": _pick_font(FONT_BOLD_CANDIDATES, 36),
        "h2": _pick_font(FONT_BOLD_CANDIDATES, 24),
        "h3": _pick_font(FONT_BOLD_CANDIDATES, 20),
        "kpi": _pick_font(FONT_BOLD_CANDIDATES, 34),
        "body": _pick_font(FONT_REG_CANDIDATES, 18),
        "body_b": _pick_font(FONT_BOLD_CANDIDATES, 18),
        "small": _pick_font(FONT_REG_CANDIDATES, 16),
        "small_b": _pick_font(FONT_BOLD_CANDIDATES, 16),
    }


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int = 8) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), cand, font=font)[2] <= max_w:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) >= max_lines - 1:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines or ["-"]


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill=WHITE, outline=BORDER, radius: int = 18) -> None:
    draw.rounded_rectangle(list(box), radius=radius, fill=fill, outline=outline, width=2)


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    label: str,
    val: int,
    target: int,
    color: tuple[int, int, int],
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> int:
    pct = min(100.0, (val / max(1, target)) * 100.0)
    left = max(0, target - val)
    draw.text((x, y), label, font=fonts["small_b"], fill=NAVY)
    right_txt = f"{val:,} / {target:,} ({pct:.2f}%) • kurang {left:,}"
    tw = draw.textbbox((0, 0), right_txt, font=fonts["small"])[2]
    draw.text((x + w - tw, y), right_txt, font=fonts["small"], fill=SLATE)
    by = y + 26
    draw.rounded_rectangle([x, by, x + w, by + 22], radius=11, fill=(226, 232, 240))
    fill_w = max(18 if val > 0 else 0, int(w * (pct / 100.0)))
    if fill_w > 0:
        draw.rounded_rectangle([x, by, x + fill_w, by + 22], radius=11, fill=color)
    return by + 36


def generate_weekly_audit_pdf(
    audit: Agent8WeeklyAuditOutput,
    enriched_videos: list[dict[str, Any]],
    channel_stats: dict[str, Any],
    out_pdf: Path,
    countries_summary: dict[str, Any] | None = None,
    milestone_stats: dict[str, Any] | None = None,
) -> Path:
    """Render a 3-page executive visual PDF report (including Page 3 International & Milestone DB) and return its Path."""
    fonts = _load_fonts()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    # ======================== PAGE 1: KPI, MONETIZATION & CHARTS ========================
    p1 = Image.new("RGB", (A4_W, A4_H), BG)
    d1 = ImageDraw.Draw(p1)

    # Header Banner
    d1.rectangle([0, 0, A4_W, 155], fill=NAVY)
    d1.text((56, 36), "KIMIABILL STUDIO • LAPORAN AUDIT MINGGUAN", font=fonts["h1"], fill=YELLOW)
    d1.text(
        (56, 92),
        f"Agent 8 (Auditor Tertinggi)  |  Periode Minggu: {audit.week_ending}  |  Channel: {channel_stats.get('title', 'Kimiabill')}",
        font=fonts["body"],
        fill=(203, 213, 225),
    )

    # 4 KPI Cards
    subs = int(channel_stats.get("subscribers") or 0)
    tot_views = int(channel_stats.get("total_views") or 0)
    vid_count = int(channel_stats.get("video_count") or len(enriched_videos))
    qa_scores = [float(v["qa_score"]) for v in enriched_videos if v.get("qa_score") is not None]
    avg_qa = f"{sum(qa_scores) / len(qa_scores):.1f}/10" if qa_scores else "N/A"

    kpis = [
        ("SUBSCRIBER", f"{subs:,}", BLUE),
        ("TOTAL VIEWS", f"{tot_views:,}", CYAN),
        ("TOTAL VIDEO", f"{vid_count:,}", PURPLE),
        ("RATA-RATA QA", avg_qa, GREEN),
    ]
    cw = 266
    gap = 21
    for i, (k_lbl, k_val, k_col) in enumerate(kpis):
        kx0 = 56 + i * (cw + gap)
        _card(d1, (kx0, 185, kx0 + cw, 295))
        d1.rounded_rectangle([kx0, 185, kx0 + 8, 295], radius=4, fill=k_col)
        d1.text((kx0 + 24, 203), k_lbl, font=fonts["small_b"], fill=SLATE)
        d1.text((kx0 + 24, 236), k_val, font=fonts["kpi"], fill=NAVY)

    # Monetization Progress Card (YPP Shorts)
    my0 = 320
    _card(d1, (56, my0, A4_W - 56, my0 + 290))
    d1.text((84, my0 + 22), "PROGRES MENUJU MONETISASI YOUTUBE SHORTS (YPP)", font=fonts["h2"], fill=NAVY)
    cy = my0 + 64
    cy = _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 1 • Fan Funding — Target 500 Subscriber", subs, 500, BLUE, fonts)
    cy = _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 1 • Fan Funding — Target 3 Juta Views (90 hari)", tot_views, 3_000_000, CYAN, fonts)
    cy = _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 2 • Iklan Shorts — Target 1.000 Subscriber", subs, 1000, GREEN, fonts)
    _draw_progress_bar(d1, 84, cy, A4_W - 168, "Tahap 2 • Iklan Shorts — Target 10 Juta Views (90 hari)", tot_views, 10_000_000, AMBER, fonts)

    # Bar Chart: Views per Recent Video
    by0 = 635
    _card(d1, (56, by0, A4_W - 56, by0 + 425))
    d1.text((84, by0 + 22), "VISUALISASI PERFORMA VIDEO TERBARU (VIEWS & RETENSI)", font=fonts["h2"], fill=NAVY)
    top_vids = enriched_videos[:7]
    if top_vids:
        max_v = max(1, max(int(v.get("views") or 0) for v in top_vids))
        bar_colors = [BLUE, CYAN, PURPLE, GREEN, AMBER, RED, (14, 116, 144)]
        vy = by0 + 72
        for idx, v in enumerate(top_vids):
            v_views = int(v.get("views") or 0)
            title = (v.get("title") or v.get("episode_id") or "Video")[:38]
            ret = v.get("avg_view_percentage")
            ret_txt = f" | Ret: {ret}%" if ret is not None else ""
            d1.text((84, vy + 4), title, font=fonts["small_b"], fill=NAVY)
            bar_x0 = 490
            bar_max_w = 420
            bw = max(14, int(bar_max_w * (v_views / max_v)))
            d1.rounded_rectangle([bar_x0, vy + 2, bar_x0 + bar_max_w, vy + 28], radius=8, fill=(238, 242, 247))
            d1.rounded_rectangle([bar_x0, vy + 2, bar_x0 + bw, vy + 28], radius=8, fill=bar_colors[idx % len(bar_colors)])
            d1.text((bar_x0 + bar_max_w + 16, vy + 4), f"{v_views:,} views{ret_txt}", font=fonts["small_b"], fill=SLATE)
            vy += 48
    else:
        d1.text((84, by0 + 90), "Belum ada data video minggu ini.", font=fonts["body"], fill=SLATE)

    # Executive Summary Card
    sy0 = 1085
    _card(d1, (56, sy0, A4_W - 56, sy0 + 280))
    d1.text((84, sy0 + 22), "RINGKASAN PERFORMA MINGGU INI", font=fonts["h2"], fill=BLUE)
    ty = sy0 + 62
    for ln in _wrap(d1, audit.summary, fonts["body"], A4_W - 168, max_lines=8):
        d1.text((84, ty), ln, font=fonts["body"], fill=NAVY)
        ty += 25

    # Workflow & Token Efficiency Card
    wy0 = 1390
    _card(d1, (56, wy0, A4_W - 56, A4_H - 56))
    d1.text((84, wy0 + 22), "AUDIT EFISIENSI WORKFLOW & PENGGUNAAN TOKEN", font=fonts["h2"], fill=AMBER)
    ty = wy0 + 62
    for ln in _wrap(d1, audit.workflow_efficiency_summary or "-", fonts["body"], A4_W - 168, max_lines=8):
        d1.text((84, ty), ln, font=fonts["body"], fill=NAVY)
        ty += 25

    # ======================== PAGE 2: ACTION PLAN & AGENT UPGRADES ========================
    p2 = Image.new("RGB", (A4_W, A4_H), BG)
    d2 = ImageDraw.Draw(p2)
    d2.rectangle([0, 0, A4_W, 110], fill=NAVY)
    d2.text((56, 34), "RENCANA PERBAIKAN & UPGRADE OTOMATIS SELURUH AGENT", font=fonts["h2"], fill=YELLOW)
    d2.text((56, 72), f"Diterapkan otomatis mulai minggu berikutnya  •  Kimiabill Studio ({audit.week_ending})", font=fonts["small"], fill=(203, 213, 225))

    # Top Patterns vs Weaknesses (2 columns)
    col_w = 550
    _card(d2, (56, 138, 56 + col_w, 468))
    d2.text((82, 158), "POLA PALING BERHASIL", font=fonts["h3"], fill=GREEN)
    ty = 196
    for item in (audit.top_performing_patterns or ["-"])[:4]:
        for ln in _wrap(d2, f"• {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > 442:
                break
            d2.text((82, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 22
        ty += 6

    rx0 = 56 + col_w + 28
    _card(d2, (rx0, 138, A4_W - 56, 468))
    d2.text((rx0 + 26, 158), "HAMBATAN ENGAGEMENT", font=fonts["h3"], fill=RED)
    ty = 196
    for item in (audit.bottlenecks_and_weaknesses or ["-"])[:4]:
        for ln in _wrap(d2, f"• {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > 442:
                break
            d2.text((rx0 + 26, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 22
        ty += 6

    # Token & Cost Optimizations
    _card(d2, (56, 492, A4_W - 56, 720))
    d2.text((82, 512), "OPTIMASI TOKEN, KUOTA & KECEPATAN PRODUKSI", font=fonts["h3"], fill=AMBER)
    ty = 550
    for item in (audit.token_and_cost_optimizations or ["-"])[:4]:
        for ln in _wrap(d2, f"• {item}", fonts["small"], A4_W - 168, max_lines=2):
            if ty > 695:
                break
            d2.text((82, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 22
        ty += 6

    # Cross-Agent Directives Card
    _card(d2, (56, 744, A4_W - 56, 1435))
    d2.text((82, 764), "INSTRUKSI UPGRADE OTOMATIS KE SELURUH AGENT", font=fonts["h2"], fill=NAVY)
    agent_blocks = [
        ("AGENT 1 (Trend & Ideasi)", BLUE, audit.agent_1_directives),
        ("AGENT 2 (Auditor Hook)", PURPLE, audit.agent_2_directives),
        ("AGENT 3 (Skrip & Animasi Mekanistik)", CYAN, audit.agent_3_directives),
        ("AGENT 5 (Video QA Auditor)", RED, audit.agent_5_directives),
        ("AGENT 6 (Pencari Footage Real)", AMBER, audit.agent_6_directives),
    ]
    ay = 812
    for label, col, items in agent_blocks:
        if ay > 1390:
            break
        d2.rounded_rectangle([82, ay, 475, ay + 30], radius=8, fill=col)
        d2.text((96, ay + 6), label, font=fonts["small_b"], fill=WHITE)
        ay += 38
        for it in (items or ["Pertahankan standar saat ini."])[:2]:
            for ln in _wrap(d2, f"→ {it}", fonts["small"], A4_W - 180, max_lines=2):
                if ay > 1418:
                    break
                d2.text((92, ay), ln, font=fonts["small"], fill=NAVY)
                ay += 21
        ay += 8

    # Bottom Row: Next Week Experiments + Viewer Requests (Left) + KARAT Weekend Research Recommendations (Right)
    _card(d2, (56, 1455, 56 + col_w, A4_H - 56))
    d2.text((82, 1475), "EKSPERIMEN & REQUEST PENONTON", font=fonts["h3"], fill=GREEN)
    ty = 1511
    for req in (getattr(audit, "audience_requests", None) or [])[:2]:
        req_line = f"► Request {req.requester_name}: {req.topic_title}"
        for ln in _wrap(d2, req_line, fonts["small_b"], col_w - 52, max_lines=2):
            if ty > A4_H - 72:
                break
            d2.text((82, ty), ln, font=fonts["small_b"], fill=BLUE)
            ty += 21
        ty += 4
    for item in (audit.next_week_experiments or ["-"])[:3]:
        for ln in _wrap(d2, f"★ {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > A4_H - 72:
                break
            d2.text((82, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 21
        ty += 5

    _card(d2, (rx0, 1455, A4_W - 56, A4_H - 56))
    d2.text((rx0 + 26, 1475), "ARAHAN RISET EDISI KARAT (WEEKEND)", font=fonts["h3"], fill=(234, 88, 12))
    ty = 1511
    for item in (audit.karat_weekend_directives or ["Angkat riset terbaru dari topik paling rame minggu ini."])[:3]:
        for ln in _wrap(d2, f"◆ {item}", fonts["small"], col_w - 52, max_lines=3):
            if ty > A4_H - 72:
                break
            d2.text((rx0 + 26, ty), ln, font=fonts["small"], fill=NAVY)
            ty += 21
        ty += 5

    # ======================== PAGE 3: INTERNATIONAL POTENTIAL & MILESTONE DATABASE ========================
    cs = countries_summary or {}
    ms = milestone_stats or {}
    p3 = Image.new("RGB", (A4_W, A4_H), BG)
    d3 = ImageDraw.Draw(p3)
    d3.rectangle([0, 0, A4_W, 110], fill=NAVY)
    d3.text((56, 34), "POTENSI INTERNASIONAL & DATABASE MILESTONE STUDIO", font=fonts["h2"], fill=YELLOW)
    d3.text(
        (56, 72),
        f"Data Kumulatif Jangka Panjang (SQLite + 1 Master CSV Mingguan)  •  Periode {audit.week_ending}",
        font=fonts["small"],
        fill=(203, 213, 225),
    )

    k_avg_str = f"{ms['karat_avg_views']:.0f}" if ms.get("karat_avg_views") is not None else "-"
    non_id_str = f"{float(cs.get('non_id_views_pct') or ms.get('latest_non_id_pct') or 0.0):.1f}%"
    db_kpis = [
        ("TOTAL EPISODE ARSIP", f"{int(ms.get('total_videos_archived') or len(enriched_videos)):,}", BLUE),
        ("MINGGU DIAUDIT", f"{int(ms.get('total_weeks_audited') or 1):,}", PURPLE),
        ("AVG VIEWS KARAT", k_avg_str, (234, 88, 12)),
        ("VIEWER LUAR RI", non_id_str, CYAN),
    ]
    for i, (k_lbl, k_val, k_col) in enumerate(db_kpis):
        kx0 = 56 + i * (cw + gap)
        _card(d3, (kx0, 140, kx0 + cw, 250))
        d3.rounded_rectangle([kx0, 140, kx0 + 8, 250], radius=4, fill=k_col)
        d3.text((kx0 + 24, 158), k_lbl, font=fonts["small_b"], fill=SLATE)
        d3.text((kx0 + 24, 191), k_val, font=fonts["kpi"], fill=NAVY)

    # Top Countries Card
    cy0 = 278
    _card(d3, (56, cy0, A4_W - 56, cy0 + 440))
    d3.text((84, cy0 + 22), "SEBARAN NEGARA PENONTON (28 HARI TERAKHIR)", font=fonts["h2"], fill=NAVY)
    top_countries = cs.get("top_countries") or []
    if top_countries:
        max_cv = max(1, max(int(c.get("views") or 0) for c in top_countries[:8]))
        tot_cv = max(1, int(cs.get("analytics_total_views") or sum(int(c.get("views") or 0) for c in top_countries)))
        vy = cy0 + 74
        c_colors = [BLUE, CYAN, GREEN, PURPLE, AMBER, RED, (14, 116, 144), SLATE]
        for idx, c in enumerate(top_countries[:8]):
            code = str(c.get("country") or "-")
            c_views = int(c.get("views") or 0)
            pct_c = (c_views / tot_cv) * 100.0
            d3.text((84, vy + 4), f"Negara {code}", font=fonts["small_b"], fill=NAVY)
            bx0, bmax = 310, 580
            bw = max(14 if c_views > 0 else 0, int(bmax * (c_views / max_cv)))
            d3.rounded_rectangle([bx0, vy + 2, bx0 + bmax, vy + 28], radius=8, fill=(238, 242, 247))
            if bw > 0:
                d3.rounded_rectangle([bx0, vy + 2, bx0 + bw, vy + 28], radius=8, fill=c_colors[idx % len(c_colors)])
            d3.text((bx0 + bmax + 18, vy + 4), f"{c_views:,} views ({pct_c:.1f}%)", font=fonts["small_b"], fill=SLATE)
            vy += 44
    else:
        d3.text((84, cy0 + 84), "Data negara belum tersedia pada siklus ini (akan aktif otomatis setelah Apps Script diperbarui).", font=fonts["body"], fill=SLATE)

    # International Viewer Potential Assessment Card
    iy0 = 746
    _card(d3, (56, iy0, A4_W - 56, iy0 + 380))
    d3.text((84, iy0 + 22), "ANALISIS POTENSI VIEWER INTERNASIONAL & EKSPANSI CHANNEL INGGRIS", font=fonts["h2"], fill=CYAN)
    intl_txt = (getattr(audit, "international_potential", "") or "").strip() or (
        "Pantau persentase penonton non-Indonesia, komentar berbahasa Inggris, serta performa visual topik universal "
        "selama 2-4 minggu sebagai acuan pembukaan channel internasional."
    )
    ty = iy0 + 66
    for ln in _wrap(d3, intl_txt, fonts["body"], A4_W - 168, max_lines=10):
        d3.text((84, ty), ln, font=fonts["body"], fill=NAVY)
        ty += 26

    # Cumulative Milestone Database Card
    dy0 = 1154
    _card(d3, (56, dy0, A4_W - 56, A4_H - 56))
    d3.text((84, dy0 + 22), "REKAP DATABASE KUMULATIF & KESIAPAN MILESTONE (KARAT PRO / MULTI-CHANNEL)", font=fonts["h2"], fill=PURPLE)
    r_avg_v = f"{ms['regular_avg_views']:.1f} views" if ms.get("regular_avg_views") is not None else "belum cukup data"
    k_avg_v = f"{ms['karat_avg_views']:.1f} views" if ms.get("karat_avg_views") is not None else "belum cukup data"
    r_avg_ret = f"{ms['regular_avg_retention_pct']:.1f}%" if ms.get("regular_avg_retention_pct") is not None else "-"
    k_avg_ret = f"{ms['karat_avg_retention_pct']:.1f}%" if ms.get("karat_avg_retention_pct") is not None else "-"
    milestone_lines = [
        f"• Total Video Terarsip Permanen: {int(ms.get('total_videos_archived') or len(enriched_videos))} episode (Reguler: {ms.get('regular_videos', 0)} | KARAT: {ms.get('karat_videos', 0)})",
        f"• Perbandingan Performa Segmen: REGULER rata-rata {r_avg_v} (retensi {r_avg_ret})  vs  KARAT rata-rata {k_avg_v} (retensi {k_avg_ret})",
        f"• Disiplin Sains Terkuat (Kumulatif): {ms.get('top_discipline', '-')} (rata-rata {ms.get('top_discipline_avg_views', 0.0):.1f} views/video)",
        f"• Sinyal Komentar Asing Minggu Ini: {len(cs.get('intl_comments') or [])} komentar non-Indonesia terdeteksi",
        "• Master Database CSV: File tunggal KIMIABILL_MASTER_DATABASE.csv otomatis diperbarui setiap Minggu di GitHub & Google Drive (Weekly_Audits) untuk analisis Excel/Google Sheets.",
    ]
    ty = dy0 + 72
    for item in milestone_lines:
        for ln in _wrap(d3, item, fonts["body"], A4_W - 168, max_lines=3):
            if ty > A4_H - 80:
                break
            d3.text((84, ty), ln, font=fonts["body"], fill=NAVY)
            ty += 26
        ty += 12

    p1.save(out_pdf, "PDF", resolution=150.0, save_all=True, append_images=[p2, p3])
    return out_pdf
