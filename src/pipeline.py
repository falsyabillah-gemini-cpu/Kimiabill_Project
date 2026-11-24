"""Kimiabill 6-Agent Content Automation Orchestrator.

Supports:
- Full automatic execution when API providers (gemini_api / claude_code_cli / anthropic_api / openrouter) are enabled.
- Antigravity Local trial mode (`antigravity_local`) where Antigravity runs each agent role via strict JSON handoffs
  in `episodes/<episode_id>/handoffs/`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional

import yaml

from src.memory import StudioMemory
from src.providers import call_structured_agent
from src.state import (
    Agent1Output,
    Agent2Output,
    Agent3Output,
    Agent5AuditOutput,
    Agent6Output,
    Agent7Output,
    EpisodeState,
    RenderPlan,
    TimelineSegment,
)
from src.tools.asset_scout import scout_assets_for_scenes
from src.tools.gdrive_sync import upload_episode_to_gdrive
from src.tools.mechanistic_animator import render_hybrid_science_video
from src.tools.qa_gate import run_gate0_checks
from src.tools.social_publisher import publish_to_all_platforms
from src.tools.thumbnail_maker import generate_cover_thumbnail
from src.tools.trend_scout import build_agent1_brief
from src.tools.tts_engine import generate_episode_voiceover
from src.tools.video_renderer import render_video

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(env_path: Optional[Path] = None) -> None:
    """Load key=value pairs from .env into os.environ without overwriting existing vars."""
    path = env_path or (BASE_DIR / ".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value


def load_configs() -> tuple[dict, dict]:
    brand = yaml.safe_load((BASE_DIR / "config" / "brand.yaml").read_text(encoding="utf-8"))
    agents = yaml.safe_load((BASE_DIR / "config" / "agents.yaml").read_text(encoding="utf-8"))
    return brand, agents


def episode_paths(episode_id: str) -> dict[str, Path]:
    ep_dir = BASE_DIR / "episodes" / episode_id
    handoffs = ep_dir / "handoffs"
    assets = ep_dir / "assets"
    renders = ep_dir / "renders"
    for d in (ep_dir, handoffs, assets, renders):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "ep_dir": ep_dir,
        "handoffs": handoffs,
        "assets": assets,
        "renders": renders,
        "state": ep_dir / "episode_state.json",
    }


def load_or_init_state(episode_id: str) -> EpisodeState:
    p = episode_paths(episode_id)["state"]
    if p.exists():
        return EpisodeState.model_validate_json(p.read_text(encoding="utf-8"))
    return EpisodeState(episode_id=episode_id)


def save_state(state: EpisodeState) -> Path:
    p = episode_paths(state.episode_id)["state"]
    p.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return p


def _collect_episode_references(state: EpisodeState) -> list[str]:
    """Assemble clean scientific references + real footage source credits for end-card & description."""
    refs: list[str] = []
    seen: set[str] = set()

    def add_ref(text: str) -> None:
        cleaned = " ".join(text.strip().split())
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            refs.append(cleaned)

    if state.agent3 and state.agent3.references:
        for r in state.agent3.references:
            add_ref(r)

    if state.agent1 and state.agent2:
        winner = next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if winner and winner.viral_source_url:
            add_ref(f"Sumber Riset: {winner.viral_source_url}")

    if not refs and state.agent3:
        add_ref(f"Referensi Sains Umum: {state.agent3.title}")

    if state.agent6 and state.agent6.assets:
        sources = sorted({a.source.replace("_", " ").title() for a in state.agent6.assets})
        add_ref(f"Footage Video: {', '.join(sources)} (Lisensi CC/Gratis)")

    return refs[:4]


def build_render_plan(
    state: EpisodeState,
    voice_path: str,
    scene_timings: list,
    subtitles: list,
    resolution_mode: str = "proxy_480p",
    version: int = 1,
    subtitle_font_size_ratio: float = 0.052,
    bgm_gain_db: float = -24.0,
    scene_offsets: Optional[dict[str, float]] = None,
) -> RenderPlan:
    assert state.agent3 is not None and state.agent6 is not None
    brand, _ = load_configs()
    paths = episode_paths(state.episode_id)
    asset_map = {a.scene_id: a for a in state.agent6.assets}
    scene_map = {s.scene_id: s for s in state.agent3.scenes}
    offsets = scene_offsets or {}

    winner_disc = "kimia"
    if state.agent1 and state.agent2:
        winner = next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if winner:
            winner_disc = winner.discipline

    segments: list[TimelineSegment] = []
    for seg_i, timing in enumerate(scene_timings):
        sc = scene_map[timing.scene_id]
        ast = asset_map[timing.scene_id]
        default_off = (
            round(min(1.2, max(0.0, ast.duration_sec - timing.duration_sec - 0.5)), 2)
            if seg_i == 0 and ast.duration_sec > timing.duration_sec + 1.5
            else 0.0
        )
        segments.append(
            TimelineSegment(
                scene_id=timing.scene_id,
                start_sec=timing.start_sec,
                end_sec=timing.end_sec,
                duration_sec=timing.duration_sec,
                footage_path=ast.footage_path,
                footage_start_offset=offsets.get(timing.scene_id, default_off),
                narration=sc.narration,
                overlay_badge=sc.overlay_badge,
                mechanism=sc.mechanism,
                sfx_path=ast.sfx_path,
                sfx_gain_db=-10.5,
                motion=sc.motion,
            )
        )

    if resolution_mode == "final_1080p":
        out_mp4 = paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    else:
        out_mp4 = paths["renders"] / f"proxy_v{version}_480p.mp4"

    from datetime import datetime
    from zoneinfo import ZoneInfo

    is_kitchen = _is_kitchen_chemistry_slot()
    is_karat = _is_weekend_karat_mode()
    section_tag = "KARAT" if is_karat else ("DAPUR" if is_kitchen else "REGULER")
    section_tagline = (
        "KARAT — Kabar Riset Terkini"
        if is_karat
        else ("KIMIA DAPUR • Sains Sehari-hari" if is_kitchen else "")
    )

    total_dur = segments[-1].end_sec if segments else 0.0
    use_mascot = bool(brand.get("video", {}).get("mascot", {}).get("enabled", True))
    return RenderPlan(
        version=version,
        resolution_mode=resolution_mode,
        title=state.agent3.title,
        discipline=winner_disc,
        section_tag=section_tag,
        section_tagline=section_tagline,
        requested_by=state.requested_by or getattr(state.agent3, "requested_by", None),
        use_mascot=use_mascot,
        references=_collect_episode_references(state),
        voice_path=voice_path,
        total_duration_sec=round(total_dur, 3),
        segments=segments,
        subtitles=subtitles,
        output_video_path=str(out_mp4),
        subtitle_font_size_ratio=subtitle_font_size_ratio,
        bgm_gain_db=bgm_gain_db,
    )


def _is_kitchen_chemistry_slot() -> bool:
    """Return True on the daily 12:00 WIB lunch-break slot for relatable Kitchen Chemistry / everyday science."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if os.environ.get("FORCE_KARAT_WEEKEND", "").lower() == "true":
        return False
    if os.environ.get("FORCE_KITCHEN_CHEMISTRY", "").lower() == "true":
        return True
    target_pub = os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
    if ":" in target_pub:
        try:
            return int(target_pub.split(":", 1)[0]) < 16
        except ValueError:
            pass
    return datetime.now(ZoneInfo("Asia/Jakarta")).hour < 16


