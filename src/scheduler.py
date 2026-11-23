"""24/7 Automated Scheduler for Kimiabill_videos (VPS / Docker).

Runs the full 6-agent pipeline automatically on a daily schedule (WIB / Asia/Jakarta)
even when your laptop is turned off.
"""
from __future__ import annotations

import os
import shutil
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from src.pipeline import (
    load_configs,
    load_dotenv,
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
from src.pipeline import load_or_init_state, episode_paths

from src.tools.gdrive_sync import can_upload_to_gdrive

WIB = ZoneInfo("Asia/Jakarta")


import re


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


def run_one_automated_episode() -> None:
    load_dotenv()
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
    avoid_titles: list[str] = []

    for topic_attempt in range(1, max_topic_attempts + 1):
        now = datetime.now(WIB)
        suffix = "" if topic_attempt == 1 else f"_t{topic_attempt}"
        episode_id = f"ep_{now.strftime('%Y%m%d_%H%M')}{suffix}"
        print(
            f"[scheduler] Memulai produksi otomatis (percobaan topik {topic_attempt}/{max_topic_attempts}): {episode_id}"
        )

        try:
            state = run_agent_1_to_3(episode_id, avoid_titles=avoid_titles)
            title = state.agent3.title if state.agent3 else "-"
            print(f"[scheduler] Agent 1-3 selesai: {title}")

            state = run_agent_6_scout(episode_id)
            print(f"[scheduler] Agent 6 selesai: {len(state.agent6.assets) if state.agent6 else 0} klip real footage")
        except Exception as exc:
            print(f"[scheduler] Percobaan topik #{topic_attempt} gagal di tahap skrip/footage: {exc}")
            traceback.print_exc()
            continue

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
            print(
                f"[scheduler] Topik '{title}' belum lolos QA setelah {max_revisions} revisi. "
                f"TIDAK di-upload ke Drive/YouTube; mengganti ke topik baru..."
            )
            state.status = "rejected_by_qa"
            save_state(state)
            if title and title != "-":
                avoid_titles.append(title)
            continue

        # QA PASSED! Render final 1080p HD and verify Gate 0 HD
        state, final_video, _ = run_final_hd_render(episode_id)
        hd_ok = bool(state.gate0 and state.gate0.passed)
        print(f"[scheduler] Render final 1080p selesai: {final_video} (Gate 0 HD passed={hd_ok})")
        if not hd_ok:
            print("[scheduler] Gate 0 HD gagal; mencoba topik berikutnya...")
            if title and title != "-":
                avoid_titles.append(title)
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

        if os.environ.get("AUTO_UPLOAD_GDRIVE", "true").lower() == "true":
            if can_upload_to_gdrive():
                state = run_upload(episode_id, allow_youtube=True, qa_summary=qa_summary)
                print(f"[scheduler] Uploaded ke Google Drive & YouTube Shorts: {state.gdrive_folder_url}")
            else:
                print("[scheduler] Melewati upload Google Drive (GDRIVE_WEBHOOK_URL belum diisi dan CLI 'gdrive' tidak terpasang).")
        return

    raise RuntimeError(
        f"Semua {max_topic_attempts} percobaan topik harian gagal lolos Agent 5 QA. "
        f"Tidak ada video yang di-upload ke Google Drive maupun YouTube."
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
