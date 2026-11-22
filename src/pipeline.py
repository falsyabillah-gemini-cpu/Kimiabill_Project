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
    paths = episode_paths(state.episode_id)
    asset_map = {a.scene_id: a for a in state.agent6.assets}
    scene_map = {s.scene_id: s for s in state.agent3.scenes}
    offsets = scene_offsets or {}

    segments: list[TimelineSegment] = []
    for timing in scene_timings:
        sc = scene_map[timing.scene_id]
        ast = asset_map[timing.scene_id]
        segments.append(
            TimelineSegment(
                scene_id=timing.scene_id,
                start_sec=timing.start_sec,
                end_sec=timing.end_sec,
                duration_sec=timing.duration_sec,
                footage_path=ast.footage_path,
                footage_start_offset=offsets.get(timing.scene_id, 0.0),
                overlay_badge=sc.overlay_badge,
                sfx_path=ast.sfx_path,
                sfx_gain_db=-10.5,
                motion=sc.motion,
            )
        )

    if resolution_mode == "final_1080p":
        out_mp4 = paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    else:
        out_mp4 = paths["renders"] / f"proxy_v{version}_480p.mp4"

    total_dur = segments[-1].end_sec if segments else 0.0
    return RenderPlan(
        version=version,
        resolution_mode=resolution_mode,
        voice_path=voice_path,
        total_duration_sec=round(total_dur, 3),
        segments=segments,
        subtitles=subtitles,
        output_video_path=str(out_mp4),
        subtitle_font_size_ratio=subtitle_font_size_ratio,
        bgm_gain_db=bgm_gain_db,
    )


