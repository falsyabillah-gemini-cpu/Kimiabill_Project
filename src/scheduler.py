"""24/7 Automated Scheduler for Kimiabill_videos (VPS / Docker).

Runs the full 6-agent pipeline automatically on a daily schedule (WIB / Asia/Jakarta)
even when your laptop is turned off.
"""
from __future__ import annotations

import os
import re
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from src.pipeline import (
    load_configs,
    load_dotenv,
    prepare_episode_thumbnail,
    rescout_affected_scenes,
    run_agent_1_to_3,
    run_agent_4_proxy_and_gate0,
    run_agent_5_video_qa,
    run_agent_6_scout,
    run_agent_7_publish,
    run_final_hd_render,
    run_upload,
    save_state,
)

from src.tools.gdrive_sync import can_upload_to_gdrive

WIB = ZoneInfo("Asia/Jakarta")


def _extract_affected_scenes(audit, valid_scene_ids: list[str]) -> list[str]:
    """Collect affected scene IDs from audit.affected_scenes + regex scan of issues/instructions."""
    found: list[str] = []
    valid_set = set(valid_scene_ids)
    for sc in audit.affected_scenes or []:
        sc_norm = sc.strip().lower().replace(" ", "_")
        if sc_norm in valid_set and sc_norm not in found:
            found.append(sc_norm)
    blob = " ".join((audit.issues or []) + (audit.patch_instructions or []))
    for m in re.finditer(r"scene[\s_]*(\d+)", blob, flags=re.IGNORECASE):
        sc_norm = f"scene_{m.group(1)}"
        if sc_norm in valid_set and sc_norm not in found:
            found.append(sc_norm)
    return found


def _tg_status(message: str) -> None:
    """Best-effort live Telegram status update during automated production (never raises)."""
    try:
        from src.tools.telegram_notify import send_telegram_message, telegram_enabled

        if telegram_enabled():
            send_telegram_message(message)
    except Exception:
        pass


def _redact_url_secrets(msg: str) -> str:
    return re.sub(r"([?&](?:key|token)=)[^&\s'\"]+", r"\1***", msg)