def _is_weekend_karat_mode() -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if os.environ.get("FORCE_KARAT_WEEKEND", "").lower() == "true":
        return True
    if _is_kitchen_chemistry_slot():
        return False
    return datetime.now(ZoneInfo("Asia/Jakarta")).weekday() in (5, 6)


def run_agent_1_to_3(
    episode_id: str,
    topic_hint: Optional[str] = None,
    avoid_titles: Optional[list[str]] = None,
    requested_by: Optional[str] = None,
    request_id: Optional[str] = None,
    request_source_comment: Optional[str] = None,
    request_comment_id: Optional[str] = None,
) -> EpisodeState:
    brand, agents_cfg = load_configs()
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if requested_by:
        state.requested_by = requested_by
        state.request_id = request_id
        state.request_source_comment = request_source_comment
        state.request_comment_id = request_comment_id
    is_kitchen = _is_kitchen_chemistry_slot() and not (requested_by and topic_hint)
    is_karat = _is_weekend_karat_mode()

    # Pre-Agent 1 (0-Token): Scan viral pure-science trends, Kitchen Chemistry bank, or Weekend KARAT papers
    ideation_cfg = brand["brand"].get("ideation_policy", {})
    trend_brief = build_agent1_brief(
        BASE_DIR,
        lookback_hours=ideation_cfg.get("viral_lookback_hours", 96),
        min_viral_matches=ideation_cfg.get("min_viral_matches", 2),
        weekend_paper_mode=is_karat,
        kitchen_chemistry_mode=is_kitchen,
    )
    (paths["handoffs"] / "agent0_trend_brief.json").write_text(
        json.dumps(trend_brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Agent 1: Batch Ideation
    from src.tools.weekly_growth_auditor import load_latest_agent_directives

    from datetime import datetime
    from zoneinfo import ZoneInfo

    weekly_dir = load_latest_agent_directives()
    karat_dir = weekly_dir.get("karat_weekend_directives", []) if is_karat else []
    slot_map_hint: list[str] = []
    if weekly_dir.get("next_week_topic_map"):
        day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        today_name = day_names[datetime.now(ZoneInfo("Asia/Jakarta")).weekday()].lower()
        target_sec = "KARAT" if is_karat else ("DAPUR" if is_kitchen else "REGULER")
        for slot in weekly_dir["next_week_topic_map"]:
            if str(slot.get("day_label", "")).lower().startswith(today_name[:4]) and str(slot.get("section", "")).upper() == target_sec:
                slot_map_hint.append(
                    f"PETA TOPIK AGENT 8 ({slot.get('day_label')} {slot.get('slot_time_wib')} {target_sec}): "
                    f"Prioritaskan ide '{slot.get('planned_topic')}' ({slot.get('discipline')}) — alasan: {slot.get('rationale')}"
                )
                break
    a1_cfg = agents_cfg["agents"]["agent_1_ideation"]
    karat_a1_note = (
        "WEEKEND SPECIAL SECTION ACTIVE — 'KARAT (Kabar Riset Terkini)': Select and pitch 5 ideas centered on "
        "RECENT PEER-REVIEWED RESEARCH PAPERS or breaking scientific breakthroughs from the last 72 hours "
        "(Nature, Science, Phys.org, ScienceDaily, national/international science news) that bring cutting-edge "
        "discoveries from scientists directly to the Indonesian public ('Dari Saintis untuk Masyarakat'). "
        "PRIORITIZE papers matching Agent 8's `karat_weekend_directives` (derived from this week's hottest/most-engaged topics). "
        if is_karat
        else ""
    )
    kitchen_a1_note = (
        "DAILY SPECIAL SECTION ACTIVE — 'KIMIA DAPUR (Kitchen Chemistry & Sains Sehari-hari)': "
        "Select and pitch 5 hyper-relatable science ideas about COOKING, FOOD, DRINKS, or EVERYDAY KITCHEN/HOME HABITS "
        "(e.g., bawang, gorengan, kopi, es batu, daging empuk nanas, pedas cabai vs susu, telur, nasi dingin, teh jeruk nipis, sabun & minyak) "
        "backed by real chemistry/physics/food-science mechanisms! Pick unused topics from `idea_bank_fallback_candidates` "
        "or create equally relatable fresh Indonesian kitchen-science phenomena with concrete filmable kitchen footage. "
        if is_kitchen
        else ""
    )
    req_a1_note = (
        f"AUDIENCE COMMENT REQUEST PRIORITY: Viewer '{requested_by}' specifically requested: '{topic_hint}' "
        f"(comment: '{request_source_comment or topic_hint}'). You MUST put a fulfilling, accurate pure-science "
        f"Shorts idea for this exact request as Candidate #1! "
        if requested_by and topic_hint
        else ""
    )
    state.agent1 = call_structured_agent(
        provider=a1_cfg["provider"],
        model=a1_cfg.get("model", a1_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 1 (Viral Pure-Science Trend Scout) for Kimiabill. "
            "Cover strictly pure science phenomena across: kimia, fisika, matematika, medisinal, biologi. "
            f"{req_a1_note}{karat_a1_note}{kitchen_a1_note}"
            "PRIORITY 1: When in KARAT or REGULER mode and `viral_candidates` has fresh viral science topics/papers, turn them into "
            "engaging Indonesian short-video ideas (`source_mode: 'viral_trend'`). "
            "PRIORITY 2 (Daily Kitchen Chemistry / Fallback): When in KIMIA DAPUR mode or when no strong viral topic fits, select and enrich "
            "ideas from `idea_bank_fallback_candidates` (`source_mode: 'idea_bank'`), avoiding repeats. "
            "STORYTELLING RULE (Veritasium Principle): Angle every idea around a sharp COMMON MISCONCEPTION vs SURPRISING REALITY "
            "or a counterintuitive curiosity question so viewers immediately feel a knowledge gap worth closing. "
            "Every idea must be visualizable with REAL, concrete, filmable science/nature/kitchen footage (never full animation), "
            "MUST NOT repeat any topic in `avoid_titles`, and MUST apply any `weekly_growth_directives` from Agent 8."
        ),
        user_payload={
            "brand": brand["brand"],
            "section": "KARAT — Kabar Riset Terkini" if is_karat else ("KIMIA DAPUR — Sains Sehari-hari" if is_kitchen else "REGULER"),
            "trend_brief": trend_brief,
            "topic_hint": topic_hint,
            "requested_by": requested_by,
            "request_comment": request_source_comment,
            "avoid_titles": avoid_titles or [],
            "weekly_growth_directives": weekly_dir.get("agent_1_directives", []) + karat_dir + slot_map_hint,
        },
        response_model=Agent1Output,
        handoff_path=paths["handoffs"] / "agent1_ideas.json",
    )
    state.status = "ideated"
    save_state(state)

    # Agent 2: Editorial Auditor (Score 5 -> pick 1 winner + max 4 sharp improvements)
    a2_cfg = agents_cfg["agents"]["agent_2_editorial_auditor"]
    req_a2_note = (
        f"Because viewer '{requested_by}' requested '{topic_hint}', ensure the candidate answering that viewer request is selected cleanly and stays faithful to what they asked! "
        if requested_by and topic_hint
        else ""
    )
    state.agent2 = call_structured_agent(
        provider=a2_cfg["provider"],
        model=a2_cfg.get("model", a2_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 2 (Editorial & 70%+ Retention Hook Auditor) for Kimiabill. Score all candidates on: "
            "(1) first-3-second hook strength — must open an instant curiosity loop or challenge a misconception (NO dictionary definitions, "
            "NO slow greetings, NO 'pernahkah kamu berpikir' filler), "
            "(2) anti-over-explaining pacing — after the hook, move the explanation forward immediately without rambling, and "
            "(3) plain-language clarity + real-footage visual strength. "
            "Pick the #1 winner and give max 4 concrete improvement notes (including exact rewrite advice for scene_1 hook & open-loop pacing), "
            f"applying any `weekly_growth_directives` from Agent 8. {req_a2_note}"
        ),
        user_payload={
            "candidates": [c.model_dump() for c in state.agent1.candidates],
            "requested_by": requested_by,
            "topic_hint": topic_hint,
            "weekly_growth_directives": weekly_dir.get("agent_2_directives", []) + karat_dir,
        },
        response_model=Agent2Output,
        handoff_path=paths["handoffs"] / "agent2_audit.json",
    )
    state.status = "audited_idea"
    save_state(state)

    # Agent 3: Script & Shot-List Reviser
    a3_cfg = agents_cfg["agents"]["agent_3_script_reviser"]
    winner = next(
        (c for c in state.agent1.candidates if c.id == state.agent2.winner_id),
        state.agent1.candidates[0],
    )
    if winner.source_mode == "idea_bank":
        memory.mark_idea_used(winner.id, episode_id)
    karat_a3_note = (
        "THIS IS A WEEKEND 'KARAT — Kabar Riset Terkini' EPISODE: "
        "Open scene_1 narration with a crisp mention like 'Selamat datang di KARAT — Kabar Riset Terkini!' "
        "and clearly break down 1 latest scientific research paper/discovery (who/which institution discovered it, "
        "what mechanism they found, and why it matters to everyday society). In `references`, cite the exact "
        "journal / research institution / year & title. "
        if is_karat
        else ""
    )
    kitchen_a3_note = (
        "THIS IS A DAILY 'KIMIA DAPUR (Kitchen Chemistry)' EPISODE: "
        "Keep the explanation super relatable to Indonesian kitchens, cooking, food, or drinks at home, "
        "pair every step with appetizing/concrete real kitchen footage queries (e.g. 'chopping onion kitchen knife', "
        "'hot frying oil pan', 'pouring black coffee cup'), and end the science payoff with a crisp practical kitchen tip! "
        if is_kitchen
        else ""
    )
    spoken_requester = requested_by.lstrip("@") if requested_by else ""
    req_a3_note = (
        f"THIS EPISODE FULFILLS A VIEWER REQUEST FROM '{requested_by}' (comment: '{request_source_comment or topic_hint}'): "
        f"In scene_1's spoken narration, warmly mention the requester (e.g., 'Menjawab request dari {spoken_requester}!' or 'Halo {spoken_requester}, ini jawaban buat request kamu!'), "
        f"and right before the closing CTA mention thanking {spoken_requester} and inviting viewers to drop their own topic requests in the comments! "
        if requested_by
        else ""
    )
    state.agent3 = call_structured_agent(
        provider=a3_cfg["provider"],
        model=a3_cfg.get("model", a3_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 3 (Script, Shot-List & Mechanistic Storyboard Reviser) for Kimiabill. "
            "Write a cheerful, clear, moderate-pace Indonesian short script (~55-65s total spoken, 6-7 scenes, "
            "~18-24 words narration per scene so the viewer gets rich, deep scientific insight) applying Agent 2's notes "
            "and Agent 8's `weekly_growth_directives`. "
            f"{req_a3_note}{karat_a3_note}{kitchen_a3_note}"
            "PROVEN RETENTION & STORYTELLING STRUCTURE (Veritasium + Creator Insider 70%+ Retention):\n"
            "- Scene 1 (0-3s Hook): NO generic greeting or slow textbook definition! Start immediately with a punchy misconception-breaker "
            "or counterintuitive question that opens an irresistible curiosity loop.\n"
            "- Middle scenes (A-Plot ↔ B-Plot alternation & Zero Over-Explaining): Do NOT dump dry background jargon after the hook! "
            "Immediately advance the story by alternating between tangible real-world stakes/phenomena (A-plot) and how the hidden "
            "molecular/physical mechanism actually works (B-plot). End scene 2 or 3 with a crisp micro re-hook/open loop "
            "(e.g., 'Tapi bagian paling mengejutkannya justru terjadi saat...', 'Nah, di sinilah logika biasa mulai salah...') so viewers stay past detik ke-20.\n"
            "- Final scene: Close the main open loop with a satisfying 'Aha!' payoff, then include ONE brief casual comment CTA "
            "answerable in 1-3 words (e.g. 'Kamu tim baru tahu atau udah pernah dengar? Tulis di komentar atau request topik lain ya!'), "
            "and end cleanly with the exact closing CTA: 'Jangan lupa follow Kimiabill untuk info sains berikutnya!' \n"
            "VISUAL QUERY RULES: Each scene MUST specify an English real-footage visual_query (no cartoons/animations) that names a "
            "CONCRETE, FAMILY-SAFE, filmable object or lab process (e.g. 'gel cube compressed by hand in laboratory', "
            "'rust on iron chain close up'). NEVER use words about human bodies, skin, stretching people, clothing, "
            "dance, music, or vague single words; provide 2-3 equally concrete fallback_queries. "
            "For ALL middle explanation scenes (scenes 2 through second-to-last), ALWAYS provide BOTH `overlay_badge` AND `mechanism` "
            "so the renderer draws a REAL animated scientific diagram (NOT just text boxes!): choose `mechanism.kind` from "
            "'electron_transfer' (redox/electron flow arrows), 'polymer_crosslink' (chains bending & crosslinking into rigid lattice), "
            "'molecular_reaction' (bonds breaking/forming with curved arrows), 'receptor_binding' (enzyme/drug lock-and-key docking), "
            "'force_wave' (physics wave/force vectors), or 'math_geometry' (live mathematical curve/growth), and fill concise "
            "`title` (<=30 chars), `actor_a` & `actor_b` (<=14 chars, 1-2 crisp Indonesian words so cards NEVER truncate with '...'), `result` (<=20 chars), and `caption` matching the scene's exact mechanism. "
            "ALWAYS populate `references` with 1-3 real, accurate scientific references (journal, textbook, university, or source article)."
        ),
        user_payload={
            "winner_idea": winner.model_dump(),
            "audit_notes": state.agent2.improvement_notes,
            "voice_style": brand["voice"],
            "requested_by": requested_by,
            "request_comment": request_source_comment,
            "weekly_growth_directives": weekly_dir.get("agent_3_directives", []) + karat_dir,
        },
        response_model=Agent3Output,
        handoff_path=paths["handoffs"] / "agent3_script.json",
    )
    if requested_by and not state.agent3.requested_by:
        state.agent3.requested_by = requested_by
    state.status = "scripted"
    save_state(state)
    return state


def run_agent_6_scout(episode_id: str) -> EpisodeState:
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None:
        raise RuntimeError("Run Agent 1-3 first before scouting assets.")

    handoff_a6 = paths["handoffs"] / "agent6_assets.json"
    if handoff_a6.exists():
        state.agent6 = Agent6Output.model_validate_json(handoff_a6.read_text(encoding="utf-8"))
    else:
        tried_urls: set[str] = set()
        state.agent6 = scout_assets_for_scenes(
            scenes=state.agent3.scenes,
            assets_dir=paths["assets"],
            memory=memory,
            tried_urls_out=tried_urls,
        )
        handoff_a6.write_text(state.agent6.model_dump_json(indent=2), encoding="utf-8")
        if tried_urls:
            (paths["handoffs"] / "rejected_urls.json").write_text(
                json.dumps(sorted(tried_urls), indent=2), encoding="utf-8"
            )

    state.status = "assets_ready"
    save_state(state)
    return state


def rescout_affected_scenes(episode_id: str, affected_scenes: list[str]) -> EpisodeState:
    """Replace only the scenes flagged by Agent 5 QA with fresh, alternative real footage."""
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.agent6 is None:
        return state

    target_ids = {s for s in affected_scenes if s}
    if not target_ids:
        return state

    rejected_file = paths["handoffs"] / "rejected_urls.json"
    prev_rejected: set[str] = set()
    if rejected_file.exists():
        try:
            prev_rejected = set(json.loads(rejected_file.read_text(encoding="utf-8")))
        except Exception:
            prev_rejected = set()

    existing_map = {a.scene_id: a for a in state.agent6.assets}
    exclude_urls = prev_rejected | {a.source_url for a in state.agent6.assets}
    tried_urls: set[str] = set(exclude_urls)

    state.agent6 = scout_assets_for_scenes(
        scenes=state.agent3.scenes,
        assets_dir=paths["assets"],
        memory=memory,
        exclude_urls=exclude_urls,
        existing_assets=existing_map,
        target_scene_ids=target_ids,
        tried_urls_out=tried_urls,
    )
    rejected_file.write_text(json.dumps(sorted(tried_urls), indent=2), encoding="utf-8")
    (paths["handoffs"] / "agent6_assets.json").write_text(
        state.agent6.model_dump_json(indent=2), encoding="utf-8"
    )
    save_state(state)
    return state


def run_agent_4_proxy_and_gate0(
    episode_id: str,
    version: int = 1,
    subtitle_font_size_ratio: float = 0.052,
    bgm_gain_db: float = -24.0,
    scene_offsets: Optional[dict[str, float]] = None,
) -> tuple[EpisodeState, str, str]:
    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.agent6 is None:
        raise RuntimeError("Need Agent 3 script and Agent 6 assets before rendering.")

    if (
        version > 1
        and state.render_plan is not None
        and Path(state.render_plan.voice_path).exists()
        and len(state.render_plan.segments) == len(state.agent3.scenes)
    ):
        from src.tools.tts_engine import SceneVoiceResult

        voice_path = state.render_plan.voice_path
        scene_timings = [
            SceneVoiceResult(
                scene_id=seg.scene_id,
                audio_path=voice_path,
                start_sec=seg.start_sec,
                end_sec=seg.end_sec,
                duration_sec=seg.duration_sec,
                subtitles=[],
            )
            for seg in state.render_plan.segments
        ]
        subtitles = list(state.render_plan.subtitles)
        print(f"[tts] Memakai ulang audio voiceover dari v1 untuk revisi v{version} (hemat kuota ElevenLabs)")
    else:
        voice_cfg = brand["voice"]
        voice_path, scene_timings, subtitles, _ = generate_episode_voiceover(
            scenes=state.agent3.scenes,
            out_dir=paths["renders"] / f"audio_v{version}",
            voice=voice_cfg["voice_name"],
            rate=voice_cfg["rate"],
            pitch=voice_cfg["pitch"],
        )

    plan = build_render_plan(
        state=state,
        voice_path=voice_path,
        scene_timings=scene_timings,
        subtitles=subtitles,
        resolution_mode="proxy_480p",
        version=version,
        subtitle_font_size_ratio=subtitle_font_size_ratio,
        bgm_gain_db=bgm_gain_db,
        scene_offsets=scene_offsets,
    )
    use_mascot = bool(brand.get("video", {}).get("mascot", {}).get("enabled", True))
    video_path, sheet_path = render_hybrid_science_video(
        plan=plan,
        work_dir=paths["renders"],
        output_mp4=Path(plan.output_video_path),
        bgm_path=state.agent6.bgm_path,
        mascots_dir=BASE_DIR / "assets" / "mascots",
        use_mascot=use_mascot,
    )
    gate0 = run_gate0_checks(video_path, expected_width=480, expected_height=854)

    state.render_plan = plan
    state.gate0 = gate0
    state.status = "rendered_proxy"
    save_state(state)
    return state, video_path, sheet_path


def run_agent_5_video_qa(
    episode_id: str,
    sheet_path: str,
    version: int = 1,
) -> tuple[EpisodeState, Agent5AuditOutput]:
    """Strict multimodal Video QA Audit inspecting the rendered contact sheet + Gate 0 report."""
    brand, agents_cfg = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.render_plan is None or state.gate0 is None:
        raise RuntimeError("Render proxy and Gate 0 before running Agent 5 QA.")

    min_qa = float(brand.get("guardrails", {}).get("min_qa_score", 8.0))
    a5_cfg = agents_cfg["agents"]["agent_5_video_qa"]

    audit = call_structured_agent(
        provider=a5_cfg["provider"],
        model=a5_cfg.get("model", a5_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 5 (Strict Multimodal Video QA Auditor) for Kimiabill. "
            "Inspect the attached contact sheet image carefully alongside Gate 0 technical checks and script. "
            "Be RUTHLESS and objective — do NOT pass mediocre videos! Check EVERY item:\n"
            "0. SAFETY FIRST: Any nudity, suggestive, violent or otherwise inappropriate frame for a kids/teen science "
            "channel = automatic fail with revision_target 'asset_scout' and that scene in affected_scenes.\n"
            "1. Visual clarity & framing: Is the real footage crisp and un-zoomed (NOT blurry or overly cropped)? "
            "NOTE: landscape clips are INTENTIONALLY shown as a large full-width window over a blurred backdrop of "
            "the same clip — this is the house style, NOT a defect. Also judge whether each clip visually matches its narration "
            "and is free of watermarks/music-player/app UI.\n"
            "2. Mechanistic comic-science overlays: Are the animated mechanism diagrams clearly visible, with 100% crisp "
            "text contrast (NO white text spilling onto white card background, NO clipped/overflowing labels, NO mascot covering text)?\n"
            "3. Duo mascots: Are the Kimiabill mascots visible and cleanly positioned without blocking subtitles?\n"
            "4. Subtitle readability: Are Indonesian subtitles sharp, centered, and inside safe zone?\n"
            "5. End-card references: The LAST tile of the contact sheet labelled 'END-CARD' is the closing frame; "
            "check that it shows the 'REFERENSI & SUMBER' bibliography card.\n"
            "6. Gate 0 audio/video health: Must pass technical checks.\n"
            "7. Hook & Retention Pacing: Confirm scene_1 visual frame + narration deliver an immediate, scroll-stopping "
            "curiosity hook (zero generic filler) and middle scenes advance briskly without over-explaining.\n"
            f"Set `passed: true` ONLY if Gate 0 passed AND overall_score >= {min_qa} AND no major visual defects exist. "
            "If failing, specify exact `revision_target` ('asset_scout', 'editor', or 'both'), list the exact "
            "`affected_scenes` (e.g. ['scene_2']), and give up to 4 sharp `patch_instructions`."
        ),
        user_payload={
            "version_audited": version,
            "min_qa_score": min_qa,
            "gate0": state.gate0.model_dump(),
            "title": state.agent3.title,
            "references": state.render_plan.references,
            "weekly_growth_directives": (
                __import__("src.tools.weekly_growth_auditor", fromlist=["load_latest_agent_directives"])
                .load_latest_agent_directives()
                .get("agent_5_directives", [])
            ),
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_query": s.visual_query,
                    "overlay_badge": s.overlay_badge,
                }
                for s in state.agent3.scenes
            ],
        },
        response_model=Agent5AuditOutput,
        handoff_path=paths["handoffs"] / f"agent5_audit_v{version}.json",
        image_paths=[Path(sheet_path)],
    )

    # Enforce hard guardrail programmatically even if LLM was lenient
    if not state.gate0.passed or audit.overall_score < min_qa:
        audit.passed = False

    state.agent5_history.append(audit)
    state.revision_count = max(0, version - 1)
    if audit.passed:
        state.status = "qa_passed"
    save_state(state)
    return state, audit


