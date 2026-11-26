"""Telegram Manual Audit Manager & `data/pending_review.json` state machine.

Coordinates pre-publish review (`unlisted` YouTube draft -> 4 interactive inline buttons
-> Scheduled Publish / Revise / Reject & New Topic / AI Chief Editor Fallback).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw

from src.state import EpisodeState, PendingReviewRecord
from src.tools.gdrive_sync import (
    delete_youtube_video,
    update_youtube_publish_status,
)
from src.tools.telegram_notify import (
    _api_url,
    _bot_creds,
    record_owner_feedback,
    send_telegram_message,
)
from src.tools.topic_guard import extract_topic_keywords, record_rejected_topic, slugify_topic
from src.tools.backup_queue import (
    peek_backup_count,
    pop_next_backup_video,
    push_backup_video,
)

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PENDING_REVIEW_PATH = BASE_DIR / "data" / "pending_review.json"


def compute_target_publish_iso(publish_time_wib: Optional[str] = None, now_wib: Optional[datetime] = None) -> str:
    now = now_wib or datetime.now(WIB)
    hhmm = (
        (publish_time_wib or "").strip()
        or os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
        or os.environ.get("PUBLISH_TIME_WIB", "").strip()
    )
    if ":" not in hhmm:
        hhmm = "12:00" if now.hour < 15 else "19:00"
    hh, mm = (int(x) for x in hhmm.split(":", 1))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target.isoformat(timespec="seconds")


def load_pending_review_store() -> dict[str, Any]:
    if not PENDING_REVIEW_PATH.exists():
        return {"current": None, "history": []}
    try:
        raw = json.loads(PENDING_REVIEW_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("current", None)
            raw.setdefault("history", [])
            return raw
    except Exception:
        pass
    return {"current": None, "history": []}


def save_pending_review_store(store: dict[str, Any]) -> None:
    PENDING_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEW_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def get_review_record(episode_id: str = "") -> Optional[PendingReviewRecord]:
    store = load_pending_review_store()
    cur = store.get("current")
    if cur and (not episode_id or cur.get("episode_id") == episode_id):
        return PendingReviewRecord.model_validate(cur)
    if episode_id:
        for row in reversed(store.get("history") or []):
            if row.get("episode_id") == episode_id:
                return PendingReviewRecord.model_validate(row)
    return None


def upsert_review_record(rec: PendingReviewRecord) -> None:
    rec.updated_at = datetime.now(WIB).isoformat(timespec="seconds")
    store = load_pending_review_store()
    store["current"] = rec.model_dump()
    hist = [h for h in (store.get("history") or []) if h.get("episode_id") != rec.episode_id]
    hist.append(rec.model_dump())
    store["history"] = hist[-120:]
    save_pending_review_store(store)


def clear_audit_buttons_on_telegram(rec: PendingReviewRecord) -> None:
    """Idempotent anti-double-click: remove inline keyboard on Telegram audit message."""
    token, chat_id = _bot_creds()
    if not (token and chat_id and rec.telegram_message_id):
        return
    try:
        requests.post(
            _api_url(token, "editMessageReplyMarkup"),
            json={
                "chat_id": chat_id,
                "message_id": int(rec.telegram_message_id),
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=15,
        )
    except Exception:
        pass


def build_4_keyframe_collage(video_path: Path, out_jpg: Path) -> Optional[Path]:
    """Extract 4 keyframes evenly spaced across video into a 2x2 collage JPG."""
    if not video_path.exists():
        return None
    try:
        raw_dur = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
            ],
            text=True,
        ).strip()
        dur = max(4.0, float(raw_dur))
    except Exception:
        dur = 35.0

    tmp_dir = out_jpg.parent / "_audit_collage"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    imgs: list[Image.Image] = []
    for idx, frac in enumerate((0.12, 0.38, 0.65, 0.90)):
        fp = tmp_dir / f"kf_{idx}.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{dur * frac:.2f}", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "3", str(fp),
            ],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if fp.exists():
            im = Image.open(fp).convert("RGB")
            im.thumbnail((540, 960))
            imgs.append(im)
            fp.unlink(missing_ok=True)
    if not imgs:
        return None
    w, h = imgs[0].size
    pad = 12
    canvas = Image.new("RGB", (w * 2 + pad * 3, h * 2 + pad * 3), (18, 22, 34))
    for idx, im in enumerate(imgs[:4]):
        r, c = divmod(idx, 2)
        canvas.paste(im.resize((w, h)), (pad + c * (w + pad), pad + r * (h + pad)))
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_jpg, quality=88)
    return out_jpg


def _build_inline_keyboard(episode_id: str, publish_time_label: str = "19:00 WIB") -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": f"✅ Upload / Jadwalkan ({publish_time_label})", "callback_data": f"approve:{episode_id}"}],
            [
                {"text": "🔄 Revisi Video Ini", "callback_data": f"revise:{episode_id}"},
                {"text": "💾 Simpan Jadi Cadangan", "callback_data": f"save_backup:{episode_id}"},
            ],
            [{"text": "❌ Tolak / Ganti", "callback_data": f"reject:{episode_id}"}],
            [{"text": "🤖 Serahkan ke AI", "callback_data": f"ai_audit:{episode_id}"}],
        ]
    }


def send_audit_review_message(
    state: EpisodeState,
    video_path: Path,
    *,
    qa_summary: str = "",
    target_publish_iso: Optional[str] = None,
    artifact_path_or_url: Optional[str] = None,
) -> PendingReviewRecord:
    """Create `pending_review.json` entry and send MP4 (<45MB) or 4-keyframe collage + 4 inline buttons to Telegram."""
    ep_dir = BASE_DIR / "episodes" / state.episode_id
    yt_meta_path = ep_dir / "handoffs" / "youtube_upload.json"
    yt_meta: dict[str, Any] = {}
    if yt_meta_path.exists():
        try:
            yt_meta = json.loads(yt_meta_path.read_text(encoding="utf-8"))
        except Exception:
            yt_meta = {}

    yt_url = state.youtube_url or yt_meta.get("youtube_url")
    yt_vid = yt_meta.get("video_id") or (yt_url.rstrip("/").split("/")[-1] if yt_url else None)
    drive_file_id = yt_meta.get("drive_video_file_id")
    drive_url = state.gdrive_folder_url or yt_meta.get("drive_folder_url")

    pub_iso = target_publish_iso or compute_target_publish_iso()
    try:
        pub_dt = datetime.fromisoformat(pub_iso)
        pub_hhmm = pub_dt.strftime("%H:%M WIB")
        fallback_hhmm = (pub_dt - timedelta(minutes=15)).strftime("%H:%M WIB")
    except Exception:
        pub_hhmm = "19:00 WIB"
        fallback_hhmm = "18:45 WIB"

    winner = (
        next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if state.agent1 and state.agent2
        else None
    )
    title = state.agent3.title if state.agent3 else state.episode_id
    slug = state.agent3.episode_slug if state.agent3 else slugify_topic(title)
    dur_sec = round(float(state.render_plan.total_duration_sec), 1) if state.render_plan else 0.0
    refs = (state.render_plan.references if state.render_plan else []) or []
    score = state.agent5_history[-1].overall_score if state.agent5_history else 0.0

    rec = PendingReviewRecord(
        episode_id=state.episode_id,
        topic_title=title,
        topic_slug=slug,
        source_url=winner.viral_source_url if winner else None,
        keywords=extract_topic_keywords(f"{title} {winner.phenomenon if winner else ''}"),
        topic_bucket=winner.topic_bucket if winner else "everyday",
        discipline=state.render_plan.discipline if state.render_plan else "kimia",
        section=state.render_plan.section_tag if state.render_plan else "REGULER",
        duration_sec=dur_sec,
        references=refs,
        youtube_video_id=yt_vid,
        youtube_url=yt_url,
        drive_url=drive_url,
        artifact_path_or_url=artifact_path_or_url or drive_file_id or str(video_path),
        revision_count=state.revision_count,
        qa_summary=qa_summary or f"Agent 5 QA {score}/10",
        target_publish_iso=pub_iso,
        status="awaiting_review",
        reminder_sent=False,
        created_at=datetime.now(WIB).isoformat(timespec="seconds"),
    )

    collage_path = build_4_keyframe_collage(video_path, ep_dir / f"COLLAGE_4KF_{state.episode_id}.jpg")

    t1 = t2 = t3 = 0
    if state.agent6 and state.agent6.assets:
        for a in state.agent6.assets:
            src = (a.source or "").lower()
            if src in ("ai_scene", "tier3_ambient"):
                t3 += 1
            elif src.endswith("_photo"):
                t2 += 1
            else:
                t1 += 1
    tier_line = f"🎞️ Sumber Visual: {t1} Tier-1 Video | {t2} Tier-2 Foto | {t3} Tier-3 AI"

    refs_txt = "\n".join(f"• {r[:90]}" for r in refs[:2]) if refs else "• Editorial Sains Kimiabill"
    caption = "\n".join([
        f"🧪 AUDIT VIDEO KIMIABILL ({rec.section})",
        f"Judul: {title}",
        f"Durasi: {dur_sec:.1f} dtk | Skor Agent 5: {score}/10 | Revisi: {rec.revision_count}x",
        tier_line,
        f"Target Tayang: {pub_hhmm} (Status saat ini: Unlisted Draft)",
        f"▶️ YouTube Unlisted: {yt_url or '-'}",
        f"📁 Google Drive: {drive_url or '-'}",
        "",
        "📚 Referensi (Deskripsi YT):",
        refs_txt,
        "",
        f"Pilih aksi di bawah sebelum AI Chief Editor ambil alih otomatis ({fallback_hhmm}):",
    ])[:1000]

    token, chat_id = _bot_creds()
    keyboard = _build_inline_keyboard(state.episode_id, publish_time_label=pub_hhmm)
    msg_id: Optional[int] = None

    if token and chat_id:
        size_mb = (video_path.stat().st_size / (1024 * 1024)) if video_path.exists() else 999.0
        sent_ok = False
        if video_path.exists() and size_mb < 45.0:
            try:
                with video_path.open("rb") as fh:
                    resp = requests.post(
                        _api_url(token, "sendVideo"),
                        data={
                            "chat_id": chat_id,
                            "caption": caption,
                            "supports_streaming": "true",
                            "reply_markup": json.dumps(keyboard),
                        },
                        files={"video": (video_path.name, fh, "video/mp4")},
                        timeout=120,
                    )
                j = resp.json() if resp.status_code == 200 else {}
                if j.get("ok"):
                    msg_id = int((j.get("result") or {}).get("message_id") or 0) or None
                    sent_ok = True
            except Exception as exc:
                print(f"[audit] sendVideo gagal ({exc}), fallback ke kolase 4 keyframe...")

        if not sent_ok and collage_path and collage_path.exists():
            try:
                with collage_path.open("rb") as fh:
                    resp = requests.post(
                        _api_url(token, "sendPhoto"),
                        data={
                            "chat_id": chat_id,
                            "caption": caption,
                            "reply_markup": json.dumps(keyboard),
                        },
                        files={"photo": (collage_path.name, fh)},
                        timeout=60,
                    )
                j = resp.json() if resp.status_code == 200 else {}
                if j.get("ok"):
                    msg_id = int((j.get("result") or {}).get("message_id") or 0) or None
                    sent_ok = True
            except Exception as exc:
                print(f"[audit] sendPhoto collage gagal ({exc})")

        if not sent_ok:
            resp = requests.post(
                _api_url(token, "sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": caption,
                    "reply_markup": keyboard,
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            j = resp.json() if resp.status_code == 200 else {}
            if j.get("ok"):
                msg_id = int((j.get("result") or {}).get("message_id") or 0) or None

    rec.telegram_message_id = msg_id
    upsert_review_record(rec)
    return rec


def send_pending_review_reminder() -> dict[str, Any]:
    """Run at 18:00 WIB: send 1 concise reminder if current episode is still awaiting review."""
    rec = get_review_record()
    if not rec:
        return {"ok": True, "action": "no_pending_review"}
    if rec.status != "awaiting_review":
        return {"ok": True, "action": "already_handled", "status": rec.status}
    if rec.reminder_sent:
        return {"ok": True, "action": "reminder_already_sent"}

    token, chat_id = _bot_creds()
    fallback_hhmm = "11:45 / 18:45 WIB"
    target_hhmm = rec.target_publish_iso or "jadwal tayang"
    if rec.target_publish_iso:
        try:
            pub_dt = datetime.fromisoformat(rec.target_publish_iso)
            target_hhmm = pub_dt.strftime("%H:%M WIB")
            fallback_hhmm = (pub_dt - timedelta(minutes=15)).strftime("%H:%M WIB")
        except Exception:
            pass
    msg = (
        f"⏰ PENGINGAT AUDIT KIMIABILL (15 menit menuju AI Fallback)\n"
        f"• Judul: {rec.topic_title}\n"
        f"• Episode: `{rec.episode_id}`\n"
        f"• Target Tayang: {target_hhmm}\n"
        f"• Unlisted YT: {rec.youtube_url or '-'}\n"
        f"Silakan tekan tombol pada pesan preview di atas (Approve / Revisi / Tolak / AI Audit). "
        f"Jika tidak ada respon hingga {fallback_hhmm}, AI Chief Editor akan otomatis mengaudit."
    )
    if token and chat_id:
        send_telegram_message(msg)
    rec.reminder_sent = True
    upsert_review_record(rec)
    return {"ok": True, "action": "reminder_sent", "episode_id": rec.episode_id}


def handle_telegram_approve(
    episode_id: str = "",
    *,
    by_ai: bool = False,
    ai_note: str = "",
) -> dict[str, Any]:
    """Schedule YouTube private+publishAt (if before target_publish_iso) or publish immediately."""
    rec = get_review_record(episode_id)
    if not rec:
        raise RuntimeError(f"Pending review tidak ditemukan untuk episode_id='{episode_id}'")

    if rec.status in ("approved", "approved_by_ai"):
        clear_audit_buttons_on_telegram(rec)
        return {"ok": True, "idempotent_skip": True, "status": rec.status, "episode_id": rec.episode_id}
    if rec.status == "rejected":
        raise RuntimeError(f"Episode '{rec.episode_id}' sudah ditolak sebelumnya.")

    clear_audit_buttons_on_telegram(rec)

    now_wib = datetime.now(WIB)
    target_iso = rec.target_publish_iso or compute_target_publish_iso()
    target_dt = datetime.fromisoformat(target_iso)
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=WIB)

    ep_dir = BASE_DIR / "episodes" / rec.episode_id
    first_cmt: Optional[str] = None
    a7_p = ep_dir / "handoffs" / "agent7_package.json"
    if a7_p.exists():
        try:
            a7 = json.loads(a7_p.read_text(encoding="utf-8"))
            for cp in a7.get("platform_copies") or []:
                if cp.get("platform") == "youtube_shorts" and cp.get("pinned_comment"):
                    first_cmt = str(cp["pinned_comment"]).strip()
                    break
        except Exception:
            pass

    if rec.youtube_video_id:
        if now_wib < target_dt:
            upd = update_youtube_publish_status(
                rec.youtube_video_id,
                privacy_status="private",
                publish_at_iso=target_dt.isoformat(timespec="seconds"),
            )
            mode_label = f"Scheduled Private -> Tayang {target_dt.strftime('%H:%M WIB')}"
        else:
            upd = update_youtube_publish_status(
                rec.youtube_video_id,
                privacy_status="public",
                first_comment=first_cmt,
            )
            mode_label = "Published Public Sekarang"
    else:
        upd = {"ok": False, "warning": "no youtube_video_id"}
        mode_label = "Disetujui (tanpa YouTube video ID)"

    from src.tools.weekly_growth_auditor import mark_audience_request_fulfilled, record_published_episode

    state_file = ep_dir / "episode_state.json"
    qa_score = None
    hook_line = ""
    mechanisms_used: list[dict[str, Any]] = []
    req_id = None
    if state_file.exists():
        try:
            st = EpisodeState.model_validate_json(state_file.read_text(encoding="utf-8"))
            qa_score = st.agent5_history[-1].overall_score if st.agent5_history else None
            hook_line = st.agent3.scenes[0].narration if (st.agent3 and st.agent3.scenes) else ""
            req_id = st.request_id
            if st.agent3:
                mechanisms_used = [
                    {
                        "scene_id": s.scene_id,
                        "kind": s.mechanism.kind,
                        "title": s.mechanism.title,
                        "actor_a": s.mechanism.actor_a,
                        "actor_b": s.mechanism.actor_b,
                        "result": s.mechanism.result,
                        "preset_id": s.mechanism.preset_id,
                    }
                    for s in st.agent3.scenes
                    if s.mechanism is not None
                ]
        except Exception:
            pass

    record_published_episode(
        episode_id=rec.episode_id,
        title=rec.topic_title,
        discipline=rec.discipline,
        section=rec.section,
        youtube_url=rec.youtube_url,
        gdrive_folder_url=rec.drive_url,
        qa_score=qa_score,
        duration_sec=rec.duration_sec,
        hook_line=hook_line,
        revisions_used=rec.revision_count,
        topic_slug=rec.topic_slug,
        source_url=rec.source_url,
        keywords=rec.keywords,
        topic_bucket=rec.topic_bucket,
        mechanisms_used=mechanisms_used,
        publish_mode="public" if now_wib >= target_dt else "scheduled",
    )
    if req_id and rec.youtube_url:
        try:
            mark_audience_request_fulfilled(req_id, rec.episode_id, youtube_url=rec.youtube_url)
        except Exception:
            pass

    rec.status = "approved_by_ai" if by_ai else "approved"
    rec.decision_note = ai_note or mode_label
    upsert_review_record(rec)

    actor_label = "🤖 AI Chief Editor Fallback" if by_ai else "✅ Disetujui Owner"
    send_telegram_message(
        "\n".join([
            f"{actor_label}: `{rec.topic_title}`",
            f"• Status YouTube: {mode_label}",
            f"• Link Shorts: {rec.youtube_url or '-'}",
            f"• Google Drive: {rec.drive_url or '-'}",
            *( [f"• Catatan AI: {ai_note}"] if ai_note else [] ),
        ])
    )
    return {"ok": True, "status": rec.status, "mode": mode_label, "youtube_update": upd}


def handle_telegram_rerender(episode_id: str = "", feedback: str = "") -> dict[str, Any]:
    """Revise active episode (`telegram_rerender`), delete old unlisted YT draft, re-render & re-send 4 audit buttons."""
    rec = get_review_record(episode_id)
    clean_fb = " ".join((feedback or "").strip().split())
    if clean_fb:
        record_owner_feedback(clean_fb, mode="rerender", target_episode_id=(rec.episode_id if rec else episode_id), compile_agents=True)

    if not rec:
        # No active pending review found — fall back to fresh production using owner feedback
        from src.scheduler import run_one_automated_episode
        os.environ["PUBLISH_MODE"] = "draft"
        run_one_automated_episode()
        return {"ok": True, "fallback_full_episode": True}

    if rec.status in ("approved", "approved_by_ai", "rejected"):
        clear_audit_buttons_on_telegram(rec)
        raise RuntimeError(f"Episode '{rec.episode_id}' berstatus '{rec.status}' dan tidak bisa direvisi.")

    clear_audit_buttons_on_telegram(rec)
    rec.status = "revising"
    upsert_review_record(rec)

    if rec.youtube_video_id:
        delete_youtube_video(rec.youtube_video_id)

    ep_dir = BASE_DIR / "episodes" / rec.episode_id
    state_file = ep_dir / "episode_state.json"
    if not state_file.exists():
        # If running on a fresh runner without local episode files, produce a fresh replacement cleanly
        from src.scheduler import run_one_automated_episode
        os.environ["PUBLISH_MODE"] = "draft"
        run_one_automated_episode()
        return {"ok": True, "recreated_new_episode": True}

    from src.pipeline import (
        load_or_init_state,
        prepare_episode_thumbnail,
        rescout_affected_scenes,
        run_agent_1_to_3,
        run_agent_4_proxy_and_gate0,
        run_agent_5_video_qa,
        run_final_hd_render,
        run_upload,
        save_state,
    )

    state = load_or_init_state(rec.episode_id)
    new_rev = rec.revision_count + 1
    low_fb = clean_fb.lower()
    wants_broll = any(k in low_fb for k in ("b-roll", "broll", "footage", "klip", "visual", "bumper"))
    wants_script_or_diagram = any(k in low_fb for k in ("naskah", "script", "audio", "suara", "pelafalan", "diagram", "cta", "typo", "salah"))

    if wants_script_or_diagram or (not wants_broll):
        state = run_agent_1_to_3(rec.episode_id, topic_hint=rec.topic_title)
        state = rescout_affected_scenes(rec.episode_id, [s.scene_id for s in state.agent3.scenes]) if (wants_broll and state.agent3) else state
        # Reset audio/agent7 cache so new script & pronunciation are regenerated cleanly
        state.render_plan = None
        state.agent7 = None
        save_state(state)
    elif wants_broll and state.agent3:
        state = rescout_affected_scenes(rec.episode_id, [s.scene_id for s in state.agent3.scenes])

    proxy_ver = new_rev + 1
    state, _proxy_mp4, sheet_path = run_agent_4_proxy_and_gate0(rec.episode_id, version=proxy_ver)
    state, audit = run_agent_5_video_qa(rec.episode_id, sheet_path=sheet_path, version=proxy_ver)
    state.revision_count = new_rev
    save_state(state)

    state = prepare_episode_thumbnail(rec.episode_id)
    state, final_mp4, _ = run_final_hd_render(rec.episode_id)

    prev_mode = os.environ.get("PUBLISH_MODE")
    os.environ["PUBLISH_MODE"] = "draft"
    try:
        qa_sum = f"Revisi #{new_rev} Lolos QA | Skor {audit.overall_score}/10"
        state = run_upload(rec.episode_id, allow_youtube=True, qa_summary=qa_sum, send_basic_telegram_summary=False)
    finally:
        if prev_mode is None:
            os.environ.pop("PUBLISH_MODE", None)
        else:
            os.environ["PUBLISH_MODE"] = prev_mode

    new_rec = send_audit_review_message(
        state,
        Path(final_mp4),
        qa_summary=f"Revisi #{new_rev} | Skor Agent 5: {audit.overall_score}/10",
        target_publish_iso=rec.target_publish_iso,
    )
    return {"ok": True, "episode_id": rec.episode_id, "revision_count": new_rec.revision_count, "youtube_url": new_rec.youtube_url}


def handle_telegram_reject_next(episode_id: str = "") -> dict[str, Any]:
    """Blacklist rejected topic in `data/rejected_topics.json`, delete old YT draft, and produce a fresh topic."""
    rec = get_review_record(episode_id)
    if rec:
        if rec.status in ("approved", "approved_by_ai"):
            clear_audit_buttons_on_telegram(rec)
            raise RuntimeError(f"Episode '{rec.episode_id}' sudah disetujui dan tidak bisa ditolak ulang.")
        clear_audit_buttons_on_telegram(rec)
        record_rejected_topic(
            episode_id=rec.episode_id,
            title=rec.topic_title,
            topic_slug=rec.topic_slug,
            source_url=rec.source_url,
            keywords=rec.keywords,
            reason="Ditolak via tombol audit Telegram",
        )
        if rec.youtube_video_id:
            delete_youtube_video(rec.youtube_video_id)
        rec.status = "rejected"
        rec.decision_note = "Ditolak user -> diganti topik baru"
        upsert_review_record(rec)

    from src.scheduler import run_one_automated_episode
    os.environ["PUBLISH_MODE"] = "draft"
    run_one_automated_episode()
    latest = get_review_record()
    return {"ok": True, "rejected_episode_id": rec.episode_id if rec else episode_id, "new_episode_id": latest.episode_id if latest else None}


def save_episode_as_backup(
    state: EpisodeState,
    *,
    qa_summary: str = "",
    reason: str = "saved_manual",
) -> dict[str, Any]:
    """Store an unlisted EpisodeState directly into `data/backup_queue.json` and notify Telegram."""
    ep_dir = BASE_DIR / "episodes" / state.episode_id
    yt_meta_path = ep_dir / "handoffs" / "youtube_upload.json"
    yt_meta: dict[str, Any] = {}
    if yt_meta_path.exists():
        try:
            yt_meta = json.loads(yt_meta_path.read_text(encoding="utf-8"))
        except Exception:
            yt_meta = {}

    yt_url = state.youtube_url or yt_meta.get("youtube_url")
    yt_vid = yt_meta.get("video_id") or (yt_url.rstrip("/").split("/")[-1] if yt_url else None)
    drive_file_id = yt_meta.get("drive_video_file_id")
    drive_url = state.gdrive_folder_url or yt_meta.get("drive_folder_url")

    winner = (
        next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if state.agent1 and state.agent2
        else None
    )
    title = state.agent3.title if state.agent3 else state.episode_id
    slug = state.agent3.episode_slug if state.agent3 else slugify_topic(title)
    dur_sec = round(float(state.render_plan.total_duration_sec), 1) if state.render_plan else 0.0
    refs = (state.render_plan.references if state.render_plan else []) or []
    score = state.agent5_history[-1].overall_score if state.agent5_history else 0.0

    pinned_cmt: Optional[str] = None
    if state.agent7:
        for cp in state.agent7.platform_copies:
            if cp.platform == "youtube_shorts" and cp.pinned_comment:
                pinned_cmt = cp.pinned_comment.strip()
                break

    item = push_backup_video(
        {
            "episode_id": state.episode_id,
            "topic_title": title,
            "topic_slug": slug,
            "source_url": winner.viral_source_url if winner else None,
            "keywords": extract_topic_keywords(f"{title} {winner.phenomenon if winner else ''}"),
            "topic_bucket": winner.topic_bucket if winner else "everyday",
            "discipline": state.render_plan.discipline if state.render_plan else "kimia",
            "section": state.render_plan.section_tag if state.render_plan else "REGULER",
            "duration_sec": dur_sec,
            "references": refs,
            "youtube_video_id": yt_vid,
            "youtube_url": yt_url,
            "drive_url": drive_url,
            "artifact_path_or_url": drive_file_id,
            "qa_summary": qa_summary or f"Agent 5 QA {score}/10",
            "pinned_comment": pinned_cmt,
        },
        reason=reason,
    )
    stok = peek_backup_count()
    send_telegram_message(
        "\n".join([
            f"💾 [BANK CADANGAN KIMIABILL] Video tersimpan sebagai cadangan!",
            f"• Judul: `{title}`",
            f"• Episode: `{state.episode_id}` | Skor QA: {score}/10",
            f"• YouTube (Unlisted aman): {yt_url or '-'}",
            f"• Google Drive: {drive_url or '-'}",
            f"• Total stok cadangan sekarang: {stok} video (otomatis terpakai jika produksi gagal / Anda pilih Pakai Cadangan saat Reject).",
        ])
    )
    return item


def handle_telegram_save_backup(episode_id: str = "") -> dict[str, Any]:
    """Move an `awaiting_review` draft out of `pending_review.json['current']` into `data/backup_queue.json`."""
    rec = get_review_record(episode_id)
    if not rec:
        raise RuntimeError(f"Draft review tidak ditemukan untuk episode_id='{episode_id}'")
    if rec.status in ("approved", "approved_by_ai"):
        clear_audit_buttons_on_telegram(rec)
        raise RuntimeError(f"Episode '{rec.episode_id}' sudah disetujui untuk tayang dan tidak bisa dijadikan cadangan.")
    if rec.status == "rejected":
        clear_audit_buttons_on_telegram(rec)
        raise RuntimeError(f"Episode '{rec.episode_id}' sudah ditolak.")

    clear_audit_buttons_on_telegram(rec)
    rec.status = "saved_as_backup"
    rec.decision_note = "Disimpan owner ke Bank Video Cadangan"
    rec.updated_at = datetime.now(WIB).isoformat(timespec="seconds")

    # Preserve pinned comment if episode state exists locally
    pinned_cmt: Optional[str] = None
    a7_p = BASE_DIR / "episodes" / rec.episode_id / "handoffs" / "agent7_package.json"
    if a7_p.exists():
        try:
            a7 = json.loads(a7_p.read_text(encoding="utf-8"))
            for cp in a7.get("platform_copies") or []:
                if cp.get("platform") == "youtube_shorts" and cp.get("pinned_comment"):
                    pinned_cmt = str(cp["pinned_comment"]).strip()
                    break
        except Exception:
            pass

    rec_dump = rec.model_dump()
    rec_dump["pinned_comment"] = pinned_cmt
    push_backup_video(rec_dump, reason="telegram_save_backup")

    # Clear active 'current' slot so 11:45 / 18:45 AI fallback will NOT publish this backup early
    store = load_pending_review_store()
    store["current"] = None
    hist = [h for h in (store.get("history") or []) if h.get("episode_id") != rec.episode_id]
    hist.append(rec.model_dump())
    store["history"] = hist[-120:]
    save_pending_review_store(store)

    stok = peek_backup_count()
    send_telegram_message(
        "\n".join([
            f"💾 [DISIMPAN JADI CADANGAN] `{rec.topic_title}`",
            f"• Status YouTube: Tetap *Unlisted* aman ({rec.youtube_url or '-'})",
            f"• Total stok cadangan: {stok} video",
            "• Video ini tidak akan dipublish jam 11:45/18:45 dan siap menggantikan jika ada jadwal gagal atau video ditolak.",
        ])
    )
    return {"ok": True, "episode_id": rec.episode_id, "backup_count": stok}


def promote_backup_to_slot(
    backup_item: dict[str, Any],
    *,
    target_publish_iso: Optional[str] = None,
    publish_mode: str = "draft",
    reason: str = "Fallback cadangan otomatis",
) -> dict[str, Any]:
    """Promote a standby backup video to the active publish/review slot."""
    pub_iso = target_publish_iso or compute_target_publish_iso()
    try:
        pub_dt = datetime.fromisoformat(pub_iso)
        pub_hhmm = pub_dt.strftime("%H:%M WIB")
        fallback_hhmm = (pub_dt - timedelta(minutes=15)).strftime("%H:%M WIB")
    except Exception:
        pub_dt = datetime.now(WIB)
        pub_hhmm = "19:00 WIB"
        fallback_hhmm = "18:45 WIB"

    ep_id = str(backup_item.get("episode_id") or "")
    title = str(backup_item.get("topic_title") or ep_id)
    yt_vid = backup_item.get("youtube_video_id")
    yt_url = backup_item.get("youtube_url")
    drive_url = backup_item.get("drive_url")

    if publish_mode == "public" and yt_vid:
        now_wib = datetime.now(WIB)
        if now_wib < pub_dt:
            upd = update_youtube_publish_status(
                str(yt_vid),
                privacy_status="private",
                publish_at_iso=pub_dt.isoformat(timespec="seconds"),
            )
            mode_label = f"Scheduled Private -> Tayang {pub_hhmm}"
        else:
            upd = update_youtube_publish_status(
                str(yt_vid),
                privacy_status="public",
                first_comment=backup_item.get("pinned_comment"),
            )
            mode_label = "Published Public Sekarang"

        from src.tools.weekly_growth_auditor import record_published_episode

        record_published_episode(
            episode_id=ep_id,
            title=title,
            discipline=str(backup_item.get("discipline") or "kimia"),
            section=str(backup_item.get("section") or "REGULER"),
            duration_sec=float(backup_item.get("duration_sec") or 0.0),
            publish_mode="public",
            youtube_url=yt_url,
            gdrive_folder_url=drive_url,
            source_url=backup_item.get("source_url"),
            topic_slug=str(backup_item.get("topic_slug") or ep_id),
            keywords=list(backup_item.get("keywords") or []),
            topic_bucket=str(backup_item.get("topic_bucket") or "everyday"),
        )
        send_telegram_message(
            "\n".join([
                f"🆘✅ [CADANGAN AKTIF & DIJADWALKAN] `{title}`",
                f"• Alasan: {reason}",
                f"• Status YouTube: {mode_label}",
                f"• YouTube: {yt_url or '-'}",
                f"• Sisa stok cadangan: {peek_backup_count()} video",
            ])
        )
        return {"ok": True, "promoted_episode_id": ep_id, "mode": mode_label, "youtube_update": upd}

    # Draft audit mode -> populate active pending_review.json & send audit buttons
    rec = PendingReviewRecord(
        episode_id=ep_id,
        topic_title=title,
        topic_slug=str(backup_item.get("topic_slug") or ep_id),
        source_url=backup_item.get("source_url"),
        keywords=list(backup_item.get("keywords") or []),
        topic_bucket=str(backup_item.get("topic_bucket") or "everyday"),
        discipline=str(backup_item.get("discipline") or "kimia"),
        section=str(backup_item.get("section") or "REGULER"),
        duration_sec=float(backup_item.get("duration_sec") or 0.0),
        references=list(backup_item.get("references") or []),
        youtube_video_id=yt_vid,
        youtube_url=yt_url,
        drive_url=drive_url,
        artifact_path_or_url=backup_item.get("artifact_path_or_url"),
        revision_count=0,
        qa_summary=f"{reason} | {backup_item.get('qa_summary') or 'QA OK'}",
        target_publish_iso=pub_iso,
        status="awaiting_review",
        decision_note=reason,
        reminder_sent=False,
        created_at=datetime.now(WIB).isoformat(timespec="seconds"),
    )

    token, chat_id = _bot_creds()
    keyboard = _build_inline_keyboard(ep_id, publish_time_label=pub_hhmm)
    msg_id: Optional[int] = None
    msg_txt = "\n".join([
        f"🆘🧪 [VIDEO CADANGAN DIAKTIFKAN] ({rec.section})",
        f"Alasan: {reason}",
        f"Judul: {title}",
        f"Target Tayang: {pub_hhmm} (Unlisted Draft siap tayang)",
        f"▶️ YouTube Unlisted: {yt_url or '-'}",
        f"📁 Google Drive: {drive_url or '-'}",
        f"📦 Sisa stok cadangan: {peek_backup_count()} video",
        "",
        f"Pilih aksi di bawah sebelum AI Chief Editor ambil alih otomatis ({fallback_hhmm}):",
    ])
    if token and chat_id:
        try:
            resp = requests.post(
                _api_url(token, "sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": msg_txt,
                    "reply_markup": keyboard,
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            j = resp.json() if resp.status_code == 200 else {}
            if j.get("ok"):
                msg_id = int((j.get("result") or {}).get("message_id") or 0) or None
        except Exception as exc:
            print(f"[backup] Gagal kirim preview cadangan ke Telegram: {exc}")

    rec.telegram_message_id = msg_id
    upsert_review_record(rec)
    return {"ok": True, "promoted_episode_id": ep_id, "status": "awaiting_review", "youtube_url": yt_url}


def handle_telegram_reject_use_backup(episode_id: str = "") -> dict[str, Any]:
    """Blacklist rejected topic, delete rejected YT draft, and immediately promote 1 standby backup video."""
    rec = get_review_record(episode_id)
    target_iso = (rec.target_publish_iso if rec else None) or compute_target_publish_iso()
    if rec:
        if rec.status in ("approved", "approved_by_ai"):
            clear_audit_buttons_on_telegram(rec)
            raise RuntimeError(f"Episode '{rec.episode_id}' sudah disetujui dan tidak bisa ditolak ulang.")
        clear_audit_buttons_on_telegram(rec)
        record_rejected_topic(
            episode_id=rec.episode_id,
            title=rec.topic_title,
            topic_slug=rec.topic_slug,
            source_url=rec.source_url,
            keywords=rec.keywords,
            reason="Ditolak via tombol Telegram (diganti video cadangan)",
        )
        if rec.youtube_video_id:
            delete_youtube_video(rec.youtube_video_id)
        rec.status = "rejected"
        rec.decision_note = "Ditolak user -> diganti video cadangan"
        upsert_review_record(rec)

    b_item = pop_next_backup_video()
    if not b_item:
        send_telegram_message("⚠️ Stok Bank Cadangan sedang kosong! Otomatis memproduksi topik baru sekarang...")
        from src.scheduler import run_one_automated_episode
        os.environ["PUBLISH_MODE"] = "draft"
        run_one_automated_episode()
        latest = get_review_record()
        return {"ok": True, "used_backup": False, "new_episode_id": latest.episode_id if latest else None}

    prom = promote_backup_to_slot(
        b_item,
        target_publish_iso=target_iso,
        publish_mode="draft",
        reason=f"Pengganti topik ditolak ({rec.topic_title if rec else episode_id})",
    )
    return {"ok": True, "used_backup": True, "rejected_episode_id": rec.episode_id if rec else episode_id, **prom}
