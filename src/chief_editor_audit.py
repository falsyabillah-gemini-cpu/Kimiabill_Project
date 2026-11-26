"""AI Chief Editor Fallback Auditor (`src/chief_editor_audit.py`).

Independent blind auditor evaluating rendered MP4 video+audio natively via Gemini File API
(or 8-10 evenly spaced keyframes + STT/transcript fallback when GEMINI_API_KEY is unset),
with strict zero-tolerance rubric and automatic cleanup.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.providers import call_structured_agent, _discover_gemini_flash_models, _extract_json
from src.state import ChiefEditorAuditOutput, ChiefEditorChecks
from src.tools.gdrive_sync import download_drive_file_by_id
from src.tools.review_manager import (
    clear_audit_buttons_on_telegram,
    get_review_record,
    handle_telegram_approve,
    handle_telegram_rerender,
    upsert_review_record,
)
from src.tools.telegram_notify import send_telegram_message

BASE_DIR = Path(__file__).resolve().parent.parent.parent

CHIEF_EDITOR_SYSTEM_PROMPT = """You are the Independent AI Chief Editor for YouTube Shorts channel @kimiabill.
Conduct a RUTHLESS blind audit of this rendered Indonesian Pure-Science Short.

ZERO-TOLERANCE FAIL CONDITIONS (if ANY occurs -> `decision` MUST BE `"REVISE"` or `"HOLD"`, NEVER `"APPROVE"`):
1. `science_accuracy_and_diagram_match`: Any scientific error or contradiction (e.g. nonpolar capsaicin shown reacting with plain water), misused `mechanical_stress` in a non-mechanics topic, or `redox_reaction` without real electron transfer.
2. `no_placeholder_or_clipping`: Any raw template placeholder (`Molekul A`, `Molekul B`, `SOLUSI:`, `REAKSI`, `TODO`, `None`, `undefined`), any word cut off mid-syllable (`diasi U`, `npone`, `...`), or labels overflowing diagram boxes.
3. `broll_relevance_and_no_foreign_bumper`: Any foreign university/lab intro or logo (e.g. PennState, LIFE IN THE LAB), PowerPoint/lecture slide, unrelated Arduino LCD, unrelated animal, small 16:9 clip inside static blur box instead of clean 9:16 framing, or watermark.
4. `audio_consistency_and_pronunciation`: Any voice shift mid-video away from consistent male Indonesian narrator `id-ID-ArdiNeural`, glaring mispronunciation (e.g. raw English words not adapted like `kapsaisin`/`glimfatik`, misspelled/misspoken acronyms), distracting dead silence >0.6s, or narration typos.
5. `shorts_pacing_and_youtube_cta`: Total duration outside 25-58s (ideal 30-48s), sluggish greeting hook, static bibliography end-card or `Thank you for watching`, OR ANY TikTok/IG `"follow"` CTA instead of YouTube `"Subscribe"` / seamless loop.

Decision policy:
- `"APPROVE"`: ONLY if ALL 5 checks are `"PASS"`, `fatal_flaws` is empty `[]`, and `overall_score >= 8.0`.
- `"REVISE"`: Fixable flaws (bad scene B-roll, diagram mismatch, script/pronunciation tweak) — write crystal-clear actionable `revision_instruction` in Indonesian.
- `"HOLD"`: Severe conceptual/safety flaw or repeated failure that must NOT publish publicly without human CEO review.