def run_final_hd_render(episode_id: str) -> tuple[EpisodeState, str, str]:
    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.render_plan is None or state.agent3 is None or state.agent6 is None:
        raise RuntimeError("Render proxy and pass QA before final HD render.")

    final_plan = state.render_plan.model_copy(deep=True)
    final_plan.resolution_mode = "final_1080p"
    if state.agent7 and state.agent7.thumbnail_path and Path(state.agent7.thumbnail_path).exists():
        final_plan.cover_thumbnail_path = state.agent7.thumbnail_path
    final_plan.output_video_path = str(
        paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    )
    use_mascot = bool(brand.get("video", {}).get("mascot", {}).get("enabled", True))
    video_path, sheet_path = render_hybrid_science_video(
        plan=final_plan,
        work_dir=paths["renders"],
        output_mp4=Path(final_plan.output_video_path),
        bgm_path=state.agent6.bgm_path,
        mascots_dir=BASE_DIR / "assets" / "mascots",
        use_mascot=use_mascot,
    )
    gate0_hd = run_gate0_checks(video_path, expected_width=1080, expected_height=1920)
    state.render_plan = final_plan
    state.gate0 = gate0_hd
    state.final_video_path = video_path
    state.status = "rendered_final"
    save_state(state)

    memory = StudioMemory(BASE_DIR)
    last_score = state.agent5_history[-1].overall_score if state.agent5_history else None
    winner_cand = (
        next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if state.agent1 and state.agent2
        else None
    )
    winner_phenom = winner_cand.phenomenon if winner_cand else state.agent3.title
    winner_disc = winner_cand.discipline if winner_cand else "kimia"
    memory.record_episode(
        episode_id=episode_id,
        slug=state.agent3.episode_slug,
        title=state.agent3.title,
        phenomenon=winner_phenom,
        status=state.status,
        discipline=winner_disc,
        final_score=last_score,
    )
    return state, video_path, sheet_path


