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
    run_agent_1_to_3,
    run_agent_4_proxy_and_gate0,
    run_agent_6_scout,
    run_agent_7_publish,
    run_final_hd_render,
    run_upload,
)

WIB = ZoneInfo("Asia/Jakarta")


def run_one_automated_episode() -> None:
    load_dotenv()
    _, agents_cfg = load_configs()
    if agents_cfg.get("runtime_mode") == "antigravity_local":
        print(
            "[scheduler] runtime_mode masih 'antigravity_local'. "
            "Ubah runtime_mode di config/agents.yaml ke 'production' dan provider ke auto / gemini_api / anthropic_api / openrouter "
            "untuk menjalankan mode otomatis 24/7."
        )
        return

    now = datetime.now(WIB)
    episode_id = f"ep_{now.strftime('%Y%m%d_%H%M')}"
    print(f"[scheduler] Memulai produksi otomatis: {episode_id}")

    state = run_agent_1_to_3(episode_id)
    print(f"[scheduler] Agent 1-3 selesai: {state.agent3.title if state.agent3 else '-'}")

    state = run_agent_6_scout(episode_id)
    print(f"[scheduler] Agent 6 selesai: {len(state.agent6.assets) if state.agent6 else 0} klip real footage")

    state, proxy_video, _ = run_agent_4_proxy_and_gate0(episode_id, version=1)
    print(f"[scheduler] Agent 4 proxy + Gate 0: passed={state.gate0.passed if state.gate0 else False} ({proxy_video})")

    state, final_video, _ = run_final_hd_render(episode_id)
    print(f"[scheduler] Render final 1080p selesai: {final_video}")

    state = run_agent_7_publish(episode_id)
    print(f"[scheduler] Agent 7 selesai: {[r.model_dump() for r in (state.agent7.publish_results if state.agent7 else [])]}")

    if os.environ.get("AUTO_UPLOAD_GDRIVE", "true").lower() == "true":
        if shutil.which("gdrive"):
            state = run_upload(episode_id)
            print(f"[scheduler] Uploaded ke Google Drive: {state.gdrive_folder_url}")
        else:
            print("[scheduler] Melewati upload Google Drive (CLI 'gdrive' tidak terpasang di environment ini).")


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