Output STRICT JSON matching the exact schema."""


def _find_or_fetch_video_for_episode(episode_id: str, rec_artifact: Optional[str] = None) -> Optional[Path]:
    ep_dir = BASE_DIR / "episodes" / episode_id
    if ep_dir.exists():
        finals = sorted(ep_dir.glob("FINAL_*.mp4"))
        if finals:
            return finals[-1]
        proxies = sorted((ep_dir / "renders").glob("proxy_*.mp4")) if (ep_dir / "renders").exists() else []
        if proxies:
            return proxies[-1]

    # Try yt_meta drive_video_file_id or rec_artifact as Drive File ID
    file_id: Optional[str] = None
    yt_meta_p = ep_dir / "handoffs" / "youtube_upload.json"
    if yt_meta_p.exists():
        try:
            file_id = json.loads(yt_meta_p.read_text(encoding="utf-8")).get("drive_video_file_id")
        except Exception:
            file_id = None
    if not file_id and rec_artifact and not ("/" in rec_artifact or "\\" in rec_artifact):
        file_id = rec_artifact

    if file_id:
        dest = ep_dir / f"FINAL_{episode_id}_audit.mp4"
        fetched = download_drive_file_by_id(file_id, dest)
        if fetched and fetched.exists():
            return fetched
    return None


def _upload_gemini_file_and_wait(mp4_path: Path, api_key: str) -> tuple[str, str]:
    """Upload MP4 via Gemini Files API resumable upload and poll until ACTIVE. Returns (file_uri, file_name)."""
    size_bytes = mp4_path.stat().st_size
    start_resp = requests.post(
        f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size_bytes),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": mp4_path.name}},
        timeout=30,
    )
    start_resp.raise_for_status()
    upload_url = start_resp.headers.get("X-Goog-Upload-URL") or start_resp.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini File API did not return X-Goog-Upload-URL")

    with mp4_path.open("rb") as fh:
        fin_resp = requests.post(
            upload_url,
            headers={
                "Content-Length": str(size_bytes),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            data=fh,
            timeout=180,
        )
    fin_resp.raise_for_status()
    fobj = fin_resp.json().get("file") or fin_resp.json()
    file_name = str(fobj.get("name") or "")
    file_uri = str(fobj.get("uri") or "")
    if not file_name or not file_uri:
        raise RuntimeError(f"Gemini File API missing file name/uri: {fin_resp.text[:200]}")

    for _ in range(30):
        state = str(fobj.get("state") or "").upper()
        if state == "ACTIVE":
            return file_uri, file_name
        if state == "FAILED":
            raise RuntimeError(f"Gemini File API video processing failed: {fobj}")
        time.sleep(2.0)
        chk = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}",
            timeout=20,
        )
        if chk.status_code == 200:
            fobj = chk.json().get("file") or chk.json()
    raise RuntimeError(f"Gemini File API processing timed out for {file_name}")


def _delete_gemini_file(file_name: Optional[str], api_key: str) -> None:
    if not file_name or not api_key:
        return
    try:
        requests.delete(
            f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}",
            timeout=20,
        )
    except Exception:
        pass


def _audit_native_mp4_gemini(mp4_path: Path, episode_meta: dict[str, Any], api_key: str) -> ChiefEditorAuditOutput:
    schema_hint = json.dumps(ChiefEditorAuditOutput.model_json_schema(), indent=2)
    preferred_model = os.environ.get("CHIEF_EDITOR_MODEL", "").strip()
    discovered = _discover_gemini_flash_models(api_key)
    models_to_try = [m for m in [preferred_model, "gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.6-flash", *discovered] if m]
    seen_m: list[str] = []
    for m in models_to_try:
        if m not in seen_m:
            seen_m.append(m)

    file_uri, file_name = _upload_gemini_file_and_wait(mp4_path, api_key)
    try:
        body = {
            "system_instruction": {"parts": [{"text": CHIEF_EDITOR_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}},
                        {
                            "text": (
                                f"EPISODE METADATA:\n{json.dumps(episode_meta, ensure_ascii=False)}\n\n"
                                f"Audit BOTH the video frames AND full spoken audio in the MP4 above.\n"
                                f"Return STRICT JSON matching this schema:\n{schema_hint}"
                            )
                        },
                    ],
                }
            ],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        last_err = ""
        for model in seen_m:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                json=body,
                timeout=90,
            )
            if resp.status_code == 200:
                txt = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                return ChiefEditorAuditOutput.model_validate(_extract_json(txt))
            last_err = f"HTTP {resp.status_code} ({model}): {resp.text[:200]}"
            if resp.status_code not in (400, 404, 429, 500, 502, 503, 504):
                break
        raise RuntimeError(f"Gemini File API Chief Editor failed across models: {last_err}")
    finally:
        # Mandatory cleanup immediately after audit completes
        _delete_gemini_file(file_name, api_key)


def _extract_keyframes_for_fallback(mp4_path: Path, count: int = 8) -> list[Path]:
    out_dir = mp4_path.parent / "_chief_editor_kf"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw_dur = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(mp4_path)],
            text=True,
        ).strip()
        dur = max(3.0, float(raw_dur))
    except Exception:
        dur = 35.0

    paths: list[Path] = []
    for i in range(count):
        ts = max(0.1, min(dur - 0.2, (dur * (i + 0.5)) / count))
        fp = out_dir / f"kf_{i:02d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(mp4_path), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", str(fp)],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if fp.exists():
            paths.append(fp)
    return paths


def _audit_keyframes_fallback(mp4_path: Path, episode_meta: dict[str, Any]) -> ChiefEditorAuditOutput:
    kf_paths = _extract_keyframes_for_fallback(mp4_path, count=8)
    try:
        return call_structured_agent(
            provider="auto",
            model=os.environ.get("CHIEF_EDITOR_FALLBACK_MODEL", "google/gemini-3.8-flash"),
            system_prompt=CHIEF_EDITOR_SYSTEM_PROMPT,
            user_payload={
                "audit_mode": "keyframes_plus_transcript_fallback",
                "episode_metadata": episode_meta,
            },
            response_model=ChiefEditorAuditOutput,
            image_paths=kf_paths,
        )
    finally:
        for p in kf_paths:
            p.unlink(missing_ok=True)


def evaluate_mp4_blind(mp4_path: Path, episode_meta: dict[str, Any]) -> ChiefEditorAuditOutput:
    """Run native MP4 video+audio audit on Gemini File API, falling back to 8 keyframes + transcript."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    verdict: Optional[ChiefEditorAuditOutput] = None
    if gemini_key:
        try:
            verdict = _audit_native_mp4_gemini(mp4_path, episode_meta, gemini_key)
        except Exception as exc:
            print(f"[chief_editor] Native Gemini File API audit fallback ke keyframes ({exc})")

    if verdict is None:
        verdict = _audit_keyframes_fallback(mp4_path, episode_meta)

    # Zero-tolerance programmatic enforcement
    checks = verdict.checks
    all_pass = all(
        val == "PASS"
        for val in (
            checks.science_accuracy_and_diagram_match,
            checks.no_placeholder_or_clipping,
            checks.broll_relevance_and_no_foreign_bumper,
            checks.audio_consistency_and_pronunciation,
            checks.shorts_pacing_and_youtube_cta,
        )
    )
    if (not all_pass or len(verdict.fatal_flaws) > 0 or verdict.overall_score < 8.0) and verdict.decision == "APPROVE":
        verdict.decision = "REVISE" if verdict.revision_instruction.strip() else "HOLD"
    return verdict


