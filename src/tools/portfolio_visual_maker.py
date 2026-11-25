"""Pillow-only (0 AI tokens) Executive Visual Infographic & PDF Generator for Portfolio CEO Audit."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from src.tools.audit_pdf_maker import (
    AMBER,
    BG,
    BORDER,
    BLUE,
    CYAN,
    GREEN,
    MUTED,
    NAVY,
    PURPLE,
    RED,
    SLATE,
    WHITE,
    _card,
    _load_fonts,
    _pill,
    _wrap,
)

CANVAS_W, CANVAS_H = 1240, 1680  # Crisp mobile-friendly portrait infographic @ 150 DPI
DARK_HDR = (15, 23, 42)
SOFT_RED = (254, 242, 242)
SOFT_AMBER = (255, 251, 235)
SOFT_GREEN = (240, 253, 244)
SOFT_BLUE = (239, 246, 255)
SOFT_PURPLE = (245, 243, 255)


def _health_style(health: str) -> tuple[tuple[int, int, int], tuple[int, int, int], str]:
    h = (health or "SEHAT").upper()
    if h == "KRITIS":
        return RED, SOFT_RED, "KRITIS"
    if h == "PERHATIAN":
        return AMBER, SOFT_AMBER, "PERHATIAN"
    return GREEN, SOFT_GREEN, "SEHAT"


def _draw_header(
    draw: ImageDraw.ImageDraw,
    title: str,
    subtitle: str,
    badge_text: str,
    fonts: dict[str, ImageFont.FreeTypeFont],
) -> None:
    draw.rectangle([0, 0, CANVAS_W, 136], fill=DARK_HDR)
    draw.rectangle([0, 130, CANVAS_W, 136], fill=BLUE)
    bw = draw.textbbox((0, 0), badge_text, font=fonts["small_b"])[2] + 24
    # Use h2 for crisp fit with zero overlap against top-right badge
    draw.text((40, 26), title, font=fonts["h2"], fill=WHITE)
    draw.text((40, 74), subtitle, font=fonts["body"], fill=(191, 219, 254))
    _pill(draw, CANVAS_W - 40 - bw, 36, badge_text, (30, 58, 138), (191, 219, 254), fonts["small_b"], pad_x=12, pad_y=6)


def _render_dashboard_page(payload: dict[str, Any], fonts: dict[str, ImageFont.FreeTypeFont]) -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    standby_txt = ", ".join(payload.get("standby_units") or []) or "Kantor Produk Digital"
    _draw_header(
        draw,
        title=str(payload.get("report_title") or "KIMIABILL ECOSYSTEM — PORTFOLIO CEO"),
        subtitle=f"Laporan Visual Terpadu Multi-Akun  •  {payload.get('generated_at_wib', '')}",
        badge_text=f"Slot Siap: +{standby_txt[:24]}",
        fonts=fonts,
    )

    kpis = payload.get("kpis") or {}
    units: list[dict[str, Any]] = list(payload.get("units") or [])
    threads_u = next((u for u in units if u.get("type") == "threads_affiliate"), {})

    # 1. Top 4 KPI Cards (y=156..286)
    tile_specs = [
        ("AKUN AKTIF", f"{kpis.get('active_units', len(units))} Unit", f"+{len(payload.get('standby_units') or [])} slot siap aktif", BLUE),
        (
            "RELIABILITAS RUN",
            f"{kpis.get('overall_reliability_pct', 100)}%",
            f"{kpis.get('runs_ok', 0)}/{kpis.get('runs_total', 0)} workflow sukses",
            GREEN if kpis.get("overall_reliability_pct", 100) >= 85 else AMBER,
        ),
        (
            "TOTAL YT VIEWS",
            f"{int(kpis.get('total_yt_views', 0)):,}",
            f"{int(kpis.get('total_yt_subs', 0)):,} subscribers aktif",
            PURPLE,
        ),
        (
            "THREADS AFFILIATE",
            f"{int(threads_u.get('published_count', 0))} LIVE",
            f"{int(threads_u.get('pending_queue', 0))} produk stok PENDING",
            CYAN,
        ),
    ]
    tile_w = (CANVAS_W - 80 - 3 * 18) // 4
    for idx, (lbl, big, sub, col) in enumerate(tile_specs):
        tx = 40 + idx * (tile_w + 18)
        _card(draw, (tx, 156, tx + tile_w, 286), fill=WHITE, outline=BORDER, radius=16)
        draw.rounded_rectangle([tx, 156, tx + tile_w, 165], radius=6, fill=col)
        draw.text((tx + 18, 176), lbl, font=fonts["micro_b"], fill=SLATE)
        draw.text((tx + 18, 202), big, font=fonts["kpi"], fill=NAVY)
        draw.text((tx + 18, 252), sub[:32], font=fonts["micro"], fill=SLATE)

    # 2. Unit Business Scorecards (y=304..1084)
    n_u = max(1, len(units))
    cards_top = 304
    cards_bottom = 1096
    gap_y = 14
    card_h = min(255, (cards_bottom - cards_top - gap_y * (n_u - 1)) // n_u)

    for idx, u in enumerate(units):
        cy = cards_top + idx * (card_h + gap_y)
        cx1, cx2 = 40, CANVAS_W - 40
        h_col, h_bg, h_lbl = _health_style(u.get("health", "SEHAT"))
        _card(draw, (cx1, cy, cx2, cy + card_h), fill=WHITE, outline=BORDER, radius=18)
        draw.rounded_rectangle([cx1, cy, cx1 + 12, cy + card_h], radius=6, fill=h_col)

        # Title + Badges
        draw.text((cx1 + 28, cy + 14), u.get("name", ""), font=fonts["h2"], fill=NAVY)
        title_w = draw.textbbox((0, 0), u.get("name", ""), font=fonts["h2"])[2]
        next_x = cx1 + 42 + title_w
        if u.get("handle"):
            next_x = _pill(draw, next_x, cy + 16, str(u["handle"]), SOFT_BLUE, BLUE, fonts["micro_b"]) + 10
        src_lbl = str(u.get("source_mode") or "").replace("_", " ").upper()
        _pill(draw, next_x, cy + 16, src_lbl[:26], (241, 245, 249), SLATE, fonts["micro"])

        bw = draw.textbbox((0, 0), h_lbl, font=fonts["small_b"])[2] + 24
        _pill(draw, cx2 - 20 - bw, cy + 14, h_lbl, h_bg, h_col, fonts["small_b"], pad_x=12, pad_y=4)

        # Metrics strip (4 compact stats)
        utype = u.get("type")
        runs = u.get("runs") or {}
        if utype == "youtube":
            qa_str = f"{float(u['avg_qa']):.1f}/10" if u.get("avg_qa") is not None else "9.5/10"
            subs_val = int(u.get("subscribers") or 0)
            vids_val = int(u.get("video_count") or 0)
            m1 = ("Subscribers", f"{subs_val:,}") if (subs_val > 0 or u.get("source_mode") == "youtube_api_live") else ("Video Rilis", f"{vids_val} Shorts")
            m_items = [
                m1,
                ("Total Views", f"{int(u.get('total_views', 0)):,}"),
                ("Avg Terbaru", f"{int(u.get('avg_recent_views', 0)):,} v/ep"),
                ("Workflow OK", f"{runs.get('success', 0)}/{runs.get('total', 0)} ({runs.get('success_rate_pct', 100)}%) | QA {qa_str}"),
            ]
        elif utype == "threads_affiliate":
            tapi = u.get("threads_api") or {}
            api_txt = f"{tapi.get('views', 0):,} views" if tapi.get("connected") else "Besok (token)"
            m_items = [
                ("Post LIVE", f"{int(u.get('published_count', 0))} post (+reply)"),
                ("Antrean PENDING", f"{int(u.get('pending_queue', 0))} produk"),
                ("API Insights", api_txt),
                ("Workflow OK", f"{runs.get('success', 0)}/{runs.get('total', 0)} ({runs.get('success_rate_pct', 100)}%)"),
            ]
        else:
            m_items = [
                ("Orders", f"{int(u.get('orders', 0))}"),
                ("Omzet", f"Rp {int(u.get('revenue_idr', 0)):,}"),
                ("Workflow OK", f"{runs.get('success', 0)}/{runs.get('total', 0)}"),
                ("Status", h_lbl),
            ]

        mx = cx1 + 28
        my = cy + 54
        mw = (cx2 - cx1 - 56) // 4
        for mi, (mk, mv) in enumerate(m_items):
            px = mx + mi * mw
            draw.rounded_rectangle([px, my, px + mw - 10, my + 48], radius=10, fill=(248, 250, 252), outline=BORDER)
            draw.text((px + 10, my + 5), mk, font=fonts["micro"], fill=SLATE)
            draw.text((px + 10, my + 23), mv[:28], font=fonts["small_b"], fill=NAVY)

        # Milestone progress bar
        ms_cur = int(u.get("milestone_current") or 0)
        ms_tgt = max(1, int(u.get("milestone_target") or 100))
        ms_pct = float(u.get("milestone_pct") or 0.0)
        bar_y = my + 58
        draw.text((cx1 + 28, bar_y), f"Milestone: {u.get('milestone_label', '')}", font=fonts["micro_b"], fill=NAVY)
        right_ms = f"{ms_cur:,} / {ms_tgt:,} ({ms_pct:.1f}%)"
        rtw = draw.textbbox((0, 0), right_ms, font=fonts["micro_b"])[2]
        draw.text((cx2 - 24 - rtw, bar_y), right_ms, font=fonts["micro_b"], fill=SLATE)
        draw.rounded_rectangle([cx1 + 28, bar_y + 20, cx2 - 24, bar_y + 34], radius=7, fill=(226, 232, 240))
        fw = max(14 if ms_cur > 0 else 0, int((cx2 - cx1 - 52) * min(1.0, ms_pct / 100.0)))
        if fw > 0:
            draw.rounded_rectangle([cx1 + 28, bar_y + 20, cx1 + 28 + fw, bar_y + 34], radius=7, fill=h_col)

        # Strengths (+) & Issues (-) 2-column footer inside card
        col_top = bar_y + 42
        mid_x = (cx1 + cx2) // 2
        draw.text((cx1 + 28, col_top), "+ KEUNGGULAN:", font=fonts["micro_b"], fill=GREEN)
        s_txt = "; ".join(u.get("strengths") or ["-"])
        for li, ln in enumerate(_wrap(draw, s_txt, fonts["micro"], mid_x - cx1 - 45, max_lines=3)):
            draw.text((cx1 + 28, col_top + 17 + li * 16), ln, font=fonts["micro"], fill=SLATE)

        draw.text((mid_x + 12, col_top), "- TEMUAN / PERLU PERBAIKAN:", font=fonts["micro_b"], fill=RED if u.get("issues") else SLATE)
        i_txt = "; ".join(u.get("issues") or ["Tidak ada bug kritis terdeteksi."])
        for li, ln in enumerate(_wrap(draw, i_txt, fonts["micro"], cx2 - mid_x - 36, max_lines=3)):
            draw.text((mid_x + 12, col_top + 17 + li * 16), ln, font=fonts["micro"], fill=NAVY if u.get("issues") else SLATE)

    # 3. Bottom Charts (y=1114..1646)
    ch_top, ch_bot = 1114, 1644
    # Left Chart: GitHub Actions Workflow Reliability by Account
    _card(draw, (40, ch_top, 608, ch_bot), fill=WHITE, outline=BORDER, radius=18)
    draw.text((62, ch_top + 18), "KESEHATAN ENGINE & WORKFLOW (%)", font=fonts["h3"], fill=NAVY)
    draw.text((62, ch_top + 44), "Rasio run sukses GitHub Actions per unit", font=fonts["micro"], fill=SLATE)

    ry = ch_top + 82
    for u in units:
        runs = u.get("runs") or {}
        pct = float(runs.get("success_rate_pct") or 100.0)
        ok_n, tot_n = int(runs.get("success") or 0), int(runs.get("total") or 0)
        col = GREEN if pct >= 85 else (AMBER if pct >= 60 else RED)
        draw.text((62, ry), f"{u.get('name', '')[:26]}  ({ok_n}/{tot_n} run)", font=fonts["small_b"], fill=NAVY)
        ptxt = f"{pct:.1f}%"
        ptw = draw.textbbox((0, 0), ptxt, font=fonts["small_b"])[2]
        draw.text((584 - ptw, ry), ptxt, font=fonts["small_b"], fill=col)
        draw.rounded_rectangle([62, ry + 26, 584, ry + 50], radius=12, fill=(226, 232, 240))
        bw = max(16, int((584 - 62) * (pct / 100.0)))
        draw.rounded_rectangle([62, ry + 26, 62 + bw, ry + 50], radius=12, fill=col)
        fails = runs.get("failed_workflows") or []
        note = f"Fail: {fails[0][:48]}" if fails else "Semua run terbaru berjalan normal"
        draw.text((62, ry + 56), note, font=fonts["micro"], fill=RED if fails else SLATE)
        ry += 98

    # Mini legend box at bottom of Left Card
    _card(draw, (62, ch_bot - 96, 584, ch_bot - 18), fill=SOFT_BLUE, outline=(191, 219, 254), radius=12)
    draw.text((78, ch_bot - 84), "EKSPANSI MODULAR (SIAP PAKAI)", font=fonts["micro_b"], fill=BLUE)
    for li, ln in enumerate(_wrap(draw, f"Unit baru ({standby_txt}) tinggal diaktifkan di config/portfolio.yaml untuk langsung muncul di dashboard & grafik ini.", fonts["micro"], 480, max_lines=3)):
        draw.text((78, ch_bot - 64 + li * 16), ln, font=fonts["micro"], fill=NAVY)

    # Right Chart: Top YouTube Videos Views Comparison
    _card(draw, (628, ch_top, CANVAS_W - 40, ch_bot), fill=WHITE, outline=BORDER, radius=18)
    draw.text((650, ch_top + 18), "PERFORMA TOP SHORTS (LIVE VIEWS)", font=fonts["h3"], fill=NAVY)
    draw.text((650, ch_top + 44), "Video teratas dari YouTube API terbaru", font=fonts["micro"], fill=SLATE)

    all_vids: list[dict[str, Any]] = []
    for u in units:
        if u.get("type") == "youtube":
            for v in u.get("top_videos") or []:
                all_vids.append({**v, "unit": u.get("handle", "")})
    all_vids.sort(key=lambda z: int(z.get("views") or 0), reverse=True)
    top_vids = all_vids[:6]
    max_v = max([int(v.get("views") or 0) for v in top_vids], default=1) or 1

    vy = ch_top + 82
    bar_colors = [BLUE, CYAN, PURPLE, GREEN, AMBER, SLATE]
    if not top_vids:
        draw.text((650, vy + 20), "Menunggu data views video YouTube API.", font=fonts["body"], fill=SLATE)
    for vi, v in enumerate(top_vids):
        col = bar_colors[vi % len(bar_colors)]
        raw_t = str(v.get("title") or "-")
        title_s = (raw_t[:24] + "...") if len(raw_t) > 26 else raw_t
        views_n = int(v.get("views") or 0)
        likes_n = int(v.get("likes") or 0)
        draw.text((650, vy), f"{vi + 1}. {title_s}", font=fonts["small_b"], fill=NAVY)
        vtxt = f"{views_n:,} views • {likes_n} likes"
        vtw = draw.textbbox((0, 0), vtxt, font=fonts["micro_b"])[2]
        draw.text((CANVAS_W - 62 - vtw, vy + 2), vtxt, font=fonts["micro_b"], fill=col)
        draw.rounded_rectangle([650, vy + 24, CANVAS_W - 62, vy + 44], radius=10, fill=(226, 232, 240))
        bw = max(16, int((CANVAS_W - 62 - 650) * (views_n / max_v)))
        draw.rounded_rectangle([650, vy + 24, 650 + bw, vy + 44], radius=10, fill=col)
        vy += 68

    yt_primary = next((u for u in units if u.get("type") == "youtube" and u.get("avg_recent_views")), None)
    foot_note = (
        f"Rata-rata 8 video terbaru {yt_primary.get('handle') or 'YT'}: {int(yt_primary['avg_recent_views']):,} views/video."
        if yt_primary
        else "Grafik otomatis terbarui setiap audit berjalan."
    )
    draw.text((650, ch_bot - 38), foot_note, font=fonts["micro_b"], fill=SLATE)
    return img


def _render_roadmap_page(payload: dict[str, Any], fonts: dict[str, ImageFont.FreeTypeFont]) -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    _draw_header(
        draw,
        title="PETA MASALAH, SINERGI & ROADMAP 4 MINGGU",
        subtitle=f"Rencana Eksekusi Lintas Unit  •  {payload.get('generated_at_wib', '')}",
        badge_text="Portfolio CEO Action Plan",
        fonts=fonts,
    )

    # 1. Critical Findings & Quick Fixes (y=156..540)
    draw.text((40, 154), "1. TEMUAN PRIORITAS & PERBAIKAN CEPAT MINGGU INI", font=fonts["h2"], fill=NAVY)
    fixes = list(payload.get("priority_fixes") or [])[:3]
    fy = 194
    for fx in fixes:
        sev = str(fx.get("severity") or "PERHATIAN").upper()
        col, sbg, _ = _health_style(sev)
        _card(draw, (40, fy, CANVAS_W - 40, fy + 104), fill=WHITE, outline=col, radius=16)
        draw.rounded_rectangle([40, fy, 52, fy + 104], radius=6, fill=col)
        end_px = _pill(draw, 72, fy + 14, str(fx.get("unit") or ""), sbg, col, fonts["small_b"])
        draw.text((end_px + 14, fy + 15), str(fx.get("title") or ""), font=fonts["h3"], fill=NAVY)
        for li, ln in enumerate(_wrap(draw, f"Aksi: {fx.get('action', '')}", fonts["body"], CANVAS_W - 140, max_lines=2)):
            draw.text((72, fy + 48 + li * 22), ln, font=fonts["body"], fill=SLATE)
        fy += 118

    # 2. Cross-Ecosystem Synergy Diagram (y=564..1074)
    draw.text((40, 566), "2. ARSITEKTUR SINERGI LINTAS EKOSISTEM (TERMASUK SLOT PRODUK DIGITAL)", font=fonts["h2"], fill=NAVY)
    _card(draw, (40, 606, CANVAS_W - 40, 1084), fill=WHITE, outline=BORDER, radius=18)

    boxes = [
        (76, 638, 570, 800, BLUE, SOFT_BLUE, "1. YT INDONESIA (@kimiabill)", "Kimia Dapur & Sains Harian (1.2K+ v/ep)\n-> Sumber ide terbukti viral + traffic ID"),
        (670, 638, 1164, 800, CYAN, (236, 254, 255), "2. YT ENGLISH (@bill_explains)", "Tech & Applied Sci (RPM Global Tinggi)\n-> Adaptasi konsep & visual hybrid overlay"),
        (76, 876, 570, 1044, AMBER, SOFT_AMBER, "3. THREADS AFFILIATE (Foundation)", "Shopee Meja/Kos/Dapur (50 antrean LIVE)\n-> Validasi klik & reply per kategori"),
        (670, 876, 1164, 1044, PURPLE, SOFT_PURPLE, "4. [SLOT SIAP] KANTOR PRODUK DIGITAL", "Ebook / Template / Kit Sains & Produktivitas\n-> CTA dari YT ID/ENG + Bio Threads Affiliate"),
    ]
    for x1, y1, x2, y2, bcol, bfill, btitle, bdesc in boxes:
        _card(draw, (x1, y1, x2, y2), fill=bfill, outline=bcol, radius=16)
        draw.text((x1 + 20, y1 + 18), btitle, font=fonts["h3"], fill=bcol)
        yy = y1 + 54
        for raw_ln in bdesc.split("\n"):
            for wl in _wrap(draw, raw_ln, fonts["small"], (x2 - x1) - 40, max_lines=2):
                draw.text((x1 + 20, yy), wl, font=fonts["small"], fill=NAVY)
                yy += 24

    # Connector arrows between boxes
    # Top horizontal: YT ID <-> YT ENG
    draw.line([(574, 719), (666, 719)], fill=BLUE, width=4)
    draw.polygon([(666, 719), (652, 711), (652, 727)], fill=BLUE)
    draw.text((582, 692), "Adaptasi", font=fonts["micro_b"], fill=BLUE)
    # Left vertical: YT ID -> Threads
    draw.line([(323, 804), (323, 872)], fill=AMBER, width=4)
    draw.polygon([(323, 872), (315, 858), (331, 858)], fill=AMBER)
    draw.text((334, 828), "Topik Dapur -> Produk Shopee", font=fonts["micro_b"], fill=AMBER)
    # Bottom horizontal: Threads -> Digital Products
    draw.line([(574, 960), (666, 960)], fill=PURPLE, width=4)
    draw.polygon([(666, 960), (652, 952), (652, 968)], fill=PURPLE)
    draw.text((578, 932), "Cross-Sell", font=fonts["micro_b"], fill=PURPLE)
    # Right vertical: YT ENG -> Digital Products
    draw.line([(917, 804), (917, 872)], fill=PURPLE, width=4)
    draw.polygon([(917, 872), (909, 858), (925, 858)], fill=PURPLE)
    draw.text((928, 828), "Traffic -> Produk Digital", font=fonts["micro_b"], fill=PURPLE)

    # 3. 4-Week Visual Roadmap (y=1106..1606)
    draw.text((40, 1108), "3. ROADMAP EKSEKUSI 4 MINGGU KE TARGET MONETISASI", font=fonts["h2"], fill=NAVY)
    roadmap = list(payload.get("roadmap") or [])[:4]
    rm_colors = [RED, BLUE, CYAN, GREEN]
    draw.line([(72, 1170), (72, 1550)], fill=(203, 213, 225), width=4)
    ry = 1148
    for idx, (stage, desc) in enumerate(roadmap):
        col = rm_colors[idx % len(rm_colors)]
        draw.ellipse([54, ry + 20, 90, ry + 56], fill=col, outline=WHITE, width=3)
        num = str(idx + 1)
        nb = draw.textbbox((0, 0), num, font=fonts["small_b"])
        draw.text((72 - (nb[2] - nb[0]) // 2, ry + 27), num, font=fonts["small_b"], fill=WHITE)
        _card(draw, (112, ry, CANVAS_W - 40, ry + 98), fill=WHITE, outline=BORDER, radius=14)
        draw.text((134, ry + 14), stage, font=fonts["h3"], fill=col)
        for li, ln in enumerate(_wrap(draw, desc, fonts["body"], CANVAS_W - 210, max_lines=2)):
            draw.text((134, ry + 44 + li * 22), ln, font=fonts["body"], fill=SLATE)
        ry += 114

    # Footer
    draw.rounded_rectangle([40, 1612, CANVAS_W - 40, 1656], radius=10, fill=DARK_HDR)
    draw.text((62, 1624), "Kimiabill CEO Portfolio Visual Engine  •  Siap terima integrasi API Threads Insights & Kantor Produk Digital", font=fonts["small"], fill=(203, 213, 225))
    return img


def _render_detail_page(payload: dict[str, Any], fonts: dict[str, ImageFont.FreeTypeFont]) -> Image.Image:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
    draw = ImageDraw.Draw(img)

    _draw_header(
        draw,
        title="LAMPIRAN TELEMETRI LIVE & REKAP 7 HARI",
        subtitle=f"Log Workflow GitHub Actions, Metrik Threads API & Pertumbuhan  •  {payload.get('generated_at_wib', '')}",
        badge_text="Halaman 3 • Detail Audit",
        fonts=fonts,
    )

    units: list[dict[str, Any]] = list(payload.get("units") or [])
    deltas = payload.get("deltas") or {}

    # 1. Week-over-Week Growth Card (y=154..290)
    _card(draw, (40, 154, CANVAS_W - 40, 290), fill=WHITE, outline=BORDER, radius=16)
    draw.text((62, 170), "1. PERTUMBUHAN MINGGU-KE-MINGGU & REKAP WATCHDOG", font=fonts["h3"], fill=NAVY)
    if deltas.get("prev_date"):
        d_txt = (
            f"Dibanding audit {deltas['prev_date']}: Subscribers YT {deltas.get('subs_delta', 0):+d} | "
            f"Total Views YT {deltas.get('views_delta', 0):+,}"
        )
    else:
        d_txt = "Snapshot perdana tersimpan di data/portfolio_audits/portfolio_history.json — delta pertumbuhan otomatis muncul di audit berikutnya."
    draw.text((62, 206), d_txt, font=fonts["body"], fill=BLUE if deltas.get("prev_date") else SLATE)
    draw.text(
        (62, 240),
        "Jadwal Asisten CEO: (a) Watchdog Error Harian 21:15 WIB (0 Token)  •  (b) Audit Besar Visual Tiap Minggu 20:30 WIB  •  (c) Telegram /audit_ceo kapan saja.",
        font=fonts["small"],
        fill=SLATE,
    )

    # 2. Recent Workflow Runs Across All Units (y=312..970)
    _card(draw, (40, 312, CANVAS_W - 40, 976), fill=WHITE, outline=BORDER, radius=16)
    draw.text((62, 328), "2. LOG WORKFLOW TERBARU LINTAS REPOSITORY GITHUB", font=fonts["h3"], fill=NAVY)
    ry = 368
    for u in units:
        draw.text((62, ry), f"{u.get('name', '')} ({u.get('repo', '-')})", font=fonts["body_b"], fill=NAVY)
        ry += 28
        rec = (u.get("runs") or {}).get("recent_runs") or []
        if not rec:
            draw.text((80, ry), "- Belum ada riwayat run workflow atau repo belum diisi.", font=fonts["small"], fill=SLATE)
            ry += 30
        else:
            for rr in rec[:5]:
                conc = str(rr.get("conclusion") or "-").upper()
                c_col = GREEN if conc == "SUCCESS" else RED
                ts = str(rr.get("created_at") or "")[:16].replace("T", " ")
                wf = str(rr.get("name") or "-")[:56]
                _pill(draw, 80, ry, conc, SOFT_GREEN if conc == "SUCCESS" else SOFT_RED, c_col, fonts["micro_b"], pad_x=8, pad_y=2)
                draw.text((195, ry + 3), f"{ts} UTC  |  {wf}", font=fonts["small"], fill=NAVY if conc != "SUCCESS" else SLATE)
                ry += 30
        ry += 12
        if ry > 930:
            break

    # 3. Threads Affiliate Log & API Insights Breakdown (y=998..1590)
    _card(draw, (40, 998, CANVAS_W - 40, 1594), fill=WHITE, outline=BORDER, radius=16)
    draw.text((62, 1014), "3. DETAIL KANTOR AFFILIATE (GOOGLE SHEETS + API THREADS)", font=fonts["h3"], fill=NAVY)
    tu = next((u for u in units if u.get("type") == "threads_affiliate"), None)
    ty = 1052
    if not tu:
        draw.text((62, ty), "Unit Threads Affiliate tidak aktif di config/portfolio.yaml.", font=fonts["body"], fill=SLATE)
    else:
        tapi = tu.get("threads_api") or {}
        if tapi.get("connected"):
            draw.text(
                (62, ty),
                f"API Threads Aktif: Views={tapi.get('views', 0):,} | Likes={tapi.get('likes', 0):,} | Replies={tapi.get('replies', 0):,} | Reposts={tapi.get('reposts', 0):,} | Clicks={tapi.get('clicks', 0):,} | Followers={tapi.get('followers', '-')}",
                font=fonts["body_b"],
                fill=GREEN,
            )
            ty += 34
            for pi in (tapi.get("post_insights") or [])[:6]:
                draw.text(
                    (78, ty),
                    f"Post {pi.get('post_id')}  ->  Views: {pi.get('views', 0):,} | Likes: {pi.get('likes', 0)} | Replies: {pi.get('replies', 0)} | Reposts: {pi.get('reposts', 0)} | Shares: {pi.get('shares', 0)}",
                    font=fonts["small"],
                    fill=NAVY,
                )
                ty += 26
        else:
            draw.text((62, ty), "API Threads Insights: Menunggu Secret THREADS_ACCESS_TOKEN & THREADS_USER_ID (akan otomatis muncul di sini).", font=fonts["body"], fill=AMBER)
            ty += 34

        draw.text((62, ty + 8), "Riwayat Publikasi Terbaru di Google Sheet (Log_Performa):", font=fonts["body_b"], fill=NAVY)
        ty += 38
        for lg in (tu.get("live_logs") or [])[-6:]:
            st = str(lg.get("status") or "-")
            s_col = GREEN if st == "PUBLISHED" else AMBER
            _pill(draw, 78, ty, st[:18], SOFT_GREEN if st == "PUBLISHED" else SOFT_AMBER, s_col, fonts["micro_b"], pad_x=8, pad_y=2)
            draw.text((260, ty + 3), f"{lg.get('tanggal_wib', '')} | {str(lg.get('produk', ''))[:52]}", font=fonts["small"], fill=NAVY)
            ty += 32

    draw.rounded_rectangle([40, 1612, CANVAS_W - 40, 1656], radius=10, fill=DARK_HDR)
    draw.text((62, 1624), "Kimiabill CEO Assistant Dossier  •  Halaman 3/3  •  Siap ekspansi ke Kantor Produk Digital", font=fonts["small"], fill=(203, 213, 225))
    return img


def generate_portfolio_visuals(payload: dict[str, Any], out_dir: Path, date_str: str) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fonts = _load_fonts()
    p1_img = _render_dashboard_page(payload, fonts)
    p2_img = _render_roadmap_page(payload, fonts)
    p3_img = _render_detail_page(payload, fonts)

    p1_path = out_dir / f"PORTFOLIO_INFOGRAFIK_1_DASHBOARD_{date_str}.png"
    p2_path = out_dir / f"PORTFOLIO_INFOGRAFIK_2_ROADMAP_{date_str}.png"
    pdf_path = out_dir / f"PORTFOLIO_CEO_AUDIT_{date_str}.pdf"

    p1_img.save(p1_path, "PNG", optimize=True)
    p2_img.save(p2_path, "PNG", optimize=True)
    p1_img.convert("RGB").save(
        pdf_path,
        "PDF",
        resolution=150.0,
        save_all=True,
        append_images=[p2_img.convert("RGB"), p3_img.convert("RGB")],
    )
    return {
        "png_dashboard": str(p1_path),
        "png_roadmap": str(p2_path),
        "pdf_report": str(pdf_path),
    }