def run_agent_1_to_3(episode_id: str, topic_hint: Optional[str] = None) -> EpisodeState:
    brand, agents_cfg = load_configs()
    memory = StudioMemory(BASE_DIR)
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)

    # Pre-Agent 1 (0-Token): Scan viral pure-science trends first, fallback to Idea Bank
    ideation_cfg = brand["brand"].get("ideation_policy", {})
    trend_brief = build_agent1_brief(
        BASE_DIR,
        lookback_hours=ideation_cfg.get("viral_lookback_hours", 96),
        min_viral_matches=ideation_cfg.get("min_viral_matches", 2),
    )
    (paths["handoffs"] / "agent0_trend_brief.json").write_text(
        json.dumps(trend_brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Agent 1: Batch Ideation (5 ideas — Viral-First across 5 Pure Sciences, fallback to Idea Bank)
    a1_cfg = agents_cfg["agents"]["agent_1_ideation"]
    state.agent1 = call_structured_agent(
        provider=a1_cfg["provider"],
        model=a1_cfg.get("model", a1_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 1 (Viral Pure-Science Trend Scout) for Kimiabill. "
            "Cover strictly pure science phenomena across: kimia, fisika, matematika, medisinal, biologi. "
            "PRIORITY 1: If `viral_candidates` has fresh viral science topics, prioritize turning them into "
            "engaging Indonesian short-video ideas (`source_mode: 'viral_trend'`). "
            "PRIORITY 2 (Fallback): If no strong viral topic is available, select and enrich unused ideas "
            "from `idea_bank_fallback_candidates` (`source_mode: 'idea_bank'`), keeping all 5 disciplines balanced. "
            "Every idea must be visualizable with REAL footage (never full animation)."
        ),
        user_payload={
            "brand": brand["brand"],
            "trend_brief": trend_brief,
            "topic_hint": topic_hint,
        },
        response_model=Agent1Output,
        handoff_path=paths["handoffs"] / "agent1_ideas.json",
    )
    state.status = "ideated"
    save_state(state)

    # Agent 2: Editorial Auditor (Score 5 -> pick 1 winner + max 4 sharp improvements)
    a2_cfg = agents_cfg["agents"]["agent_2_editorial_auditor"]
    state.agent2 = call_structured_agent(
        provider=a2_cfg["provider"],
        model=a2_cfg.get("model", a2_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 2 (Editorial Auditor) for Kimiabill. Score all candidates on hook, "
            "plain-language clarity, and real-footage visual strength. Pick the #1 winner and "
            "give max 4 concrete improvement notes."
        ),
        user_payload={"candidates": [c.model_dump() for c in state.agent1.candidates]},
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
    state.agent3 = call_structured_agent(
        provider=a3_cfg["provider"],
        model=a3_cfg.get("model", a3_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 3 (Script & Shot-List Reviser) for Kimiabill. Write a cheerful, clear, "
            "moderate-pace Indonesian short script (35-50s total, 5-7 scenes) applying Agent 2's notes. "
            "Each scene MUST specify an English real-footage visual_query (no cartoons/animations) "
            "and optional short overlay_badge ONLY when a formula/label genuinely helps understanding."
        ),
        user_payload={
            "winner_idea": winner.model_dump(),
            "audit_notes": state.agent2.improvement_notes,
            "voice_style": brand["voice"],
        },
        response_model=Agent3Output,
        handoff_path=paths["handoffs"] / "agent3_script.json",
    )
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
        state.agent6 = scout_assets_for_scenes(
            scenes=state.agent3.scenes,
            assets_dir=paths["assets"],
            memory=memory,
        )
        handoff_a6.write_text(state.agent6.model_dump_json(indent=2), encoding="utf-8")

    state.status = "assets_ready"
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
    video_path, sheet_path = render_video(
        plan=plan,
        work_dir=paths["renders"],
        bgm_path=state.agent6.bgm_path,
    )
    gate0 = run_gate0_checks(video_path, expected_width=480, expected_height=854)

    state.render_plan = plan
    state.gate0 = gate0
    state.status = "rendered_proxy"
    save_state(state)
    return state, video_path, sheet_path


def run_final_hd_render(episode_id: str) -> tuple[EpisodeState, str, str]:
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.render_plan is None or state.agent3 is None or state.agent6 is None:
        raise RuntimeError("Render proxy and pass QA before final HD render.")

    final_plan = state.render_plan.model_copy(deep=True)
    final_plan.resolution_mode = "final_1080p"
    final_plan.output_video_path = str(
        paths["ep_dir"] / f"FINAL_{state.agent3.episode_slug}_1080p.mp4"
    )
    video_path, sheet_path = render_video(
        plan=final_plan,
        work_dir=paths["renders"],
        bgm_path=state.agent6.bgm_path,
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


def run_upload(episode_id: str) -> EpisodeState:
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    url = upload_episode_to_gdrive(paths["ep_dir"], episode_name=episode_id)
    state.gdrive_folder_url = url
    state.status = "uploaded"
    save_state(state)

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


def run_agent_7_publish(
    episode_id: str,
    mode: Optional[str] = None,
    video_override: Optional[str] = None,
) -> EpisodeState:
    import os

    brand, agents_cfg = load_configs()
    paths = episode_paths(episode_id)
    state = load_or_init_state(episode_id)
    if state.agent3 is None:
        raise RuntimeError("Need Agent 3 script before running Agent 7 Publisher.")

    publish_mode = mode or os.environ.get(
        "PUBLISH_MODE",
        brand.get("publishing", {}).get("default_mode", "draft"),
    )

    a7_cfg = agents_cfg["agents"]["agent_7_publisher"]
    pkg = call_structured_agent(
        provider=a7_cfg["provider"],
        model=a7_cfg.get("model", a7_cfg["recommended_prod_model"]),
        system_prompt=(
            "You are Agent 7 (Packaging & Social Media Publisher) for @kimiabill. "
            "Create high-CTR thumbnail copy (top line + main line + short badge) and "
            "platform-tailored Indonesian title, caption, hashtags, and pinned comment "
            "for youtube_shorts, tiktok, and instagram_reels."
        ),
        user_payload={
            "brand": brand["brand"],
            "script": state.agent3.model_dump(),
            "platforms": brand.get("publishing", {}).get(
                "platforms", ["youtube_shorts", "tiktok", "instagram_reels"]
            ),
        },
        response_model=Agent7Output,
        handoff_path=paths["handoffs"] / "agent7_package.json",
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

    # Generate 9:16 Cover Thumbnail over scene_1 real footage + Opsi B Duo Mascots
    bg_clip = (
        Path(state.render_plan.segments[0].footage_path)
        if state.render_plan and state.render_plan.segments
        else final_video
    )
    bg_ts = (
        state.render_plan.segments[0].footage_start_offset + 1.4
        if state.render_plan and state.render_plan.segments
        else 1.5
    )
    use_mascot = brand.get("video", {}).get("mascot", {}).get("enabled", True)
    thumb_path = paths["ep_dir"] / f"THUMBNAIL_{state.agent3.episode_slug}_9x16.jpg"
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