def run_chief_editor_audit(episode_id: str = "", force: bool = False) -> dict[str, Any]:
    """Audit pending episode when user pressed `Serahkan ke AI` or missed 18:15 WIB deadline."""
    rec = get_review_record(episode_id)
    if not rec:
        return {"ok": True, "action": "no_pending_review"}
    if not force and rec.status != "awaiting_review":
        return {"ok": True, "action": "skipped_non_awaiting", "status": rec.status, "episode_id": rec.episode_id}

    ep_dir = BASE_DIR / "episodes" / rec.episode_id
    mp4_path = _find_or_fetch_video_for_episode(rec.episode_id, rec.artifact_path_or_url)
    if not mp4_path or not mp4_path.exists():
        clear_audit_buttons_on_telegram(rec)
        rec.status = "held_by_ai"
        rec.decision_note = "File MP4 final tidak ditemukan untuk audit AI Chief Editor"
        upsert_review_record(rec)
        send_telegram_message(f"🛑 [AI Chief Editor HOLD] `{rec.topic_title}`: File MP4 video tidak ditemukan sehingga tidak dipublish otomatis.")
        return {"ok": False, "decision": "HOLD", "reason": "mp4_missing"}

    episode_meta: dict[str, Any] = {
        "episode_id": rec.episode_id,
        "topic_title": rec.topic_title,
        "duration_sec": rec.duration_sec,
        "references": rec.references,
    }
    a3_path = ep_dir / "handoffs" / "agent3_script.json"
    if a3_path.exists():
        try:
            a3 = json.loads(a3_path.read_text(encoding="utf-8"))
            episode_meta["transcript_scenes"] = [
                {
                    "scene_id": s.get("scene_id"),
                    "narration": s.get("narration"),
                    "mechanism": s.get("mechanism"),
                    "overlay_badge": s.get("overlay_badge"),
                }
                for s in a3.get("scenes") or []
            ]
        except Exception:
            pass

    verdict = evaluate_mp4_blind(mp4_path, episode_meta)
    print(f"[chief_editor] Verdict for {rec.episode_id}: {verdict.model_dump_json()}")

    if verdict.decision == "APPROVE":
        res = handle_telegram_approve(
            rec.episode_id,
            by_ai=True,
            ai_note=f"Skor AI Chief Editor: {verdict.overall_score}/10 (5/5 checks PASS)",
        )
        return {"ok": True, "decision": "APPROVE", "score": verdict.overall_score, "approve_result": res}

    if verdict.decision == "REVISE" and rec.revision_count < 1:
        rev_note = verdict.revision_instruction or "; ".join(verdict.fatal_flaws[:2]) or "Perbaiki ketidaksesuaian B-roll/diagram/audio."
        send_telegram_message(
            f"🤖🔄 [AI Chief Editor REVISE] `{rec.topic_title}` (Skor {verdict.overall_score}/10)\n"
            f"Temuan: {'; '.join(verdict.fatal_flaws) or '-'}\n"
            f"Menjalankan 1x revisi otomatis: {rev_note}"
        )
        rev_res = handle_telegram_rerender(rec.episode_id, feedback=rev_note)
        return {"ok": True, "decision": "REVISE", "revision_result": rev_res}

    # HOLD (either direct HOLD or revision budget exhausted)
    clear_audit_buttons_on_telegram(rec)
    rec.status = "held_by_ai"
    rec.decision_note = f"HOLD AI Chief Editor ({verdict.overall_score}/10): {'; '.join(verdict.fatal_flaws)}"
    upsert_review_record(rec)
    send_telegram_message(
        "\n".join([
            f"🛑 [AI Chief Editor HOLD — TIDAK DIPUBLISH] `{rec.topic_title}`",
            f"• Skor Blind Audit: {verdict.overall_score}/10",
            f"• Temuan Fatal: {'; '.join(verdict.fatal_flaws) or '-'}",
            f"• Saran Perbaikan: {verdict.revision_instruction or 'Perlu cek manual / ganti topik'}",
            f"• Draft aman tetap Unlisted di YouTube: {rec.youtube_url or '-'}",
        ])
    )
    return {"ok": True, "decision": "HOLD", "verdict": verdict.model_dump()}
