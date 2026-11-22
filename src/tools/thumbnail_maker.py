"""Agent 7 Tool: High-CTR 9:16 Cover Thumbnail Generator for Kimiabill Shorts/Reels/TikTok."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance

from src.tools.mechanistic_animator import (
    CYAN,
    INK,
    ORANGE,
    RED,
    WHITE,
    YELLOW,
    MascotLibrary,
    _load_fonts,
    draw_brand_pill,
    draw_comic_box,
    draw_outlined_text,
)


def extract_bg_frame(video_path: Path, timestamp_sec: float, out_jpg: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp_sec:.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-q:v",
            "2",
            str(out_jpg),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_jpg


def generate_cover_thumbnail(
    bg_video_path: Path,
    bg_timestamp_sec: float,
    headline_top: str,
    headline_main: str,
    badge_text: str,
    out_jpg: Path,
    mascots_dir: Optional[Path] = None,
    use_mascot: bool = True,
) -> str:
    """Create a punchy 1080x1920 Shorts/Reels/TikTok cover thumbnail over real footage."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    tmp_frame = out_jpg.with_name(f"_tmp_{out_jpg.stem}.jpg")
    extract_bg_frame(bg_video_path, bg_timestamp_sec, tmp_frame)

    base = Image.open(tmp_frame).convert("RGBA")
    base = ImageEnhance.Contrast(base).enhance(1.18)
    base = ImageEnhance.Color(base).enhance(1.15)
    tmp_frame.unlink(missing_ok=True)

    # Top and bottom dramatic vignette for high CTR text pop
    scrim = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(0, 760, 4):
        a = int(195 * (1.0 - y / 760) ** 1.25)
        sd.rectangle([0, y, 1080, y + 4], fill=(12, 16, 34, a))
    for y in range(1180, 1920, 4):
        a = int(205 * ((y - 1180) / (1920 - 1180)) ** 1.2)
        sd.rectangle([0, y, 1080, y + 4], fill=(12, 16, 34, a))
    base.alpha_composite(scrim)

    fonts = _load_fonts()
    draw = ImageDraw.Draw(base)

    # Top-left @kimiabill brand badge
    draw_brand_pill(draw, fonts)

    # Top punchy badge pill
    bbox = draw.textbbox((0, 0), badge_text, font=fonts["small_bold"])
    bw = (bbox[2] - bbox[0]) + 72
    draw_comic_box(
        draw,
        (540 - bw // 2, 230, 540 + bw // 2, 304),
        fill=ORANGE,
        radius=34,
        border_w=5,
        shadow_offset=8,
    )
    draw_outlined_text(draw, (540, 267), badge_text, fonts["small_bold"], fill=WHITE, stroke_w=4)

    # Giant two-line comic-science headline
    draw_outlined_text(draw, (540, 395), headline_top, fonts["hook_sub"], fill=WHITE, stroke_w=10)
    draw_outlined_text(draw, (540, 515), headline_main, fonts["hook_main"], fill=YELLOW, stroke_w=12)

    # Big comic '?' and '!' accents
    draw_outlined_text(draw, (120, 740), "?", fonts["giant_sym"], fill=RED, stroke_w=10)
    draw_outlined_text(draw, (960, 725), "!", fonts["giant_sym"], fill=CYAN, stroke_w=10)

    # Duo Opsi B Mascots framing the real science visual
    if use_mascot and mascots_dir:
        lib = MascotLibrary(mascots_dir)
        lib.paste(base, "male_curious", center_x=275, bottom_y=1640, target_h=560, local_t=1.0, appear_at=0.0)
        lib.paste(base, "female_curious", center_x=805, bottom_y=1640, target_h=545, local_t=1.0, appear_at=0.0)

    base.convert("RGB").save(out_jpg, quality=93)
    return str(out_jpg)