def run_one_automated_episode() -> None:
    load_dotenv()
    try:
        from src.tools.telegram_notify import sync_telegram_feedback_updates

        sync_res = sync_telegram_feedback_updates()
        if sync_res.get("saved_count"):
            print(f"[telegram] Menyerap {sync_res['saved_count']} masukan baru dari Telegram untuk video ini & berikutnya!")
    except Exception as exc:
        print(f"[telegram] Peringatan cek feedback Telegram: {exc}")

    brand_cfg, agents_cfg = load_configs()
    if agents_cfg.get("runtime_mode") == "antigravity_local":
        print(
            "[scheduler] runtime_mode masih 'antigravity_local'. "
            "Ubah runtime_mode di config/agents.yaml ke 'production' dan provider ke auto / gemini_api / anthropic_api / openrouter "
            "untuk menjalankan mode otomatis 24/7."
        )
        return

    guardrails = brand_cfg.get("guardrails", {})
    max_revisions = int(guardrails.get("max_revisions", 4))
    max_topic_attempts = int(guardrails.get("max_topic_attempts", 3))
    avoid_titles: list[str] = [
        "Sunscreen Rahasia DNA yang Bikin Kehidupan Purba Selamat dari Radiasi!",
    ]
    try:
        from src.tools.weekly_growth_auditor import HISTORY_PATH
        import json as _json

        if HISTORY_PATH.exists():
            hist_items = _json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            for row in hist_items[-16:]:
                t = (row.get("title") or "").strip()
                if t and t not in avoid_titles:
                    avoid_titles.append(t)
            if os.environ.get("SKIP_IF_SLOT_PUBLISHED", "").lower() == "true":
                now_wib = datetime.now(WIB)
                target_pub_env = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
                slot_evening = (
                    int(target_pub_env.split(":", 1)[0]) >= 16
                    if ":" in target_pub_env
                    else now_wib.hour >= 16
                )
                for row in reversed(hist_items):
                    pub_at = row.get("published_at_wib") or ""
                    has_slot_proof = (
                        bool(row.get("youtube_url"))
                        if row.get("publish_mode", "public") != "dry_run"
                        else bool(row.get("gdrive_folder_url"))
                    )
                    if (
                        pub_at.startswith(now_wib.strftime("%Y-%m-%d"))
                        and row.get("publish_mode", "public") == "public"
                        and has_slot_proof
                    ):
                        try:
                            pub_hour = datetime.fromisoformat(pub_at).hour
                            if (pub_hour >= 16) == slot_evening:
                                print(
                                    f"[scheduler] Slot hari ini ({'malam' if slot_evening else 'siang'}) "
                                    f"sudah terbit ({row.get('youtube_url') or row.get('gdrive_folder_url')}) — melewati duplikasi cron."
                                )
                                return
                        except Exception:
                            pass
    except Exception:
        pass

    # Check if Agent 8 queued a viewer comment request to fulfill on weekday evening (REGULER) slots
    pending_req: dict | None = None
    try:
        from src.pipeline import _is_kitchen_chemistry_slot, _is_weekend_karat_mode
        from src.tools.weekly_growth_auditor import get_next_pending_audience_request

        if not _is_weekend_karat_mode() and not _is_kitchen_chemistry_slot():
            pending_req = get_next_pending_audience_request()
            if pending_req:
                print(
                    f"[scheduler] Menjalankan REQUEST PENONTON dari {pending_req.get('requester_name')}: "
                    f"{pending_req.get('topic_title')}"
                )
    except Exception:
        pending_req = None

    run_t0 = time.monotonic()
    max_recovery_rounds = max(1, int(os.environ.get("MAX_RECOVERY_ROUNDS", "2")))
    max_wall_clock_sec = float(os.environ.get("MAX_PRODUCTION_WALL_SEC", str(120 * 60)))

    last_err = "Error tidak diketahui"
    total_attempts_done = 0
    consecutive_quota_errors = 0

    for recovery_round in range(1, max_recovery_rounds + 1):
        for topic_attempt in range(1, max_topic_attempts + 1):
            if (time.monotonic() - run_t0) > max_wall_clock_sec:
                break
            total_attempts_done += 1
            try:
                from src.providers import reset_model_usage_log

                reset_model_usage_log()
            except Exception:
                pass
            now = datetime.now(WIB)
            ep_suffix = "" if (recovery_round == 1 and topic_attempt == 1) else f"_r{recovery_round}t{topic_attempt}"
            episode_id = f"ep_{now.strftime('%Y%m%d_%H%M')}{ep_suffix}"
            print(
                f"[scheduler] Memulai produksi otomatis "
                f"(ronde {recovery_round}/{max_recovery_rounds}, percobaan topik {topic_attempt}/{max_topic_attempts}): {episode_id}"
            )
            if recovery_round == 1 and topic_attempt == 1:
                try:
                    from src.pipeline import _is_kitchen_chemistry_slot, _is_weekend_karat_mode
                    from src.tools.telegram_notify import notify_production_started

                    sec_lbl = (
                        "KARAT — Riset Terkini"
                        if _is_weekend_karat_mode()
                        else ("KIMIA DAPUR" if _is_kitchen_chemistry_slot() else "REGULER")
                    )
                    sent_start = notify_production_started(
                        episode_id=episode_id,
                        section_label=sec_lbl,
                        pending_request=pending_req,
                    )
                    print(f"[telegram] Notifikasi #1 (Mulai Produksi) terkirim (ok={sent_start})")
                except Exception as exc:
                    print(f"[telegram] Peringatan gagal kirim notif mulai produksi: {exc}")

            title = "-"
            try:
                use_req = pending_req if (recovery_round == 1 and topic_attempt == 1) else None
                state = run_agent_1_to_3(
                    episode_id,
                    topic_hint=use_req.get("topic_title") if use_req else None,
                    avoid_titles=avoid_titles,
                    requested_by=use_req.get("requester_name") if use_req else None,
                    request_id=use_req.get("request_id") if use_req else None,
                    request_source_comment=use_req.get("source_comment") if use_req else None,
                    request_comment_id=use_req.get("source_comment_id") if use_req else None,
                )
                consecutive_quota_errors = 0
                title = state.agent3.title if state.agent3 else "-"
                print(f"[scheduler] Agent 1-3 selesai: {title}")
                _tg_status(
                    f"📝 [Kimiabill] Skrip siap (Ronde {recovery_round}, Percobaan #{topic_attempt}):\n"
                    f"\"{title}\"\n"
                    f"⏳ Sedang cari footage, TTS, animasi & QA render..."
                )

                state = run_agent_6_scout(episode_id)
                print(f"[scheduler] Agent 6 selesai: {len(state.agent6.assets) if state.agent6 else 0} klip real footage")

                font_ratio = 0.052
                scene_offsets: dict[str, float] = {}
                qa_passed = False
                last_audit = None
                valid_scene_ids = [s.scene_id for s in (state.agent3.scenes if state.agent3 else [])]

                for version in range(1, max_revisions + 2):
                    state, proxy_video, sheet_path = run_agent_4_proxy_and_gate0(
                        episode_id,
                        version=version,
                        subtitle_font_size_ratio=font_ratio,
                        scene_offsets=scene_offsets,
                    )
                    print(
                        f"[scheduler] Agent 4 proxy v{version} + Gate 0: "
                        f"passed={state.gate0.passed if state.gate0 else False} ({proxy_video})"
                    )

                    state, audit = run_agent_5_video_qa(episode_id, sheet_path=sheet_path, version=version)
                    last_audit = audit
                    print(
                        f"[scheduler] Agent 5 QA v{version}: passed={audit.passed}, "
                        f"score={audit.overall_score}/10 (visual={audit.visual_relevance_score}, "
                        f"sub={audit.subtitle_readability_score}, audio={audit.pacing_and_audio_score}), "
                        f"target={audit.revision_target}, issues={audit.issues}"
                    )

                    if audit.passed:
                        qa_passed = True
                        break

                    if version <= max_revisions:
                        print(f"[scheduler] Audit ditolak -> menjalankan revisi #{version}/{max_revisions}...")
                        affected = _extract_affected_scenes(audit, valid_scene_ids)
                        need_rescout = (
                            audit.revision_target in ("asset_scout", "both")
                            or audit.visual_relevance_score < 8.0
                        )
                        if need_rescout:
                            target_scenes = affected or valid_scene_ids[1:-1] or valid_scene_ids
                            try:
                                state = rescout_affected_scenes(episode_id, target_scenes)
                                print(f"[scheduler] Agent 6 mengganti footage untuk scene: {target_scenes}")
                            except Exception as exc:
                                print(f"[scheduler] Re-scout gagal untuk {target_scenes}: {exc}")
                        if audit.revision_target in ("editor", "both") or not need_rescout:
                            for sc_id in affected or valid_scene_ids:
                                scene_offsets[sc_id] = round(scene_offsets.get(sc_id, 0.0) + 2.0, 2)
                            font_ratio = 0.050 if audit.subtitle_readability_score < 8.0 else font_ratio

                if not qa_passed:
                    last_err = f"Topik '{title}' belum lolos QA setelah {max_revisions} revisi"
                    print(f"[scheduler] {last_err}. TIDAK di-upload ke Drive/YouTube; mengganti ke topik baru...")
                    state.status = "rejected_by_qa"
                    save_state(state)
                    if title and title != "-":
                        avoid_titles.append(title)
                    _tg_status(f"⚠️ [Kimiabill] Percobaan #{total_attempts_done} gagal QA ({title}). Otomatis mengulang dengan topik baru...")
                    continue

                # QA PASSED! Generate poster thumbnail first so it is embedded cleanly at frame 0 of final 1080p MP4
                try:
                    state = prepare_episode_thumbnail(episode_id)
                except Exception as exc:
                    print(f"[scheduler] Peringatan gagal prepare cover thumbnail sebelum render final: {exc}")
                state, final_video, _ = run_final_hd_render(episode_id)
                hd_ok = bool(state.gate0 and state.gate0.passed)
                print(f"[scheduler] Render final 1080p selesai: {final_video} (Gate 0 HD passed={hd_ok})")
                if not hd_ok:
                    last_err = f"Gate 0 HD gagal pada '{title}'"
                    print(f"[scheduler] {last_err}; mencoba topik berikutnya...")
                    if title and title != "-":
                        avoid_titles.append(title)
                    _tg_status(f"⚠️ [Kimiabill] Percobaan #{total_attempts_done} gagal Gate 0 HD ({title}). Otomatis mengulang...")
                    continue

                qa_summary = (
                    f"LOLOS AUDIT QA | Skor Akhir: {last_audit.overall_score}/10 | "
                    f"Visual: {last_audit.visual_relevance_score} | Subtitle: {last_audit.subtitle_readability_score} | "
                    f"Audio: {last_audit.pacing_and_audio_score} | Revisi: {state.revision_count}x"
                    if last_audit
                    else "LOLOS AUDIT QA"
                )

                state = run_agent_7_publish(episode_id)
                print(f"[scheduler] Agent 7 selesai: {[r.model_dump() for r in (state.agent7.publish_results if state.agent7 else [])]}")

                prod_elapsed = time.monotonic() - run_t0

                # Wait for prime-time publish window (uses Agent 8's optimal weekend hours on Sat/Sun when available)
                target_pub = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
                if target_pub and datetime.now(WIB).weekday() in (5, 6):
                    try:
                        from src.tools.weekly_growth_auditor import load_latest_agent_directives

                        rec_hours = load_latest_agent_directives().get("recommended_weekend_hours_wib") or []
                        is_evening_slot = int(target_pub.split(":", 1)[0]) >= 16
                        picked = [h for h in rec_hours if ":" in h and (int(h.split(":", 1)[0]) >= 16) == is_evening_slot]
                        if picked:
                            target_pub = picked[0].strip()
                            print(f"[scheduler] Edisi Weekend KARAT memakai jam ter-rame hasil audit Agent 8: {target_pub} WIB")
                    except Exception:
                        pass
                if target_pub and ":" in target_pub:
                    try:
                        th, tm = (int(x) for x in target_pub.split(":", 1))
                        now_wib = datetime.now(WIB)
                        target_dt = now_wib.replace(hour=th, minute=tm, second=0, microsecond=0)
                        # Start upload ~90s early so Drive+YouTube completes right on target HH:MM WIB
                        wait_sec = (target_dt - now_wib).total_seconds() - 90.0
                        if 0 < wait_sec <= 9000:
                            print(
                                f"[scheduler] Video sudah siap & lolos QA! Menunggu jendela upload {target_pub} WIB "
                                f"({int(wait_sec // 60)}m {int(wait_sec % 60)}s lagi)..."
                            )
                            _tg_status(
                                f"⏳ [Kimiabill] Video \"{title}\" lolos QA ({last_audit.overall_score if last_audit else 9.0}/10)! "
                                f"Menunggu jendela tayang {target_pub} WIB ({int(wait_sec // 60)} menit lagi)..."
                            )
                            time.sleep(wait_sec)
                    except Exception as exc:
                        print(f"[scheduler] Mengabaikan TARGET_PUBLISH_TIME_WIB='{target_pub}': {exc}")

                if os.environ.get("AUTO_UPLOAD_GDRIVE", "true").lower() == "true":
                    if can_upload_to_gdrive():
                        state = run_upload(
                            episode_id,
                            allow_youtube=True,
                            qa_summary=qa_summary,
                            topic_attempts_used=total_attempts_done,
                            production_seconds=prod_elapsed,
                        )
                        print(f"[scheduler] Uploaded ke Google Drive & YouTube Shorts: {state.gdrive_folder_url} | YouTube: {state.youtube_url}")
                        pub_mode_now = os.environ.get("PUBLISH_MODE", "public").strip().lower()
                        if pub_mode_now in ("public", "draft") and not state.youtube_url:
                            raise RuntimeError(
                                f"Video '{episode_id}' berhasil masuk Google Drive ({state.gdrive_folder_url}), "
                                f"tetapi auto-upload YouTube GAGAL (youtube_url kosong). Cek log [gdrive]/[youtube] di atas."
                            )
                        if (
                            state.request_id
                            and pub_mode_now == "public"
                            and state.status == "uploaded"
                            and state.youtube_url
                        ):
                            try:
                                from src.tools.weekly_growth_auditor import mark_audience_request_fulfilled

                                mark_audience_request_fulfilled(
                                    state.request_id,
                                    episode_id,
                                    youtube_url=state.youtube_url,
                                )
                                print(f"[scheduler] Request penonton '{state.request_id}' ({state.requested_by}) ditandai selesai!")
                            except Exception:
                                pass
                    else:
                        print("[scheduler] Melewati upload Google Drive (GDRIVE_WEBHOOK_URL belum diisi dan CLI 'gdrive' tidak terpasang).")
                return
            except Exception as exc:
                raw_msg = str(exc) or exc.__class__.__name__
                last_err = _redact_url_secrets(raw_msg)
                print(f"[scheduler] Percobaan #{total_attempts_done} (ronde {recovery_round}.{topic_attempt}) gagal: {last_err}")
                traceback.print_exc()
                if title and title != "-" and title not in avoid_titles:
                    avoid_titles.append(title)
                is_quota_err = ("429" in last_err or "quota" in last_err.lower() or "kuota" in last_err.lower() or "503" in last_err)
                if is_quota_err:
                    consecutive_quota_errors += 1
                    if consecutive_quota_errors >= 2:
                        _tg_status(
                            "🛑 [Kimiabill] Kuota Gemini API habis (429/503 beruntun setelah jeda RPM).\n"
                            "Produksi dihentikan sementara agar kuota pulih pada jadwal berikutnya."
                        )
                        raise RuntimeError(f"Kuota Gemini API habis beruntun: {last_err}") from exc
                    _tg_status(
                        f"⏳ [Kimiabill] Percobaan #{total_attempts_done} kena limit Gemini (429). "
                        f"Menunggu 75 detik agar kuota RPM pulih sebelum mencoba ulang 1x lagi..."
                    )
                    time.sleep(75)
                else:
                    consecutive_quota_errors = 0
                    _tg_status(
                        f"⚠️ [Kimiabill] Percobaan #{total_attempts_done} gagal: {last_err[:160]}\n"
                        f"Otomatis mengulang dengan topik baru..."
                    )
                continue

        if recovery_round < max_recovery_rounds and (time.monotonic() - run_t0) < max_wall_clock_sec:
            cooldown_s = min(180, 75 * recovery_round)
            print(f"[scheduler] Ronde pemulihan {recovery_round} habis; jeda {cooldown_s}s sebelum ronde {recovery_round + 1}...")
            _tg_status(
                f"🔄 [Kimiabill] Ronde {recovery_round}/{max_recovery_rounds} gagal ({last_err[:140]}). "
                f"Jeda {cooldown_s} detik lalu lanjut ronde {recovery_round + 1}..."
            )
            time.sleep(cooldown_s)

    _tg_status(
        f"❌ [Kimiabill] PRODUKSI DIHENTIKAN setelah {total_attempts_done} percobaan ({max_recovery_rounds} ronde).\n"
        f"Error terakhir: {last_err[:220]}"
    )
    raise RuntimeError(
        f"Semua {total_attempts_done} percobaan produksi otomatis ({max_recovery_rounds} ronde) gagal: {last_err}"
    )


def main() -> None:
    load_dotenv()
    times_raw = os.environ.get("SCHEDULE_TIMES_WIB", "08:00")
    target_times = {t.strip() for t in times_raw.split(",") if t.strip()}
    last_ran_key = ""
    print(f"[scheduler] Kimiabill_videos 24/7 aktif. Jadwal harian (WIB): {sorted(target_times)}")

    while True:
        now = datetime.now(WIB)
        hhmm = now.strftime("%H:%M")
        run_key = now.strftime("%Y-%m-%d_%H:%M")
        if hhmm in target_times and run_key != last_ran_key:
            last_ran_key = run_key
            try:
                run_one_automated_episode()
            except Exception:
                traceback.print_exc()
        time.sleep(20)


if __name__ == "__main__":
    main()
