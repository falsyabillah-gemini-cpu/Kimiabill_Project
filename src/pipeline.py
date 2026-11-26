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


def _collect_episode_references(state: EpisodeState, *, check_http: bool = True) -> list[str]:
    """Assemble HTTP-verified scientific references for YouTube description (never rendered as static video end-card)."""
    from src.tools.reference_verifier import verify_and_clean_references

    raw_refs: list[str] = []
    if state.agent3 and state.agent3.references:
        raw_refs.extend(state.agent3.references)

    if state.agent1 and state.agent2:
        winner = next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if winner and winner.viral_source_url:
            raw_refs.append(f"Sumber Riset: {winner.viral_source_url}")

    cleaned = verify_and_clean_references(raw_refs, check_http=check_http)
    if not cleaned and state.agent3:
        cleaned = [f"Editorial Sains Kimiabill: {state.agent3.title}"]
    return cleaned[:4]


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

    try:
        from src.tools.telegram_notify import get_owner_runtime_overrides

        ro = get_owner_runtime_overrides()
    except Exception:
        ro = {}

    from src.tools.asset_scout import resolve_scene_sfx_cues

    memory = StudioMemory(BASE_DIR)
    force_trans_sfx = bool(ro.get("force_sfx_every_transition", True))
    force_anim_sfx = bool(ro.get("force_sfx_every_animation", True))
    base_sfx_db = float(brand.get("audio", {}).get("sfx_gain_db", -10.5))
    sfx_gain_db = round(max(-18.0, min(-4.0, base_sfx_db + float(ro.get("sfx_gain_db_delta") or 0.0))), 2)
    eff_bgm_gain_db = round(max(-34.0, min(-14.0, float(bgm_gain_db) + float(ro.get("bgm_gain_db_delta") or 0.0))), 2)
    eff_sub_ratio = round(max(0.038, min(0.068, float(subtitle_font_size_ratio) + float(ro.get("subtitle_scale_delta") or 0.0))), 4)

    winner_disc = "kimia"
    if state.agent1 and state.agent2:
        winner = next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
        if winner:
            winner_disc = winner.discipline

    segments: list[TimelineSegment] = []
    total_segs = len(scene_timings)
    for seg_i, timing in enumerate(scene_timings):
        sc = scene_map[timing.scene_id]
        ast = asset_map[timing.scene_id]
        res_trans, res_anim = resolve_scene_sfx_cues(
            scene_idx=seg_i,
            total_scenes=total_segs,
            scene=sc,
            memory=memory,
            force_transition=force_trans_sfx,
            force_animation=force_anim_sfx,
        )
        final_trans_sfx = ast.sfx_path if (ast.sfx_path and Path(ast.sfx_path).exists()) else res_trans
        final_anim_sfx = (
            ast.animation_sfx_path
            if (getattr(ast, "animation_sfx_path", None) and Path(str(ast.animation_sfx_path)).exists())
            else res_anim
        )
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
                sfx_path=final_trans_sfx,
                animation_sfx_path=final_anim_sfx,
                sfx_gain_db=sfx_gain_db,
                motion=sc.motion,
            )
        )

    if resolution_mode == "final_1080p":
        out_mp4 = paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    else:
        out_mp4 = paths["renders"] / f"proxy_v{version}_480p.mp4"

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
        references=_collect_episode_references(state, check_http=(resolution_mode == "final_1080p")),
        voice_path=voice_path,
        total_duration_sec=round(total_dur, 3),
        segments=segments,
        subtitles=subtitles,
        output_video_path=str(out_mp4),
        subtitle_font_size_ratio=eff_sub_ratio,
        bgm_gain_db=eff_bgm_gain_db,
    )


def _is_kitchen_chemistry_slot() -> bool:
    """Return True on the daily noon lunch-break slot for relatable Kitchen Chemistry / everyday science."""
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