def run_upload(
    episode_id: str,
    allow_youtube: bool = True,
    qa_summary: str = "",
    topic_attempts_used: int = 1,
    production_seconds: Optional[float] = None,
) -> EpisodeState:
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    url = upload_episode_to_gdrive(
        paths["ep_dir"],
        episode_name=episode_id,
        allow_youtube=allow_youtube,
        qa_summary=qa_summary,
    )
    state.gdrive_folder_url = url
    yt_meta = paths["handoffs"] / "youtube_upload.json"
    if yt_meta.exists():
        try:
            state.youtube_url = json.loads(yt_meta.read_text(encoding="utf-8")).get("youtube_url")
        except Exception:
            pass
    if allow_youtube:
        state.status = "uploaded"
    save_state(state)

    if allow_youtube and state.agent3:
        from src.tools.weekly_growth_auditor import record_published_episode

        record_published_episode(
            episode_id=episode_id,
            title=state.agent3.title,
            discipline=state.render_plan.discipline if state.render_plan else "kimia",
            section=state.render_plan.section_tag if state.render_plan else "REGULER",
            youtube_url=state.youtube_url,
            gdrive_folder_url=state.gdrive_folder_url,
            qa_score=state.agent5_history[-1].overall_score if state.agent5_history else None,
            duration_sec=state.render_plan.total_duration_sec if state.render_plan else None,
            hook_line=state.agent3.scenes[0].narration if state.agent3.scenes else "",
            revisions_used=state.revision_count,
            topic_attempts_used=topic_attempts_used,
            production_seconds=production_seconds,
        )

    memory = StudioMemory(BASE_DIR)
    last_score = state.agent5_history[-1].overall_score if state.agent5_history else None
    if state.agent3:
        memory.record_episode(
            episode_id=episode_id,
            slug=state.agent3.episode_slug,
            title=state.agent3.title,
            phenomenon=state.agent3.title,
            status="uploaded",
            final_score=last_score,
            gdrive_url=url,
        )
    return state


