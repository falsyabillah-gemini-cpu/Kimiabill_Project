"""Hybrid 30fps Mechanistic Comic-Science Animation Engine for Kimiabill_videos.

Renders crisp flat-vector comic-science animations (rounded cards with bold dark outlines,
step-by-step molecular/physical simulations, animated counters/bars, diagonal wipe transitions,
TERJAWAB! outro, and optional animated Opsi B Duo mascots) directly as 30fps RGBA frames
piped into FFmpeg and composited cleanly over REAL video footage.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.state import RenderPlan, SubtitleChunk, TimelineSegment
from src.tools.video_renderer import (
    FONT_BOLD_CANDIDATES,
    FONT_REG_CANDIDATES,
    _build_contact_sheet,
    _mix_audio,
    _pick_font,
    _write_ass_subtitles,
)


# Comic-science palette inspired by reference style
INK = (18, 22, 34, 255)
SHADOW = (10, 14, 24, 140)
CARD_BG = (252, 253, 255, 242)
CARD_DARK = (22, 26, 48, 244)
WHITE = (255, 255, 255, 255)
YELLOW = (255, 214, 51, 255)
GOLD = (255, 196, 28, 255)
CYAN = (48, 214, 255, 255)
BLUE = (56, 132, 255, 255)
PURPLE = (128, 72, 232, 255)
ORANGE = (255, 132, 42, 255)
RED = (238, 66, 92, 255)
GREEN = (36, 196, 124, 255)
RUST_LIGHT = (224, 114, 42, 255)
RUST_DARK = (158, 64, 24, 255)
IRON_GRAY = (146, 158, 174, 255)
IRON_DARK = (92, 104, 120, 255)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def ease_out_back(t: float) -> float:
    t = clamp(t)
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((t - 1.0) ** 3) + c1 * ((t - 1.0) ** 2)


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int] = WHITE,
    outline_color: tuple[int, int, int, int] = INK,
    stroke_w: int = 5,
    anchor: str = "mm",
) -> None:
    draw.text(
        pos,
        text,
        font=font,
        fill=fill,
        anchor=anchor,
        stroke_width=stroke_w,
        stroke_fill=outline_color,
    )


def draw_comic_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int, int] = CARD_BG,
    outline: tuple[int, int, int, int] = INK,
    radius: int = 28,
    border_w: int = 6,
    shadow_offset: int = 10,
) -> None:
    x0, y0, x1, y1 = box
    if shadow_offset > 0:
        draw.rounded_rectangle(
            [x0 + shadow_offset, y0 + shadow_offset, x1 + shadow_offset, y1 + shadow_offset],
            radius=radius,
            fill=SHADOW,
        )
    draw.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=radius,
        fill=fill,
        outline=outline,
        width=border_w,
    )


def draw_brand_pill(draw: ImageDraw.ImageDraw, fonts: dict) -> None:
    """Top-left @kimiabill brand badge with glowing Erlenmeyer flask icon."""
    x0, y0, x1, y1 = 48, 68, 355, 144
    draw.rounded_rectangle([x0 + 4, y0 + 6, x1 + 4, y1 + 6], radius=38, fill=(0, 0, 0, 120))
    draw.rounded_rectangle([x0, y0, x1, y1], radius=38, fill=(20, 26, 48, 225), outline=WHITE, width=4)
    # Mini Erlenmeyer flask icon
    fx, fy = x0 + 44, (y0 + y1) // 2
    draw.rectangle([fx - 7, fy - 22, fx + 7, fy - 8], fill=WHITE, outline=INK, width=2)
    draw.polygon(
        [(fx - 7, fy - 8), (fx + 7, fy - 8), (fx + 20, fy + 18), (fx - 20, fy + 18)],
        fill=WHITE,
        outline=INK,
    )
    draw.polygon(
        [(fx - 11, fy + 2), (fx + 11, fy + 2), (fx + 17, fy + 16), (fx - 17, fy + 16)],
        fill=CYAN,
    )
    draw.line([(fx - 10, fy - 22), (fx + 10, fy - 22)], fill=INK, width=3)
    draw.text((x0 + 84, (y0 + y1) // 2), "@kimiabill", font=fonts["brand"], fill=WHITE, anchor="lm")


def draw_header_pill(
    draw: ImageDraw.ImageDraw,
    text: str,
    bg_color: tuple[int, int, int, int],
    fonts: dict,
    local_t: float,
    y_center: int = 225,
) -> None:
    scale = ease_out_back(local_t / 0.35)
    if scale <= 0.05:
        return
    bbox = draw.textbbox((0, 0), text, font=fonts["header"])
    tw = bbox[2] - bbox[0]
    pw = int((tw + 84) * min(scale, 1.05))
    ph = int(76 * min(scale, 1.05))
    x0 = 540 - pw // 2
    y0 = y_center - ph // 2
    draw_comic_box(draw, (x0, y0, x0 + pw, y0 + ph), fill=bg_color, radius=24, border_w=5, shadow_offset=8)
    draw_outlined_text(draw, (540, y_center), text, fonts["header"], fill=WHITE, stroke_w=4)


def draw_takeaway_card(
    draw: ImageDraw.ImageDraw,
    top_label: str,
    big_word: str,
    big_color: tuple[int, int, int, int],
    sub_label: str,
    fonts: dict,
    local_t: float,
    appear_at: float,
    center_x: int = 540,
    y0: int = 865,
    card_w: int = 620,
) -> None:
    if local_t < appear_at:
        return
    p = ease_out_back((local_t - appear_at) / 0.35)
    w = int(card_w * min(p, 1.03))
    h = int(235 * min(p, 1.03))
    x0 = center_x - w // 2
    yc = y0 + 118
    top_y = yc - h // 2
    draw_comic_box(draw, (x0, top_y, x0 + w, top_y + h), fill=CARD_BG, radius=26, border_w=5, shadow_offset=8)
    if p > 0.65:
        draw.text((center_x, top_y + 44), top_label, font=fonts["small_bold"], fill=INK, anchor="mm")
        draw_outlined_text(draw, (center_x, top_y + 118), big_word, fonts["big_kw"], fill=big_color, stroke_w=5)
        draw.text((center_x, top_y + 188), sub_label, font=fonts["tiny_bold"], fill=(60, 68, 86, 255), anchor="mm")


def draw_diagonal_wipe(draw: ImageDraw.ImageDraw, progress: float, w: int = 1080, h: int = 1920) -> None:
    """Multi-color diagonal comic wipe transition matching the reference videos."""
    if progress <= 0.0 or progress >= 1.0:
        return
    # Sweep from right (+w+600) to left (-700)
    sweep_x = int((w + 700) - progress * (w + 1500))
    skew = 320
    bands = [
        (0, 120, BLUE),
        (120, 210, YELLOW),
        (210, 310, RED),
        (310, 520, BLUE),
    ]
    for dx0, dx1, col in bands:
        pts = [
            (sweep_x + dx0 + skew, 0),
            (sweep_x + dx1 + skew, 0),
            (sweep_x + dx1 - skew, h),
            (sweep_x + dx0 - skew, h),
        ]
        draw.polygon(pts, fill=col)


def draw_sunburst_bg(w: int, h: int, angle_offset: float = 0.0) -> Image.Image:
    """Warm golden comic sunburst background for the TERJAWAB! outro."""
    img = Image.new("RGBA", (w, h), (255, 194, 34, 255))
    draw = ImageDraw.Draw(img)
    cx, cy = w // 2, int(h * 0.45)
    r = int(math.hypot(w, h))
    num_rays = 20
    for i in range(num_rays):
        a0 = (i * 2 * math.pi / num_rays) + angle_offset
        a1 = a0 + (math.pi / num_rays)
        pts = [
            (cx, cy),
            (cx + int(r * math.cos(a0)), cy + int(r * math.sin(a0))),
            (cx + int(r * math.cos(a1)), cy + int(r * math.sin(a1))),
        ]
        draw.polygon(pts, fill=(255, 224, 92, 235))
    return img


class MascotLibrary:
    def __init__(self, mascots_dir: Path) -> None:
        self.sprites: dict[str, Image.Image] = {}
        for name in (
            "male_curious",
            "female_curious",
            "male_explain",
            "female_explain",
            "male_celebrate",
            "female_celebrate",
        ):
            p = mascots_dir / f"{name}.png"
            if p.exists():
                self.sprites[name] = Image.open(p).convert("RGBA")

    def paste(
        self,
        canvas: Image.Image,
        key: str,
        center_x: int,
        bottom_y: int,
        target_h: int,
        local_t: float,
        appear_at: float = 0.0,
        flip_x: bool = False,
    ) -> None:
        if key not in self.sprites or local_t < appear_at:
            return
        sp = self.sprites[key]
        p = ease_out_back((local_t - appear_at) / 0.40)
        bob = int(math.sin((local_t - appear_at) * 4.2) * 8)
        h = max(20, int(target_h * min(p, 1.04)))
        w = max(20, int(sp.width * (h / sp.height)))
        resized = sp.resize((w, h), Image.Resampling.LANCZOS)
        if flip_x:
            resized = resized.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        x = center_x - w // 2
        y = bottom_y - h + bob
        canvas.alpha_composite(resized, (x, y))


# === SCENE-SPECIFIC MECHANISTIC ANIMATIONS (Korosi Episode 1) ===

def draw_scene1_hook(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    # Subtle top/bottom contrast scrim so real glowing iron footage shines cleanly in center
    for y in range(0, 520, 4):
        a = int(145 * (1.0 - y / 520) ** 1.4)
        draw.rectangle([0, y, 1080, y + 4], fill=(14, 16, 36, a))

    # Punchy comic hook title popping in
    if t >= 0.05:
        p1 = ease_out_back(t / 0.32)
        y1 = int(250 - (1.0 - min(p1, 1.0)) * 40)
        draw_outlined_text(draw, (540, y1), "KENAPA BESI", fonts["hook_sub"], fill=WHITE, stroke_w=9)
    if t >= 0.25:
        p2 = ease_out_back((t - 0.25) / 0.35)
        y2 = int(355 - (1.0 - min(p2, 1.0)) * 35)
        draw_outlined_text(draw, (540, y2), "BISA BERKARAT?!", fonts["hook_main"], fill=YELLOW, stroke_w=11)
    if t >= 0.65:
        p3 = ease_out_back((t - 0.65) / 0.35)
        y3 = int(450 + (1.0 - min(p3, 1.0)) * 20)
        draw_comic_box(draw, (185, y3 - 34, 895, y3 + 34), fill=ORANGE, radius=32, border_w=5, shadow_offset=6)
        draw_outlined_text(draw, (540, y3), "TERNYATA LAGI TERBAKAR PELAN!", fonts["small_bold"], fill=WHITE, stroke_w=4)

    # Bobbing comic '?' and '!' icons
    bob1 = int(math.sin(t * 5.0) * 14)
    bob2 = int(math.cos(t * 4.8) * 14)
    draw_outlined_text(draw, (135, 640 + bob1), "?", fonts["giant_sym"], fill=RED, stroke_w=10)
    draw_outlined_text(draw, (945, 630 + bob2), "!", fonts["giant_sym"], fill=CYAN, stroke_w=10)

    if mascots:
        mascots.paste(canvas, "male_curious", center_x=235, bottom_y=1315, target_h=430, local_t=t, appear_at=0.15)
        mascots.paste(canvas, "female_curious", center_x=845, bottom_y=1315, target_h=420, local_t=t, appear_at=0.28)


def draw_scene2_oxidation_compare(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "OKSIDASI = TERBAKAR LAMBAT", ORANGE, fonts, t)

    # Mechanistic card: Fast Fire Oxidation vs Slow Rust Oxidation
    bx0, by0, bx1, by1 = 68, 300, 1012, 815
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)
    # Center divider
    draw.line([(540, by0 + 26), (540, by1 - 26)], fill=(210, 218, 232, 255), width=4)

    # Left: API (Oksidasi Cepat)
    draw.rounded_rectangle([110, 330, 500, 388], radius=18, fill=RED, outline=INK, width=4)
    draw_outlined_text(draw, (305, 359), "API (CEPAT)", fonts["small_bold"], fill=WHITE, stroke_w=3)
    # Animated flame pulsing
    pulse = 1.0 + 0.12 * math.sin(t * 14.0)
    fx, fy = 305, 575
    fh = int(145 * pulse)
    fw = int(78 * pulse)
    draw.polygon(
        [(fx, fy - fh), (fx + fw, fy + 10), (fx, fy + 55), (fx - fw, fy + 10)],
        fill=ORANGE,
        outline=INK,
    )
    draw.polygon(
        [(fx, fy - int(fh * 0.62)), (fx + int(fw * 0.55), fy + 8), (fx, fy + 36), (fx - int(fw * 0.55), fy + 8)],
        fill=YELLOW,
    )
    # Sparks orbiting flame
    for i in range(5):
        ang = t * 5.0 + i * 1.25
        sx = fx + int(math.cos(ang) * (75 + (i % 2) * 28))
        sy = fy - 45 - int(((t * 90 + i * 35) % 120))
        draw.ellipse([sx - 6, sy - 6, sx + 6, sy + 6], fill=YELLOW, outline=INK, width=2)
    draw.text((305, 675), "Fe + O₂ + Panas", font=fonts["tiny_bold"], fill=INK, anchor="mm")
    draw.rounded_rectangle([140, 712, 470, 766], radius=16, fill=(255, 236, 236, 255), outline=RED, width=3)
    draw.text((305, 739), "Hitungan DETIK", font=fonts["tiny_bold"], fill=RED, anchor="mm")

    # Right: KARAT (Oksidasi Lambat)
    draw.rounded_rectangle([580, 330, 970, 388], radius=18, fill=RUST_LIGHT, outline=INK, width=4)
    draw_outlined_text(draw, (775, 359), "KARAT (LAMBAT)", fonts["small_bold"], fill=WHITE, stroke_w=3)
    # Iron bar slowly getting covered by creeping rust spots
    draw.rounded_rectangle([620, 515, 930, 595], radius=16, fill=IRON_GRAY, outline=INK, width=5)
    progress = clamp(t / max(dur * 0.85, 1.0))
    rust_w = int(300 * progress)
    if rust_w > 12:
        draw.rounded_rectangle([624, 519, 624 + rust_w, 591], radius=12, fill=RUST_LIGHT)
        # Rust speckles
        for k in range(14):
            rx = 638 + int((k * 23) % max(rust_w - 10, 1))
            ry = 532 + (k * 17) % 44
            if rx < 620 + rust_w:
                draw.ellipse([rx - 7, ry - 7, rx + 7, ry + 7], fill=RUST_DARK)
    # Gentle floating O2 bubbles approaching iron bar
    for j in range(3):
        oy = 430 + int(((t * 32 + j * 28) % 75))
        ox = 675 + j * 95
        draw.ellipse([ox - 20, oy - 20, ox + 20, oy + 20], fill=CYAN, outline=INK, width=3)
        draw.text((ox, oy), "O₂", font=fonts["micro_bold"], fill=INK, anchor="mm")

    draw.text((775, 675), "Fe + O₂ + H₂O (Tanpa Api)", font=fonts["tiny_bold"], fill=INK, anchor="mm")
    draw.rounded_rectangle([610, 712, 940, 766], radius=16, fill=(255, 243, 228, 255), outline=RUST_LIGHT, width=3)
    draw.text((775, 739), "Hitungan HARI/BULAN", font=fonts["tiny_bold"], fill=RUST_DARK, anchor="mm")

    # Key takeaway card (shifts slightly left if mascot is on right)
    tk_x = 380 if mascots else 540
    tk_w = 570 if mascots else 660
    draw_takeaway_card(
        draw,
        "Keduanya sama-sama",
        "OKSIDASI!",
        ORANGE,
        "Besi bereaksi dengan oksigen",
        fonts,
        t,
        appear_at=2.6,
        center_x=tk_x,
        y0=865,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "male_explain", center_x=865, bottom_y=1290, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene3_atomic_mechanism(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "1: REAKSI KIMIA KARAT", BLUE, fonts, t)

    # Atomic Simulation Box
    bx0, by0, bx1, by1 = 68, 300, 1012, 835
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)

    # Bottom iron lattice (2 rows of Fe atoms)
    progress = clamp(t / max(dur * 0.85, 1.0))
    rust_count = int(progress * 8)

    draw.rectangle([94, 650, 986, 765], fill=(224, 230, 238, 255), outline=INK, width=4)
    # Row 2 (bottom solid iron)
    for i in range(8):
        ax = 152 + i * 110
        ay = 725
        draw.ellipse([ax - 28, ay - 28, ax + 28, ay + 28], fill=IRON_GRAY, outline=INK, width=4)
        draw.text((ax, ay), "Fe", font=fonts["micro_bold"], fill=WHITE, anchor="mm")

    # Row 1 (surface Fe atoms gradually oxidizing into orange rust Fe2O3!)
    for i in range(8):
        ax = 152 + i * 110
        ay = 648
        is_rusted = i < rust_count
        col = RUST_LIGHT if is_rusted else IRON_GRAY
        label = "Fe³⁺" if is_rusted else "Fe"
        draw.ellipse([ax - 30, ay - 30, ax + 30, ay + 30], fill=col, outline=INK, width=4)
        draw.text((ax, ay), label, font=fonts["micro_bold"], fill=WHITE, anchor="mm")

        # Animated yellow electrons (e-) flying upward as each atom oxidizes!
        atom_t = progress * 8 - i
        if 0.0 < atom_t < 2.2:
            ey = int(ay - 40 - atom_t * 78)
            ex = ax + int(math.sin(atom_t * 6) * 18)
            if ey > by0 + 30:
                draw.ellipse([ex - 20, ey - 20, ex + 20, ey + 20], fill=YELLOW, outline=INK, width=3)
                draw.text((ex, ey), "e⁻", font=fonts["micro_bold"], fill=INK, anchor="mm")

    # Incoming H2O (cyan) and O2 (red) molecules landing onto the iron surface
    for m in range(4):
        cyc = (t * 0.75 + m * 0.27) % 1.0
        mx = 195 + m * 225
        my = int(345 + ease_out_cubic(cyc) * 235)
        if m % 2 == 0:
            draw.ellipse([mx - 34, my - 28, mx + 34, my + 28], fill=CYAN, outline=INK, width=4)
            draw.text((mx, my), "H₂O", font=fonts["micro_bold"], fill=INK, anchor="mm")
        else:
            draw.ellipse([mx - 32, my - 28, mx + 32, my + 28], fill=RED, outline=INK, width=4)
            draw.text((mx, my), "O₂", font=fonts["micro_bold"], fill=WHITE, anchor="mm")

    # Live counter bar at bottom of diagram box (just like reference virus_dubbed.mp4!)
    draw.text(
        (540, 800),
        f"Atom berubah jadi oksida besi (karat): {rust_count} / 8",
        font=fonts["tiny_bold"],
        fill=RUST_DARK,
        anchor="mm",
    )

    # Equation & takeaway card
    tk_x = 700 if mascots else 540
    tk_w = 570 if mascots else 720
    draw_takeaway_card(
        draw,
        "Rumus Pembentukan Karat",
        "Fe₂O₃·nH₂O",
        RUST_LIGHT,
        "Fe + O₂ + H₂O → Oksida Besi Oranye",
        fonts,
        t,
        appear_at=1.6,
        center_x=tk_x,
        y0=875,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "female_explain", center_x=215, bottom_y=1295, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene4_porous_rust(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "2: KARAT ITU BERPORI!", PURPLE, fonts, t)

    bx0, by0, bx1, by1 = 68, 300, 1012, 825
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)

    # Animated cross-section: porous rust eating deeper into iron core!
    progress = clamp(t / max(dur * 0.85, 1.0))
    rust_depth = int(110 + progress * 115)  # Rust expands downward over time!

    # Fresh iron core at bottom
    draw.rectangle([120, 520, 960, 765], fill=IRON_GRAY, outline=INK, width=5)
    draw.text((540, 720), "LAPISAN BESI SEHAT (TERUS TERGEROGOTI)", font=fonts["tiny_bold"], fill=WHITE, anchor="mm")

    # Expanding porous rust crust
    draw.rectangle([120, 445, 960, 445 + rust_depth], fill=RUST_LIGHT, outline=INK, width=5)
    # Visible dark pores / cracks inside the rust crust!
    pores = [(240, 465), (420, 480), (630, 460), (810, 475), (330, 525), (540, 515), (735, 530)]
    for px, py in pores:
        if py < 435 + rust_depth:
            draw.polygon(
                [(px - 18, py), (px + 18, py), (px + 8, py + 48), (px - 10, py + 48)],
                fill=(76, 36, 18, 255),
            )
    draw_outlined_text(draw, (540, 485), "Lapisan Karat Rapuh & Retak", fonts["small_bold"], fill=WHITE, stroke_w=4)

    # Animated H2O & O2 arrows penetrating straight through the cracks!
    for idx, ax in enumerate((240, 420, 630, 810)):
        cyc = (t * 0.85 + idx * 0.22) % 1.0
        ay = int(335 + cyc * (rust_depth + 115))
        col = CYAN if idx % 2 == 0 else RED
        lbl = "H₂O" if idx % 2 == 0 else "O₂"
        draw.line([(ax, ay - 26), (ax, ay + 10)], fill=col, width=7)
        draw.polygon([(ax, ay + 24), (ax - 14, ay + 4), (ax + 14, ay + 4)], fill=col, outline=INK)
        draw.ellipse([ax - 26, ay - 52, ax + 26, ay - 14], fill=col, outline=INK, width=3)
        draw.text((ax, ay - 33), lbl, font=fonts["micro_bold"], fill=INK if col == CYAN else WHITE, anchor="mm")

    tk_x = 370 if mascots else 540
    tk_w = 595 if mascots else 680
    draw_takeaway_card(
        draw,
        "Karat besi sifatnya",
        "BERPORI!",
        PURPLE,
        "Air & O₂ terus tembus ke dalam",
        fonts,
        t,
        appear_at=1.6,
        center_x=tk_x,
        y0=870,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "male_curious", center_x=875, bottom_y=1295, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene5_saltwater_race(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    draw_header_pill(draw, "3: EFEK PANTAI & AIR GARAM", RED, fonts, t)

    bx0, by0, bx1, by1 = 68, 300, 1012, 825
    draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)
    draw.text((540, 350), "Kecepatan Munculnya Karat pada Besi", font=fonts["small_bold"], fill=INK, anchor="mm")

    progress = ease_out_cubic(clamp(t / max(dur * 0.72, 1.0)))

    # Bar 1: Air Biasa (slow)
    draw.text((130, 445), "Air Biasa (Tawar)", font=fonts["small_bold"], fill=BLUE, anchor="lm")
    draw.rounded_rectangle([130, 478, 945, 545], radius=22, fill=(230, 236, 245, 255), outline=INK, width=4)
    w1 = max(24, int(805 * 0.28 * progress))
    draw.rounded_rectangle([135, 483, 135 + w1, 540], radius=18, fill=BLUE)
    draw.text((155 + w1, 512), f"{int(28 * progress)}%", font=fonts["small_bold"], fill=INK, anchor="lm")

    # Bar 2: Air Garam / Pantai (FAST + electric sparks!)
    draw.text((130, 610), "Air Garam Pantai (Na⁺ Cl⁻)", font=fonts["small_bold"], fill=RED, anchor="lm")
    draw.rounded_rectangle([130, 643, 945, 718], radius=22, fill=(252, 232, 232, 255), outline=INK, width=4)
    w2 = max(24, int(805 * 0.94 * progress))
    draw.rounded_rectangle([135, 648, 135 + w2, 713], radius=18, fill=RED)
    draw_outlined_text(draw, (min(885, 135 + w2 - 55), 680), f"{int(94 * progress)}%", fonts["small_bold"], fill=YELLOW, stroke_w=4)

    # Animated electric bolt sparks along the salt-water bar
    for s in range(4):
        sx = 180 + int(((t * 340 + s * 170) % max(w2 - 40, 40)))
        sy = 680 + int(math.sin(t * 20 + s) * 14)
        draw_outlined_text(draw, (sx, sy), "⚡", fonts["small_bold"], fill=YELLOW, stroke_w=2)

    draw.text((540, 772), "Ion garam (Na⁺, Cl⁻) = jembatan aliran listrik mikro!", font=fonts["tiny_bold"], fill=(60, 68, 86, 255), anchor="mm")

    tk_x = 710 if mascots else 540
    tk_w = 595 if mascots else 680
    draw_takeaway_card(
        draw,
        "Dekat laut, karat jauh lebih",
        "CEPAT!",
        RED,
        "Garam percepat transfer elektron",
        fonts,
        t,
        appear_at=1.8,
        center_x=tk_x,
        y0=870,
        card_w=tk_w,
    )
    if mascots:
        mascots.paste(canvas, "female_explain", center_x=205, bottom_y=1295, target_h=430, local_t=t, appear_at=0.25, flip_x=True)


def draw_scene6_solution_and_outro(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    dur: float,
    fonts: dict,
    mascots: Optional[MascotLibrary],
) -> None:
    outro_start = max(3.6, dur - 3.6)

    if t < outro_start:
        # Part 1: Real footage + Animated Protective Coating Shield Diagram
        draw_header_pill(draw, "SOLUSI: BLOKIR KONTAK!", GREEN, fonts, t)
        bx0, by0, bx1, by1 = 68, 300, 1012, 815
        draw_comic_box(draw, (bx0, by0, bx1, by1), fill=CARD_BG, radius=30, border_w=6, shadow_offset=10)

        # Iron core safely underneath
        draw.rounded_rectangle([120, 625, 960, 745], radius=18, fill=IRON_GRAY, outline=INK, width=5)
        draw_outlined_text(draw, (540, 685), "BESI AMAN & BEBAS KARAT ✔", fonts["small_bold"], fill=WHITE, stroke_w=4)

        # Protective paint / zinc galvanize shield layer sweeping across
        shield_p = ease_out_cubic(clamp(t / 1.0))
        sw = int(840 * shield_p)
        if sw > 20:
            draw.rounded_rectangle([120, 540, 120 + sw, 625], radius=16, fill=GREEN, outline=INK, width=5)
            if shield_p > 0.55:
                draw_outlined_text(draw, (540, 582), "LAPISAN CAT / SENG (GALVANIS)", fonts["small_bold"], fill=WHITE, stroke_w=4)

        # Incoming H2O & O2 particles bouncing OFF the shield!
        for idx, mx in enumerate((250, 440, 640, 830)):
            cyc = (t * 1.15 + idx * 0.25) % 1.0
            # Down from 345 to 500 then bounce back UP!
            if cyc < 0.5:
                my = int(350 + (cyc * 2.0) * 155)
            else:
                my = int(505 - ((cyc - 0.5) * 2.0) * 140)
            col = CYAN if idx % 2 == 0 else RED
            lbl = "H₂O" if idx % 2 == 0 else "O₂"
            draw.ellipse([mx - 28, my - 24, mx + 28, my + 24], fill=col, outline=INK, width=4)
            draw.text((mx, my), lbl, font=fonts["micro_bold"], fill=INK if col == CYAN else WHITE, anchor="mm")
            if 0.42 < cyc < 0.62:
                draw_outlined_text(draw, (mx, 520), "✖", fonts["small_bold"], fill=RED, stroke_w=3)

        # Clean checklist pill below
        ck_x = 370 if mascots else 540
        ck_w = 595 if mascots else 720
        x0 = ck_x - ck_w // 2
        draw_comic_box(draw, (x0, 880, x0 + ck_w, 1020), fill=CARD_BG, radius=24, border_w=5, shadow_offset=8)
        draw.ellipse([x0 + 26, 922, x0 + 82, 978], fill=GREEN, outline=INK, width=4)
        draw.text((x0 + 54, 950), "✔", font=fonts["small_bold"], fill=WHITE, anchor="mm")
        draw.text((x0 + 106, 932), "Cat atau Lapisan Seng", font=fonts["small_bold"], fill=INK, anchor="lm")
        draw.text((x0 + 106, 972), "Cegah air & O₂ menyentuh besi", font=fonts["tiny_bold"], fill=(70, 78, 96, 255), anchor="lm")

        if mascots:
            mascots.paste(canvas, "male_explain", center_x=875, bottom_y=1295, target_h=430, local_t=t, appear_at=0.15, flip_x=True)

        # Diagonal wipe right into outro
        if t > outro_start - 0.35:
            draw_diagonal_wipe(draw, (t - (outro_start - 0.35)) / 0.35)
    else:
        # Part 2: Signature TERJAWAB! Outro (clean sunburst + card + @kimiabill + source)
        ot = t - outro_start
        burst = draw_sunburst_bg(1080, 1920, angle_offset=ot * 0.25)
        canvas.alpha_composite(burst, (0, 0))
        draw = ImageDraw.Draw(canvas)

        y_shift = 0 if mascots else 185

        # Tilted TERJAWAB! comic card
        p = ease_out_back(ot / 0.35)
        card = Image.new("RGBA", (820, 260), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        draw_comic_box(cd, (20, 20, 790, 230), fill=WHITE, outline=BLUE, radius=32, border_w=10, shadow_offset=12)
        draw_outlined_text(cd, (405, 125), "TERJAWAB!", fonts["terjawab"], fill=BLUE, outline_color=WHITE, stroke_w=0)
        rot = card.rotate(3.5, resample=Image.Resampling.BICUBIC, expand=True)
        rw = max(50, int(rot.width * min(p, 1.03)))
        rh = max(50, int(rot.height * min(p, 1.03)))
        rot_s = rot.resize((rw, rh), Image.Resampling.LANCZOS)
        canvas.alpha_composite(rot_s, (540 - rw // 2, 360 + y_shift - rh // 2))

        draw = ImageDraw.Draw(canvas)
        draw_outlined_text(
            draw,
            (540, 565 + y_shift),
            "Karat = Oksidasi lambat besi + air + O₂",
            fonts["summary"],
            fill=WHITE,
            stroke_w=6,
        )

        # Follow @kimiabill button
        if ot >= 0.45:
            fp = ease_out_back((ot - 0.45) / 0.35)
            bw = int(600 * min(fp, 1.03))
            bh = int(96 * min(fp, 1.03))
            bx = 540 - bw // 2
            by = 665 + y_shift - bh // 2
            draw_comic_box(draw, (bx, by, bx + bw, by + bh), fill=BLUE, outline=WHITE, radius=48, border_w=6, shadow_offset=8)
            draw_outlined_text(draw, (540, 665 + y_shift), "Follow @kimiabill", fonts["header"], fill=WHITE, stroke_w=4)

        if mascots:
            mascots.paste(canvas, "male_celebrate", center_x=320, bottom_y=1295, target_h=455, local_t=ot, appear_at=0.10)
            mascots.paste(canvas, "female_celebrate", center_x=760, bottom_y=1295, target_h=445, local_t=ot, appear_at=0.18)

        # Credible scientific source note (matching reference style)
        draw.text(
            (540, 1690),
            "Sumber: Callister & Rethwisch, Materials Science and Engineering",
            font=fonts["micro_bold"],
            fill=(50, 34, 8, 230),
            anchor="mm",
        )


def _load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    return {
        "brand": _pick_font(FONT_BOLD_CANDIDATES, 34),
        "header": _pick_font(FONT_BOLD_CANDIDATES, 40),
        "hook_sub": _pick_font(FONT_BOLD_CANDIDATES, 76),
        "hook_main": _pick_font(FONT_BOLD_CANDIDATES, 92),
        "giant_sym": _pick_font(FONT_BOLD_CANDIDATES, 145),
        "big_kw": _pick_font(FONT_BOLD_CANDIDATES, 66),
        "terjawab": _pick_font(FONT_BOLD_CANDIDATES, 108),
        "summary": _pick_font(FONT_BOLD_CANDIDATES, 42),
        "small_bold": _pick_font(FONT_BOLD_CANDIDATES, 32),
        "tiny_bold": _pick_font(FONT_BOLD_CANDIDATES, 24),
        "micro_bold": _pick_font(FONT_BOLD_CANDIDATES, 22),
    }


def _prepare_clean_real_footage_base(
    plan: RenderPlan,
    out_stitched_mp4: Path,
    work_dir: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Path:
    """Stitch clean 1080x1920 real footage background clips (NO static badges — animation overlay handles all visuals!)."""
    seg_dir = work_dir / "hybrid_base_segs"
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg_files: list[Path] = []
    for seg in plan.segments:
        out_seg = seg_dir / f"{seg.scene_id}.mp4"
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},"
            f"eq=contrast=1.05:saturation=1.06:brightness=-0.04,format=yuv420p"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-ss",
                f"{seg.footage_start_offset:.2f}",
                "-i",
                seg.footage_path,
                "-t",
                f"{seg.duration_sec:.3f}",
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "19",
                "-pix_fmt",
                "yuv420p",
                str(out_seg),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        seg_files.append(out_seg)

    concat_txt = seg_dir / "concat.txt"
    concat_txt.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_files), encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
            "-c",
            "copy",
            str(out_stitched_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_stitched_mp4


def render_hybrid_korosi_video(
    plan: RenderPlan,
    work_dir: Path,
    output_mp4: Path,
    bgm_path: Optional[str],
    mascots_dir: Optional[Path] = None,
    use_mascot: bool = True,
    stitched_base_mp4: Optional[Path] = None,
    mixed_audio_wav: Optional[Path] = None,
) -> tuple[str, str]:
    """Render 1080x1920 30fps Hybrid Real-Footage + Mechanistic Comic-Science Animation video."""
    width, height, fps = 1080, 1920, 30
    work_dir.mkdir(parents=True, exist_ok=True)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    if stitched_base_mp4 is None or not stitched_base_mp4.exists():
        stitched_base_mp4 = work_dir / "real_footage_base_1080p.mp4"
        _prepare_clean_real_footage_base(plan, stitched_base_mp4, work_dir, width, height, fps)

    if mixed_audio_wav is None or not mixed_audio_wav.exists():
        mixed_audio_wav = work_dir / "mixed_audio_hybrid.wav"
        _mix_audio(plan, bgm_path=bgm_path, out_audio_wav=mixed_audio_wav)

    ass_path = work_dir / "subtitles_hybrid.ass"
    _write_ass_subtitles(plan.subtitles, ass_path, width=width, height=height, font_size_ratio=0.054)

    fonts = _load_fonts()
    mascots = MascotLibrary(mascots_dir) if (use_mascot and mascots_dir) else None

    # Pre-render static lower subtitle scrim once for speed
    bot_scrim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    bsd = ImageDraw.Draw(bot_scrim)
    for y in range(1260, height, 4):
        a = int(175 * ((y - 1260) / (height - 1260)) ** 1.35)
        bsd.rectangle([0, y, width, y + 4], fill=(10, 14, 26, a))

    escaped_ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    filter_complex = (
        f"[0:v][1:v]overlay=0:0:shortest=1,ass='{escaped_ass}',format=yuv420p[vout]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(stitched_base_mp4),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(mixed_audio_wav),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "2:a",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "19",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]

    scene_drawers = {
        "scene_1": draw_scene1_hook,
        "scene_2": draw_scene2_oxidation_compare,
        "scene_3": draw_scene3_atomic_mechanism,
        "scene_4": draw_scene4_porous_rust,
        "scene_5": draw_scene5_saltwater_race,
        "scene_6": draw_scene6_solution_and_outro,
    }

    total_frames = int(math.ceil(plan.total_duration_sec * fps))
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None

    segs = plan.segments
    try:
        for f_idx in range(total_frames):
            t_global = f_idx / fps
            # Locate active scene
            seg = segs[-1]
            seg_idx = len(segs) - 1
            for idx, s in enumerate(segs):
                if s.start_sec <= t_global < s.end_sec:
                    seg = s
                    seg_idx = idx
                    break
            local_t = max(0.0, t_global - seg.start_sec)

            canvas = bot_scrim.copy()
            draw = ImageDraw.Draw(canvas)

            drawer = scene_drawers.get(seg.scene_id)
            if drawer:
                drawer(canvas, draw, local_t, seg.duration_sec, fonts, mascots)

            # Diagonal wipe transition at scene boundaries (0.32s duration)
            if seg_idx > 0 and local_t < 0.32:
                draw = ImageDraw.Draw(canvas)
                draw_diagonal_wipe(draw, local_t / 0.32, width, height)

            # Persistent @kimiabill brand pill always cleanly on top
            draw = ImageDraw.Draw(canvas)
            draw_brand_pill(draw, fonts)

            proc.stdin.write(canvas.tobytes())
    finally:
        proc.stdin.close()

    stderr_out = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"FFmpeg hybrid render failed (code {ret}): {stderr_out[-800:]}")

    sheet_path = output_mp4.with_name(f"{output_mp4.stem}_contact_sheet.jpg")
    _build_contact_sheet(output_mp4, plan.segments, sheet_path)
    return str(output_mp4), str(sheet_path)