def _sanitize_agent3_output(agent3: Agent3Output) -> Agent3Output:
    """Apply clean Indonesian proofreading & enforce YouTube CTA word rules deterministically."""
    from src.tools.tts_engine import sanitize_indonesian_script

    agent3.title = sanitize_indonesian_script(agent3.title)
    agent3.caption_social = sanitize_indonesian_script(agent3.caption_social)
    for sc in agent3.scenes:
        sc.narration = sanitize_indonesian_script(sc.narration)
        if sc.overlay_badge:
            sc.overlay_badge = sanitize_indonesian_script(sc.overlay_badge)
    return agent3


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

    # Pre-Agent 1 (0-Token): Scan viral pure-science trends with strict published/rejected deduplication + 70/30 split
    ideation_cfg = brand["brand"].get("ideation_policy", {})
    trend_brief = build_agent1_brief(
        BASE_DIR,
        lookback_hours=ideation_cfg.get("viral_lookback_hours", 96),
        min_viral_matches=ideation_cfg.get("min_viral_matches", 2),
        weekend_paper_mode=is_karat,
        kitchen_chemistry_mode=is_kitchen,
        extra_avoid_titles=avoid_titles,
    )
    (paths["handoffs"] / "agent0_trend_brief.json").write_text(
        json.dumps(trend_brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from src.tools.telegram_notify import (
        get_agent_specific_owner_directives,
        get_owner_runtime_overrides,
    )
    from src.tools.topic_guard import check_topic_duplication
    from src.tools.weekly_growth_auditor import load_latest_agent_directives

    from datetime import datetime
    from zoneinfo import ZoneInfo

    weekly_dir = load_latest_agent_directives()
    owner_ro = get_owner_runtime_overrides()
    owner_a1 = get_agent_specific_owner_directives("agent_1")
    owner_a2 = get_agent_specific_owner_directives("agent_2")
    owner_a3 = get_agent_specific_owner_directives("agent_3")
    karat_dir = weekly_dir.get("karat_weekend_directives", []) if is_karat else []
    ratio_g = trend_brief.get("topic_ratio_guidance") or {}
    preferred_bucket = ratio_g.get("preferred_next_bucket", "everyday")

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
        "WEEKEND SPECIAL SECTION ACTIVE — 'KARAT (Kabar Riset Terkini)': Include strong ideas centered on "
        "RECENT PEER-REVIEWED RESEARCH PAPERS or breaking breakthroughs from the last 72 hours that connect directly "
        "to everyday human life (sleep, food, body, materials). "
        if is_karat
        else ""
    )
    kitchen_a1_note = (
        "KIMIA DAPUR MODE ACTIVE: Prioritize hyper-relatable everyday food, drink, cooking, and home chemistry phenomena "
        "with concrete filmable real footage! "
        if is_kitchen
        else ""
    )
    req_a1_note = (
        f"AUDIENCE COMMENT REQUEST PRIORITY: Viewer '{requested_by}' specifically requested: '{topic_hint}' "
        f"(comment: '{request_source_comment or topic_hint}'). Put a fulfilling, accurate pure-science idea as Candidate #1! "
        if requested_by and topic_hint
        else ""
    )
    all_avoid = list(dict.fromkeys(
        (avoid_titles or [])
        + list(trend_brief.get("recent_published_titles") or [])
        + list(trend_brief.get("rejected_topics_blacklist") or [])
    ))

    state.agent1 = call_structured_agent(
        provider=a1_cfg["provider"],
        model=a1_cfg.get("model", a1_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 1 (Viral Pure-Science Trend Scout) for Kimiabill. "
            "Cover strictly pure science phenomena across: kimia, fisika, matematika, medisinal, biologi. "
            f"{req_a1_note}{karat_a1_note}{kitchen_a1_note}"
            "TOPIC RATIO 70/30 RULE (MANDATORY):\n"
            f"- Target global mix: **70% everyday relatable life science** (`topic_bucket: 'everyday'` — food, sleep, body, kitchen, household items) "
            f"and **30% unique pure-science research news** (`topic_bucket: 'research'` — Nature, ScienceDaily, Phys.org, Quanta).\n"
            f"- Current portfolio balance suggests preferring `{preferred_bucket}` if choosing between equal candidates.\n"
            "STRICT DEDUPLICATION RULE (ZERO TOLERANCE):\n"
            "- NEVER pick or rephrase ANY topic, source_url, or core scientific concept already present in "
            "`avoid_titles`, `trend_brief.recent_published_titles`, or `trend_brief.rejected_topics_blacklist`!\n"
            "- Every candidate MUST have a punchy **3 to 6 word spoiler-free Indonesian title** and 2-4 concrete real-footage keywords."
        ),
        user_payload={
            "brand": brand["brand"],
            "section": "KARAT — Kabar Riset Terkini" if is_karat else ("KIMIA DAPUR — Sains Sehari-hari" if is_kitchen else "REGULER"),
            "trend_brief": trend_brief,
            "topic_hint": topic_hint,
            "requested_by": requested_by,
            "request_comment": request_source_comment,
            "avoid_titles": all_avoid,
            "weekly_growth_directives": owner_a1 + weekly_dir.get("agent_1_directives", []) + karat_dir + slot_map_hint,
        },
        response_model=Agent1Output,
        handoff_path=paths["handoffs"] / "agent1_ideas.json",
    )
    state.status = "ideated"
    save_state(state)

    # Filter out any duplicate candidates deterministically BEFORE Agent 2 scores them
    fresh_candidates = []
    for cand in state.agent1.candidates:
        is_dup, dup_reason = check_topic_duplication(
            cand.title,
            source_url=cand.viral_source_url,
            phenomenon=f"{cand.phenomenon} {cand.core_explanation}",
            avoid_titles=avoid_titles,
            base_dir=BASE_DIR,
        )
        if is_dup:
            print(f"[dedup] Menolak kandidat '{cand.title}': {dup_reason}")
        else:
            fresh_candidates.append(cand)
    if not fresh_candidates and not topic_hint:
        raise RuntimeError(
            "Semua kandidat Agent 1 terdeteksi duplikat dengan published_history/rejected_topics; mencoba topik baru."
        )
    if fresh_candidates:
        state.agent1.candidates = fresh_candidates

    # Agent 2: Editorial Auditor
    a2_cfg = agents_cfg["agents"]["agent_2_editorial_auditor"]
    req_a2_note = (
        f"Viewer '{requested_by}' requested '{topic_hint}' — keep that candidate selected cleanly! "
        if requested_by and topic_hint
        else ""
    )
    state.agent2 = call_structured_agent(
        provider=a2_cfg["provider"],
        model=a2_cfg.get("model", a2_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 2 (Editorial & 90%+ Retention Hook Auditor) for Kimiabill. Score all candidates strictly on:\n"
            "(1) EVERYDAY HUMAN RELATABILITY & 70/30 RATIO: Favor 'everyday' relatable life-science (70%) over abstract theory, unless 'research' is currently underrepresented AND directly relatable.\n"
            "(2) ZERO-SPOILER 3-6 WORD TITLE & 0-3s HOOK: Reject slow greetings/definitions.\n"
            "(3) REAL FILMABLE FOOTAGE: Must work with clean Pexels/stock real-world video (zero reliance on cartoons or obscure equipment).\n"
            "Pick the #1 winner and give up to 4 sharp improvement notes for Agent 3. "
            f"{req_a2_note}"
        ),
        user_payload={
            "candidates": [c.model_dump() for c in state.agent1.candidates],
            "topic_ratio_guidance": ratio_g,
            "requested_by": requested_by,
            "topic_hint": topic_hint,
            "weekly_growth_directives": owner_a2 + weekly_dir.get("agent_2_directives", []) + karat_dir,
        },
        response_model=Agent2Output,
        handoff_path=paths["handoffs"] / "agent2_audit.json",
    )
    state.status = "audited_idea"
    save_state(state)

    # Agent 3: Script & Shot-List Reviser (strict 30-48s, Subscribe/Loop CTA, zero placeholders, clean baku ID)
    a3_cfg = agents_cfg["agents"]["agent_3_script_reviser"]
    winner = next(
        (c for c in state.agent1.candidates if c.id == state.agent2.winner_id),
        state.agent1.candidates[0],
    )
    if winner.source_mode == "idea_bank":
        memory.mark_idea_used(winner.id, episode_id)
    karat_a3_note = (
        "THIS IS A 'KARAT — Kabar Riset Terkini' EPISODE (STILL STRICT 30-46s TOTAL): "
        "Open scene_1 (0-3s) IMMEDIATELY with an everyday hook without greeting; weave the recent research finding & institution cleanly into scene 2-3! "
        "In `references`, put real peer-reviewed journal/institution name + year + working URL/DOI (do NOT invent URLs). "
        if is_karat
        else ""
    )
    kitchen_a3_note = (
        "THIS IS A 'KIMIA DAPUR' EPISODE (STRICT 30-46s TOTAL): keep every explanation grounded in relatable Indonesian food/home moments! "
        if is_kitchen
        else ""
    )
    custom_dur = owner_ro.get("target_duration_sec")
    if isinstance(custom_dur, list) and len(custom_dur) == 2:
        lo_d = max(30, int(custom_dur[0]))
        hi_d = min(48, int(custom_dur[1]))
    else:
        lo_d, hi_d = 32, 45
    est_words_lo, est_words_hi = int(lo_d * 2.3), int(hi_d * 2.45)
    duration_rule = f"STRICTLY {lo_d}-{hi_d}s total spoken ({est_words_lo}-{est_words_hi} Indonesian words across 5 tight scenes — NEVER exceed 48 seconds!)"

    spoken_requester = requested_by.lstrip("@") if requested_by else ""
    req_a3_note = (
        f"FULFILLS VIEWER REQUEST FROM '{requested_by}': mention {spoken_requester} briefly right alongside scene_1 hook! "
        if requested_by
        else ""
    )
    from src.tools.mechanism_bank import format_mechanism_catalog_for_agent3
    from src.tools.qa_gate import run_pre_render_gate0_checks

    mech_catalog = format_mechanism_catalog_for_agent3()
    animator_dirs = weekly_dir.get("agent_4_animator_directives", []) + get_agent_specific_owner_directives("agent_4")

    a3_system_prompt = (
        "You are Agent 3 (High-Retention Indonesian Science Shorts Scriptwriter & Storyboarder) for @kimiabill (YouTube Shorts).\n"
        f"STRICT DURATION & STRUCTURE ({duration_rule}):\n"
        "- **0–3 detik (scene_1 Hook):** Langsung konflik/fakta mengejutkan tanpa salam, tanpa basa-basi, zero spoiler judul.\n"
        "- **3–38 detik (scenes 2–4 Isi):** Penjelasan mekanisme ilmiah padat, konkret, bebas typo (gunakan Bahasa Indonesia baku yang natural, misalnya 'Taruhan deh' BUKAN 'Taro Handei'; tulis istilah ilmiah standar seperti 'kapsaisin', 'glimfatik', 'paklitaksel').\n"
        "- **Penutup (scene terakhir — ZERO DEAD-AIR / SEAMLESS LOOP):** Akhiri langsung di klimaks/kesimpulan atau sambungkan kembali ke kalimat pembuka (*loop*). "
        "Jika memakai CTA, maksimal 1,5 detik dan WAJIB memakai kata **'Subscribe'** (DILARANG KERAS memakai kata **'follow'** di naskah atau caption YouTube!). JANGAN taruh kartu referensi statis atau 'Thank you for watching'!\n"
        "- **Referensi:** Isi field `references` untuk deskripsi YouTube (jurnal/institusi, tahun, URL/DOI yang valid; jangan mengarang URL palsu!).\n"
        "VISUAL QUERY RULES (100% REAL CAMERA FOOTAGE FOR PEXELS/PIXABAY):\n"
        "- Each scene MUST have a simple concrete 2-3 word English `visual_query` of an everyday/science camera subject + 2 concrete `fallback_queries` (e.g., 'dry cracked earth', 'scientist laboratory flask', 'fireflies dark forest', 'factory smoke sky').\n"
        "- STRICTLY FORBIDDEN words in visual_query/fallback_queries: 'animation', 'visualization', 'diagram', '3d', 'cartoon', 'illustration', 'slide', 'lecture', 'arduino', 'mouse cage'. Always phrase what a real camera can film!\n"
        "DIAGRAM / MECHANISM VISUAL RULES (ZERO PLACEHOLDERS & STRICT DOMAIN ENUM!):\n"
        "- Prioritize clean neutral templates: `concept_comparison` (Perbandingan A vs B), `process_flow` (Alur 3 tahap sebab-akibat), `highlight_stat` (Angka/fakta kunci), `minimal_formula_badge` (Rumus/senyawa singkat).\n"
        "- `mechanical_stress` / `polymer_crosslink` IS STRICTLY FORBIDDEN unless the topic is genuinely mechanics/material strength/polymers.\n"
        "- `redox_reaction` / `electron_transfer` IS STRICTLY FORBIDDEN unless there is actual electron transfer / oxidation-reduction.\n"
        "- NEVER output placeholders like 'Molekul A', 'Molekul B', 'Actor A', 'SOLUSI:', 'REAKSI', or 'Produk'! "
        "Every `mechanism` MUST specify exact Indonesian `title` (<=28 chars), concrete `actor_a` (<=16 chars), concrete `actor_b` (<=16 chars, or '' ONLY for highlight_stat/minimal_formula_badge), and concrete `result` (<=22 chars).\n"
        "- Ensure scientific accuracy (e.g., nonpolar capsaicin does NOT dissolve/react with cold water — it binds TRPV1 receptors and dissolves better in fats/casein!).\n"
        "- Assign copyright-free `sfx_cue` (`riser`, `whoosh`, `pop`, `spark`, `ding`, `chime`) on every scene.\n"
        f"{req_a3_note}{karat_a3_note}{kitchen_a3_note}"
    )

    last_a3_errors: list[str] = []
    for a3_try in range(3):
        try:
            agent3_out = call_structured_agent(
                provider=a3_cfg["provider"],
                model=a3_cfg.get("model", a3_cfg["recommended_prod_model"]),
                system_prompt=a3_system_prompt,
                user_payload={
                    "winner_idea": winner.model_dump(),
                    "audit_notes": state.agent2.improvement_notes,
                    "voice_style": brand["voice"],
                    "requested_by": requested_by,
                    "request_comment": request_source_comment,
                    "weekly_growth_directives": owner_a3 + weekly_dir.get("agent_3_directives", []) + karat_dir,
                    "animator_directives": animator_dirs,
                    "mechanism_bank": mech_catalog,
                    "schema_repair_errors": last_a3_errors,
                },
                response_model=Agent3Output,
                handoff_path=paths["handoffs"] / "agent3_script.json",
            )
            agent3_out = _sanitize_agent3_output(agent3_out)
            state.agent3 = agent3_out
            ok_pre, pre_issues = run_pre_render_gate0_checks(state)
            if ok_pre:
                break
            last_a3_errors = pre_issues
            print(f"[agent3] Pre-render Gate 0 menolak draft #{a3_try + 1}, retry Agent 3: {pre_issues}")
        except Exception as exc:
            last_a3_errors = [str(exc)[:300]]
            print(f"[agent3] Schema validation gagal #{a3_try + 1}, retry Agent 3: {last_a3_errors}")
            if a3_try >= 2:
                raise

    if state.agent3 is None:
        raise RuntimeError(f"Agent 3 failed validation after retries: {last_a3_errors}")
    ok_pre_final, pre_issues_final = run_pre_render_gate0_checks(state)
    if not ok_pre_final:
        raise RuntimeError(f"Agent 3 failed Pre-Render Gate 0: {pre_issues_final}")

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
    from src.tools.qa_gate import run_pre_render_gate0_checks
    from src.tools.tts_engine import LOCKED_DEFAULT_VOICE

    brand, _ = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.agent6 is None:
        raise RuntimeError("Need Agent 3 script and Agent 6 assets before rendering.")

    ok_pre, pre_issues = run_pre_render_gate0_checks(state)
    if not ok_pre:
        raise RuntimeError(f"Gate 0 Pre-Render failed before render: {pre_issues}")

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
        print(f"[tts] Memakai ulang audio voiceover locked {LOCKED_DEFAULT_VOICE} dari v1 untuk revisi v{version}")
    else:
        voice_cfg = dict(brand["voice"])
        try:
            import re as _re
            from src.tools.telegram_notify import get_owner_runtime_overrides

            spd_delta = int(get_owner_runtime_overrides().get("voice_speed_delta_pct") or 0)
            if spd_delta != 0:
                m_r = _re.search(r"([+-]?\d+)", str(voice_cfg.get("rate", "+0%")))
                cur_r = int(m_r.group(1)) if m_r else 0
                new_r = max(-20, min(25, cur_r + spd_delta))
                voice_cfg["rate"] = f"{new_r:+d}%"
        except Exception:
            pass
        voice_path, scene_timings, subtitles, _ = generate_episode_voiceover(
            scenes=state.agent3.scenes,
            out_dir=paths["renders"] / f"audio_v{version}",
            voice=str(voice_cfg.get("voice_name") or LOCKED_DEFAULT_VOICE),
            rate=str(voice_cfg.get("rate") or "+4%"),
            pitch=str(voice_cfg.get("pitch") or "+4Hz"),
            volume=str(voice_cfg.get("volume") or "+0%"),
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
    """Strict multimodal Video QA Audit inspecting keyframes + script-vs-diagram science consistency + Gate 0."""
    from src.tools.qa_gate import run_pre_render_gate0_checks

    brand, agents_cfg = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None or state.render_plan is None or state.gate0 is None:
        raise RuntimeError("Render proxy and Gate 0 before running Agent 5 QA.")

    ok_pre, pre_issues = run_pre_render_gate0_checks(state)
    min_qa = float(brand.get("guardrails", {}).get("min_qa_score", 8.0))
    a5_cfg = agents_cfg["agents"]["agent_5_video_qa"]

    audit = call_structured_agent(
        provider=a5_cfg["provider"],
        model=a5_cfg.get("model", a5_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 5 (Strict Multimodal Video QA Auditor) for @kimiabill YouTube Shorts. "
            "Inspect the attached 4-6 keyframe contact sheet image carefully alongside Gate 0 checks, script, and diagram specs. "
            "Be RUTHLESS and objective — do NOT pass defective videos! Check EVERY item:\n"
            "0. SAFETY FIRST: Any nudity, suggestive, violent, or inappropriate frame = automatic FAIL (`revision_target: 'asset_scout'`).\n"
            "1. Full-screen 9:16 B-Roll Relevance & Cleanliness: Confirm every scene fills 9:16 cleanly (NO small 16:9 box over static blur, "
            "NO university/channel bumpers like PennState or LIFE IN THE LAB, NO lecture slides, NO Arduino LCDs in biology topics, "
            "NO unrelated animals, NO watermarks/UI). Flag mismatched clips with `revision_target: 'asset_scout'` & exact `affected_scenes`.\n"
            "2. Science Consistency (Script vs Diagram JSON): Explicitly verify EVERY scene's narration matches its diagram (`mechanism.kind`, "
            "`actor_a`, `actor_b`, `result`). REJECT ANY scientific contradiction (e.g. nonpolar capsaicin reacting/dissolving in plain water, "
            "`mechanical_stress` in non-mechanics topics, `redox_reaction` without redox) OR ANY placeholder (`Molekul A`, `Molekul B`, `SOLUSI:`, `REAKSI`).\n"
            "3. Zero Text Clipping & Contrast: Confirm 0% clipped/truncated labels (`diasi U`, `npone`, `...`), sharp subtitles in safe zone, "
            "and mascots never blocking subtitles or diagram text.\n"
            "4. Shorts Pacing, Audio & YouTube Closing: Confirm duration <=58s (ideal 30-48s), Gate 0 silent gaps <=0.6s, NO static reference end-card "
            "or 'Thank you for watching', and NO TikTok/IG 'follow' CTA (must be seamless loop or YouTube 'Subscribe').\n"
            f"Set `passed: true` ONLY if Gate 0 passed AND pre_render_gate0_ok AND overall_score >= {min_qa} AND ZERO defects above exist."
        ),
        user_payload={
            "version_audited": version,
            "min_qa_score": min_qa,
            "pre_render_gate0_ok": ok_pre,
            "pre_render_gate0_issues": pre_issues,
            "gate0": state.gate0.model_dump(),
            "title": state.agent3.title,
            "weekly_growth_directives": (
                __import__("src.tools.telegram_notify", fromlist=["get_agent_specific_owner_directives"])
                .get_agent_specific_owner_directives("agent_5")
                + __import__("src.tools.weekly_growth_auditor", fromlist=["load_latest_agent_directives"])
                .load_latest_agent_directives()
                .get("agent_5_directives", [])
            ),
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_query": s.visual_query,
                    "overlay_badge": s.overlay_badge,
                    "mechanism": s.mechanism.model_dump() if s.mechanism else None,
                }
                for s in state.agent3.scenes
            ],
        },
        response_model=Agent5AuditOutput,
        handoff_path=paths["handoffs"] / f"agent5_audit_v{version}.json",
        image_paths=[Path(sheet_path)],
    )

    if not ok_pre or not state.gate0.passed or audit.overall_score < min_qa:
        audit.passed = False
        if not ok_pre:
            audit.issues = list(dict.fromkeys([*pre_issues[:2], *(audit.issues or [])]))[:4]

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
    final_plan.references = _collect_episode_references(state, check_http=True)
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
    send_basic_telegram_summary: bool = False,
) -> EpisodeState:
    from src.tools.topic_guard import extract_topic_keywords

    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    url = upload_episode_to_gdrive(
        paths["ep_dir"],
        episode_name=episode_id,
        allow_youtube=allow_youtube,
        qa_summary=qa_summary,
        send_basic_telegram=send_basic_telegram_summary,
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

    pub_mode = os.environ.get("PUBLISH_MODE", "draft").strip().lower()
    # Only record to published_history immediately when already published publicly;
    # unlisted drafts awaiting manual/AI audit are recorded upon approval!
    if allow_youtube and state.agent3 and pub_mode == "public":
        from src.tools.weekly_growth_auditor import record_published_episode

        winner = (
            next((c for c in state.agent1.candidates if c.id == state.agent2.winner_id), None)
            if state.agent1 and state.agent2
            else None
        )
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
            for s in state.agent3.scenes
            if s.mechanism is not None
        ]
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
            mechanisms_used=mechanisms_used,
            topic_slug=state.agent3.episode_slug,
            source_url=winner.viral_source_url if winner else None,
            keywords=extract_topic_keywords(
                f"{state.agent3.title} {winner.phenomenon if winner else ''}"
            ),
            topic_bucket=winner.topic_bucket if winner else "everyday",
            publish_mode="public",
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
    from src.tools.tts_engine import sanitize_indonesian_script

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
            f"thanking them, then ask a super easy 1-word binary poll tied to this topic so viewers immediately comment!"
            if state.requested_by
            else (
                "COMMENT-BOOSTER PINNED COMMENT RULE: Pose ONE playful, specific 1-word debate or confession "
                "tied directly to THIS video's phenomenon, followed briefly by: 'Subscribe & mau bahas keanehan sains apa lagi? Tulis di bawah!' "
                "FORBIDDEN WORD: NEVER use 'follow' anywhere in YouTube title, caption, or pinned_comment (always use 'Subscribe')."
            )
        )
        from src.tools.telegram_notify import get_agent_specific_owner_directives

        owner_a7 = get_agent_specific_owner_directives("agent_7")
        state.agent7 = call_structured_agent(
            provider=a7_cfg["provider"],
            model=a7_cfg.get("model", a7_cfg["recommended_prod_model"]),
            system_prompt=(
                "You are Agent 7 (High-CTR Shorts Packaging & Social Media Publisher) for @kimiabill (YouTube Shorts). "
                "Create viral, non-spoiler thumbnail copy AND platform-tailored Indonesian title, punchy caption, "
                "hashtags, and a conversation-starting `pinned_comment`, strictly respecting `owner_directives`.\n"
                "YOUTUBE RULE: NEVER use the word 'follow' anywhere; always use 'Subscribe'.\n"
                "PLATFORM TITLE RULE: `platform_copies[].title` MUST be **3 to 6 punchy words** (<=45 chars) with ZERO spoiler.\n"
                "COVER THUMBNAIL COPY RULE: `thumbnail_badge` 1-2 words (<=16 chars), `thumbnail_headline_top` 1-3 words (<=15 chars), "
                "`thumbnail_headline_main` 2-3 curiosity words (<=14 chars).\n"
                f"{req_a7_note}"
            ),
            user_payload={
                "brand": brand["brand"],
                "script": state.agent3.model_dump(),
                "requested_by": state.requested_by,
                "request_source_comment": state.request_source_comment,
                "owner_directives": owner_a7,
                "platforms": brand.get("publishing", {}).get(
                    "platforms", ["youtube_shorts", "tiktok", "instagram_reels"]
                ),
            },
            response_model=Agent7Output,
            handoff_path=paths["handoffs"] / "agent7_package.json",
        )
        for cp in state.agent7.platform_copies:
            cp.title = sanitize_indonesian_script(cp.title)
            cp.caption = sanitize_indonesian_script(cp.caption)
            if cp.pinned_comment:
                cp.pinned_comment = sanitize_indonesian_script(cp.pinned_comment)

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
