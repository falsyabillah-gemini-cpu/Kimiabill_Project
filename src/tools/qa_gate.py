"""Layer 0-Token Deterministic QA Gate (Pre-Render & Post-Render).

Runs fast deterministic & FFmpeg/ffprobe checks BEFORE spending Vision LLM tokens
or 1080p HD encode time, enforcing:
- Zero forbidden placeholder strings (`Molekul A/B`, `SOLUSI:`, `TODO`, `None`, `undefined`, `placeholder`)
- Zero TikTok/IG `"follow"` CTA leaks in YouTube scripts/copy
- Strict visual mechanism enum checks (`mechanical_stress` & `redox_reaction` only when domain matches)
- Diagram label character bounds (preventing mid-word clipping)
- Exact 1080x1920 (or 480x854 proxy) resolution, 25-58s duration, audio stream consistency, and no silent gap > 0.6s
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from src.state import EpisodeState, Gate0CheckResult, contains_forbidden_placeholder

MECHANICS_DOMAIN_TERMS: tuple[str, ...] = (
    "tekuk", "lentur", "elastis", "kaku", "rigid", "polimer", "hidrogel", "crosslink",
    "regang", "tegang", "tekanan", "kokoh", "tarik", "mekanik", "bend", "flex", "polymer",
    "elastic", "stress", "strain", "strength", "fiber", "serat", "jaringan", "gel", "plastik",
)

REDOX_DOMAIN_TERMS: tuple[str, ...] = (
    "elektron", "redoks", "oksidasi", "reduksi", "karat", "korosi", "besi", "baterai",
    "elektroda", "anoda", "katoda", "muatan", "electron", "redox", "oxid", "corros",
    "rust", "battery", "electrode", "galvan", "electrochem",
)

FOLLOW_CTA_RE = re.compile(r"\bfollow\b", flags=re.IGNORECASE)


def run_pre_render_gate0_checks(state: EpisodeState) -> tuple[bool, list[str]]:
    """Deterministic 0-token gate inspecting script, diagrams, badges, and YouTube copy BEFORE rendering."""
    issues: list[str] = []
    if state.agent3 is None:
        return False, ["Agent 3 script missing"]

    ep_blob_parts = [state.agent3.title]
    total_words = 0
    for s in state.agent3.scenes:
        ep_blob_parts.append(s.narration)
        total_words += len((s.narration or "").split())
        if s.overlay_badge:
            ep_blob_parts.append(s.overlay_badge)
    ep_blob_low = " ".join(ep_blob_parts).lower()

    if total_words < 72:
        issues.append(
            f"Narration too short ({total_words} words < 72 words minimum; risks falling below 25s after silence trim). Target 85-115 words (32-46s)."
        )

    if FOLLOW_CTA_RE.search(state.agent3.title or "") or FOLLOW_CTA_RE.search(state.agent3.caption_social or ""):
        issues.append("Forbidden CTA word 'follow' found in YouTube title/caption (use 'Subscribe').")

    for idx, sc in enumerate(state.agent3.scenes):
        nar = (sc.narration or "").strip()
        nar_low = nar.lower()
        if FOLLOW_CTA_RE.search(nar):
            issues.append(f"{sc.scene_id}: forbidden CTA word 'follow' in narration (must use 'Subscribe' or seamless loop).")
        bad_nar_tok = contains_forbidden_placeholder(nar_low)
        bad_badge_tok = contains_forbidden_placeholder(sc.overlay_badge or "")
        if bad_nar_tok or bad_badge_tok:
            issues.append(f"{sc.scene_id}: forbidden placeholder '{bad_nar_tok or bad_badge_tok}' in narration/badge.")
        if sc.overlay_badge and len(sc.overlay_badge.strip()) > 48:
            issues.append(f"{sc.scene_id}: overlay_badge too long ({len(sc.overlay_badge)} chars > 48).")

        m = sc.mechanism
        if m is not None:
            scene_ctx = f"{ep_blob_low} {nar_low} {m.title.lower()} {m.actor_a.lower()} {m.actor_b.lower()} {m.result.lower()}"
            for field_name, val, max_len in (
                ("title", m.title, 32),
                ("actor_a", m.actor_a, 20),
                ("actor_b", m.actor_b, 20),
                ("result", m.result, 24),
            ):
                sval = (val or "").strip()
                if len(sval) > max_len:
                    issues.append(f"{sc.scene_id}.mechanism.{field_name} exceeds {max_len} chars ('{sval}')")
                low_v = sval.lower()
                if contains_forbidden_placeholder(low_v) or low_v in {"a", "b", "produk", "hasil", "todo", "none", "undefined"}:
                    issues.append(f"{sc.scene_id}.mechanism.{field_name} contains forbidden placeholder '{sval}'")

            if m.kind in ("polymer_crosslink", "mechanical_stress"):
                if not any(re.search(rf"\b{re.escape(k)}", scene_ctx) for k in MECHANICS_DOMAIN_TERMS):
                    issues.append(
                        f"{sc.scene_id}: mechanism.kind='{m.kind}' invalid for non-mechanics topic (use process_flow / concept_comparison / highlight_stat)."
                    )
            if m.kind in ("electron_transfer", "redox_reaction"):
                if not any(re.search(rf"\b{re.escape(k)}", scene_ctx) for k in REDOX_DOMAIN_TERMS):
                    issues.append(
                        f"{sc.scene_id}: mechanism.kind='{m.kind}' invalid without real redox/electron transfer (use process_flow / concept_comparison)."
                    )
        elif 0 < idx < len(state.agent3.scenes) - 1:
            # Middle scenes with no mechanism object still OK if clean footage + badge, never forced placeholder
            pass

    if state.agent7 is not None:
        for cp in state.agent7.platform_copies:
            if cp.platform == "youtube_shorts":
                for fld, txt in (("title", cp.title), ("caption", cp.caption), ("pinned_comment", cp.pinned_comment or "")):
                    if FOLLOW_CTA_RE.search(txt):
                        issues.append(f"Agent 7 youtube_shorts.{fld} uses forbidden 'follow' (must use 'Subscribe').")

    return len(issues) == 0, issues


def run_gate0_checks(
    video_path: str | Path,
    expected_width: int,
    expected_height: int,
    min_duration_sec: float = 25.0,
    max_duration_sec: float = 58.0,
    max_silent_gap_sec: float = 0.60,
) -> Gate0CheckResult:
    p = Path(video_path)
    issues: list[str] = []

    # 1. Resolution, streams, and duration via ffprobe
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,sample_rate:format=duration",
            "-of",
            "json",
            str(p),
        ],
        text=True,
    )
    meta = json.loads(raw)
    streams = meta.get("streams", [])
    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not v_streams:
        raise RuntimeError(f"No video stream in {p}")
    v0 = v_streams[0]
    width = int(v0.get("width") or 0)
    height = int(v0.get("height") or 0)
    dur = float(meta["format"]["duration"])

    if width != expected_width or height != expected_height:
        issues.append(f"Resolution mismatch: got {width}x{height}, expected {expected_width}x{expected_height}")
    if dur < min_duration_sec or dur > max_duration_sec:
        issues.append(f"Duration {dur:.1f}s outside strict Shorts target [{min_duration_sec}s, {max_duration_sec}s]")
    if not a_streams:
        issues.append("Audio stream missing")

    # 2. Audio loudness & clipping check (volumedetect)
    vol_proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(p),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    mean_match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", vol_proc.stderr)
    max_match = re.search(r"max_volume:\s*([-\d.]+)\s*dB", vol_proc.stderr)
    mean_db = float(mean_match.group(1)) if mean_match else -99.0
    max_db = float(max_match.group(1)) if max_match else -99.0

    if mean_db < -28.0:
        issues.append(f"Audio too quiet (mean {mean_db:.1f} dB)")
    if max_db > -0.1:
        issues.append(f"Audio clipping risk (max {max_db:.1f} dB)")

    # 3. Silent gap detection (strict <= 0.6s anywhere in audio stream)
    sil_proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(p),
            "-af",
            f"silencedetect=noise=-42dB:d={max_silent_gap_sec:.2f}",
            "-vn",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    sil_gaps = [float(m.group(1)) for m in re.finditer(r"silence_duration:\s*([\d.]+)", sil_proc.stderr)]
    if sil_gaps:
        max_gap = max(sil_gaps)
        issues.append(f"Dead silence gap detected ({max_gap:.2f}s > {max_silent_gap_sec:.2f}s)")

    # 4. Black frame detection
    blk_proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(p),
            "-vf",
            "blackdetect=d=0.3:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    black_total = sum(
        float(m.group(1))
        for m in re.finditer(r"black_duration:([\d.]+)", blk_proc.stderr)
    )
    black_ratio = round(black_total / dur, 4) if dur > 0 else 1.0
    if black_ratio > 0.05:
        issues.append(f"Excessive black frames detected ({black_ratio*100:.1f}% of video)")

    return Gate0CheckResult(
        passed=len(issues) == 0,
        duration_sec=round(dur, 2),
        width=width,
        height=height,
        mean_volume_db=round(mean_db, 2),
        max_volume_db=round(max_db, 2),
        black_frame_ratio=black_ratio,
        issues=issues,
    )