def prepare_episode_thumbnail(episode_id: str) -> EpisodeState:
    """Run Agent 7 copy generation (if needed) and generate 9:16 cover thumbnail BEFORE final 1080p render."""
    brand, agents_cfg = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None:
        raise RuntimeError("Need Agent 3 script before preparing Agent 7 thumbnail.")

    if state.agent7 is None:
        a7_cfg = agents_cfg["agents"]["agent_7_publisher"]
        req_a7_note = (
            f"IMPORTANT — VIEWER REQUEST SHOUTOUT: This episode fulfills a comment request from '{state.requested_by}' "
            f"(comment: '{state.request_source_comment or ''}'). Mention '{state.requested_by}' clearly inside BOTH `caption` AND `pinned_comment` "
            f"thanking them, then ask a quick 1-3 word engagement question tied to the topic plus invite others to drop future science requests!"
            if state.requested_by
            else (
                "ENGAGEMENT CTA & PINNED COMMENT RULE: Both `caption` (ending) and especially `pinned_comment` MUST start with a casual, "
                "low-friction Indonesian question that casual viewers can answer in just 1-3 words (e.g., "
                "'Kamu tim yang baru tahu fakta ini atau udah pernah dengar? 👇' or 'Pilih A atau B, atau kasih skor 1-10 buat fenomena ini! 🤔'), "
                "tailored specifically to THIS video's science twist, then immediately add: "
                "'Atau mau bahas fenomena sains apa lagi minggu depan? Tulis singkat di komentar ya!' "
                "Keep `pinned_comment` warm, non-intimidating, and under 240 characters so viewers aren't shy to reply."
            )
        )
        state.agent7 = call_structured_agent(
            provider=a7_cfg["provider"],
            model=a7_cfg.get("model", a7_cfg["recommended_prod_model"]),
            system_prompt=(
                "You are Agent 7 (Packaging & Social Media Publisher) for @kimiabill. "
                "Create high-CTR thumbnail copy AND platform-tailored Indonesian title, high-engagement caption (with a quick 1-3 word poll/question CTA), "
                "hashtags, and a non-empty `pinned_comment` for every platform. "
                "THUMBNAIL COPY RULE: keep `thumbnail_badge` to 1-3 uppercase words (<=18 chars, e.g. 'RISET TERKINI' or 'SAINS MEDISINAL'), "
                "`thumbnail_headline_top` to 2-3 punchy words (<=18 chars, e.g. 'TEMBAKAU JADI'), and `thumbnail_headline_main` to 2-3 explosive trigger words "
                "(<=16 chars, e.g. 'OBAT KANKER?!') creating a curiosity gap instead of repeating the full video title. "
                f"{req_a7_note}"
            ),
            user_payload={
                "brand": brand["brand"],
                "script": state.agent3.model_dump(),
                "requested_by": state.requested_by,
                "request_source_comment": state.request_source_comment,
                "platforms": brand.get("publishing", {}).get(
                    "platforms", ["youtube_shorts", "tiktok", "instagram_reels"]
                ),
            },
            response_model=Agent7Output,
            handoff_path=paths["handoffs"] / "agent7_package.json",
        )

    pkg = state.agent7
    thumb_path = paths["ep_dir"] / f"THUMBNAIL_{state.agent3.episode_slug}_9x16.jpg"
    if not pkg.thumbnail_path or not Path(pkg.thumbnail_path).exists():
        if state.render_plan and state.render_plan.segments:
            bg_clip = Path(state.render_plan.segments[0].footage_path)
            bg_ts = state.render_plan.segments[0].footage_start_offset + 1.4
        elif state.agent6 and state.agent6.assets:
            bg_clip = Path(state.agent6.assets[0].footage_path)
            bg_ts = 1.6
        else:
            raise RuntimeError("Need footage clip to generate cover thumbnail.")
        use_mascot = brand.get("video", {}).get("mascot", {}).get("enabled", True)
        generate_cover_thumbnail(
            bg_video_path=bg_clip,
            bg_timestamp_sec=bg_ts,
            headline_top=pkg.thumbnail_headline_top,
            headline_main=pkg.thumbnail_headline_main,
            badge_text=pkg.thumbnail_badge,
            out_jpg=thumb_path,
            mascots_dir=BASE_DIR / "assets" / "mascots",
            use_mascot=use_mascot,
        )
        pkg.thumbnail_path = str(thumb_path)

    (paths["handoffs"] / "agent7_package.json").write_text(
        pkg.model_dump_json(indent=2), encoding="utf-8"
    )
    save_state(state)
    return state


def run_agent_7_publish(
    episode_id: str,
    mode: Optional[str] = None,
    video_override: Optional[str] = None,
) -> EpisodeState:
    import os

    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = prepare_episode_thumbnail(episode_id)
    assert state.agent3 is not None and state.agent7 is not None
    pkg = state.agent7

    publish_mode = mode or os.environ.get(
        "PUBLISH_MODE",
        brand.get("publishing", {}).get("default_mode", "draft"),
    )

    # Choose best final video (prefer Duo Mascot v2 if available)
    if video_override:
        final_video = Path(video_override)
    elif state.final_video_path and Path(state.final_video_path).exists():
        final_video = Path(state.final_video_path)
    else:
        candidates = sorted(paths["ep_dir"].glob("FINAL_*DUO_MASCOT*.mp4")) or sorted(
            paths["ep_dir"].glob("FINAL_*.mp4")
        )
        if not candidates:
            raise RuntimeError(f"No FINAL_*.mp4 found in {paths['ep_dir']}")
        final_video = candidates[-1]

    # Publish or package across platforms
    pkg.publish_results = publish_to_all_platforms(
        video_path=final_video,
        agent7_pkg=pkg,
        mode=publish_mode,  # type: ignore[arg-type]
    )
    (paths["handoffs"] / "agent7_package.json").write_text(
        pkg.model_dump_json(indent=2), encoding="utf-8"
    )
    state.agent7 = pkg
    if any(r.status in ("published", "uploaded_draft") for r in pkg.publish_results):
        state.status = "published"
    save_state(state)
    return state


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Kimiabill 7-Agent Automation Studio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scout-trends")

    for name in ("ideate", "scout", "render-proxy", "render-final", "upload", "publish"):
        p = sub.add_parser(name)
        p.add_argument("--episode-id", required=True)
        if name == "ideate":
            p.add_argument("--topic-hint", default=None)
        if name == "render-proxy":
            p.add_argument("--version", type=int, default=1)
            p.add_argument("--bgm-gain-db", type=float, default=-24.0)
            p.add_argument("--font-ratio", type=float, default=0.052)
        if name == "publish":
            p.add_argument("--mode", choices=["dry_run", "draft", "public"], default=None)
            p.add_argument("--video", default=None)

    args = parser.parse_args()
    if args.cmd == "scout-trends":
        brief = build_agent1_brief(BASE_DIR)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    elif args.cmd == "ideate":
        st = run_agent_1_to_3(args.episode_id, topic_hint=args.topic_hint)
        print(json.dumps({"status": st.status, "title": st.agent3.title if st.agent3 else None}, indent=2))
    elif args.cmd == "scout":
        st = run_agent_6_scout(args.episode_id)
        print(json.dumps({"status": st.status, "assets": len(st.agent6.assets) if st.agent6 else 0}, indent=2))
    elif args.cmd == "render-proxy":
        st, vpath, spath = run_agent_4_proxy_and_gate0(
            args.episode_id,
            version=args.version,
            subtitle_font_size_ratio=args.font_ratio,
            bgm_gain_db=args.bgm_gain_db,
        )
        print(json.dumps({"video": vpath, "contact_sheet": spath, "gate0": st.gate0.model_dump() if st.gate0 else None}, indent=2))
    elif args.cmd == "render-final":
        st, vpath, spath = run_final_hd_render(args.episode_id)
        print(json.dumps({"final_video": vpath, "contact_sheet": spath, "gate0": st.gate0.model_dump() if st.gate0 else None}, indent=2))
    elif args.cmd == "upload":
        st = run_upload(args.episode_id)
        print(json.dumps({"status": st.status, "gdrive_url": st.gdrive_folder_url}, indent=2))
    elif args.cmd == "publish":
        st = run_agent_7_publish(args.episode_id, mode=args.mode, video_override=args.video)
        print(json.dumps(st.agent7.model_dump() if st.agent7 else {}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
