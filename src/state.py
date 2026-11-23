"""Strict JSON state schemas for Kimiabill 6-Agent Pipeline.

Each agent only reads the fields it needs and outputs compact structured JSON
to avoid chat-history bloat and keep token usage minimal.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


Discipline = Literal["kimia", "fisika", "matematika", "medisinal", "biologi"]


class IdeaCandidate(BaseModel):
    id: str
    discipline: Discipline = "kimia"
    source_mode: Literal["viral_trend", "idea_bank"] = "idea_bank"
    viral_source_url: Optional[str] = None
    title: str
    phenomenon: str
    hook_3s: str = Field(description="Opening 3-second hook in Indonesian")
    core_explanation: str = Field(description="1-2 sentence plain-language science core")
    real_footage_feasibility: str = Field(description="Why real footage is easy to find")


class Agent1Output(BaseModel):
    mode_used: Literal["viral_trend", "hybrid", "idea_bank_fallback"] = "idea_bank_fallback"
    candidates: list[IdeaCandidate] = Field(min_length=3, max_length=5)


class IdeaScore(BaseModel):
    id: str
    hook_score: float = Field(ge=1, le=10)
    clarity_score: float = Field(ge=1, le=10)
    visual_realness_score: float = Field(ge=1, le=10)
    overall_score: float = Field(ge=1, le=10)
    short_note: str


class Agent2Output(BaseModel):
    scores: list[IdeaScore]
    winner_id: str
    passed: bool
    improvement_notes: list[str] = Field(
        max_length=4,
        description="Max 4 concrete editorial improvements for Agent 3",
    )


class MechanismVisual(BaseModel):
    kind: Literal[
        "electron_transfer",
        "polymer_crosslink",
        "molecular_reaction",
        "receptor_binding",
        "force_wave",
        "math_geometry",
    ] = "molecular_reaction"
    title: str = Field(
        default="MEKANISME SAINS",
        description="Short Indonesian simulation title, e.g. 'TRANSFER ELEKTRON' or 'PENGUATAN STRUKTUR POLIMER'",
    )
    actor_a: str = Field(
        default="A",
        description="Short formula/actor label (max 14 chars), e.g. 'Fe', 'Rantai Gula', 'Obat'",
    )
    actor_b: str = Field(
        default="B",
        description="Second formula/actor/force label (max 14 chars), e.g. 'O₂', 'Ikatan Silang', 'Enzim COX'",
    )
    result: str = Field(
        default="Produk",
        description="Outcome label (max 18 chars), e.g. 'Fe₂O₃ (Karat)', 'Struktur Rigid', 'Nyeri Reda'",
    )
    caption: str = Field(
        default="",
        description="Concise Indonesian caption under the animation (max 48 chars)",
    )


class SceneSpec(BaseModel):
    scene_id: str
    narration: str = Field(description="Indonesian spoken line for this scene")
    visual_query: str = Field(description="English search query for REAL video footage")
    fallback_queries: list[str] = Field(default_factory=list)
    overlay_badge: Optional[str] = Field(
        default=None,
        description="Optional short formula or callout badge ONLY if helpful (e.g. 'Fe + O₂ + H₂O → Karat')",
    )
    mechanism: Optional[MechanismVisual] = Field(
        default=None,
        description="Structured scientific animation spec for visual mechanistic illustration (atoms, electrons, chains, receptors, waves, graphs)",
    )
    sfx_cue: Optional[Literal["whoosh", "pop", "riser", "chime", "ding", "none"]] = "none"
    motion: Literal["slow_zoom_in", "slow_zoom_out", "pan_right", "static"] = "slow_zoom_in"


class Agent3Output(BaseModel):
    episode_slug: str
    title: str
    caption_social: str
    hashtags: list[str]
    references: list[str] = Field(
        default_factory=list,
        description="1-3 concise scientific references or source citations shown at the end of the video",
    )
    scenes: list[SceneSpec] = Field(min_length=4, max_length=8)


class SceneAsset(BaseModel):
    scene_id: str
    footage_path: str
    source: str
    source_url: str
    license: str
    duration_sec: float
    width: int
    height: int
    sfx_path: Optional[str] = None
    notes: str = ""


class Agent6Output(BaseModel):
    assets: list[SceneAsset]
    bgm_path: Optional[str] = None


class SubtitleChunk(BaseModel):
    start: float
    end: float
    text: str


class TimelineSegment(BaseModel):
    scene_id: str
    start_sec: float
    end_sec: float
    duration_sec: float
    footage_path: str
    footage_start_offset: float = 0.0
    narration: str = ""
    overlay_badge: Optional[str] = None
    mechanism: Optional[MechanismVisual] = None
    sfx_path: Optional[str] = None
    sfx_gain_db: float = -10.0
    motion: str = "slow_zoom_in"


class RenderPlan(BaseModel):
    version: int = 1
    resolution_mode: Literal["proxy_480p", "final_1080p"] = "proxy_480p"
    title: str = ""
    discipline: str = "kimia"
    section_tag: str = "REGULER"
    section_tagline: str = ""
    use_mascot: bool = True
    references: list[str] = Field(default_factory=list)
    voice_path: str
    total_duration_sec: float
    segments: list[TimelineSegment]
    subtitles: list[SubtitleChunk]
    output_video_path: str
    subtitle_font_size_ratio: float = 0.052
    bgm_gain_db: float = -24.0


class Gate0CheckResult(BaseModel):
    passed: bool
    duration_sec: float
    width: int
    height: int
    mean_volume_db: float
    max_volume_db: float
    black_frame_ratio: float
    issues: list[str] = Field(default_factory=list)


class Agent5AuditOutput(BaseModel):
    version_audited: int
    passed: bool
    overall_score: float = Field(ge=1, le=10)
    visual_relevance_score: float = Field(ge=1, le=10)
    subtitle_readability_score: float = Field(ge=1, le=10)
    pacing_and_audio_score: float = Field(ge=1, le=10)
    revision_target: Literal["none", "editor", "asset_scout", "both"] = "none"
    affected_scenes: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list, max_length=4)
    patch_instructions: list[str] = Field(default_factory=list, max_length=4)


class PlatformCopy(BaseModel):
    platform: Literal["youtube_shorts", "tiktok", "instagram_reels"]
    title: str = Field(description="Short punchy title (< 70 chars)")
    caption: str = Field(description="Platform-optimized Indonesian caption with CTA")
    hashtags: list[str]
    pinned_comment: Optional[str] = None


class PlatformPublishResult(BaseModel):
    platform: Literal["youtube_shorts", "tiktok", "instagram_reels"]
    mode: Literal["dry_run", "draft", "public"]
    status: Literal["packaged_only", "uploaded_draft", "published", "skipped_missing_creds", "error"]
    post_id: Optional[str] = None
    post_url: Optional[str] = None
    detail: str = ""


class Agent7Output(BaseModel):
    thumbnail_headline_top: str
    thumbnail_headline_main: str
    thumbnail_badge: str
    thumbnail_path: Optional[str] = None
    platform_copies: list[PlatformCopy]
    publish_results: list[PlatformPublishResult] = Field(default_factory=list)


class EpisodeState(BaseModel):
    episode_id: str
    status: Literal[
        "init",
        "ideated",
        "audited_idea",
        "scripted",
        "assets_ready",
        "rendered_proxy",
        "qa_passed",
        "rendered_final",
        "uploaded",
        "published",
        "needs_human_review",
    ] = "init"
    revision_count: int = 0
    agent1: Optional[Agent1Output] = None
    agent2: Optional[Agent2Output] = None
    agent3: Optional[Agent3Output] = None
    agent6: Optional[Agent6Output] = None
    render_plan: Optional[RenderPlan] = None
    gate0: Optional[Gate0CheckResult] = None
    agent5_history: list[Agent5AuditOutput] = Field(default_factory=list)
    agent7: Optional[Agent7Output] = None
    final_video_path: Optional[str] = None
    gdrive_folder_url: Optional[str] = None
    youtube_url: Optional[str] = None


class Agent8WeeklyAuditOutput(BaseModel):
    week_ending: str = Field(description="ISO date YYYY-MM-DD (WIB) of the Sunday audit")
    summary: str = Field(description="Executive summary in Indonesian of this week's channel & video performance")
    top_performing_patterns: list[str] = Field(
        default_factory=list,
        description="3-5 concrete patterns that worked best (topics, hooks, disciplines, pacing, visuals)",
    )
    bottlenecks_and_weaknesses: list[str] = Field(
        default_factory=list,
        description="3-5 concrete weaknesses holding back views, retention, likes, or comments",
    )
    agent_1_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 1 (Trend Scout & Ideation) for next week",
    )
    agent_2_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 2 (Editorial & Hook Auditor) for next week",
    )
    agent_3_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 3 (Script, Hook & Mechanistic Storyboarder) for next week",
    )
    agent_5_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 5 (Video QA Auditor) for next week",
    )
    agent_6_directives: list[str] = Field(
        default_factory=list,
        description="2-3 concrete instructions for Agent 6 (Footage Scout) to reduce wasted screening & find better clips faster",
    )
    workflow_efficiency_summary: str = Field(
        default="",
        description="Indonesian assessment of weekly production speed, revision rate, topic retries, and Gemini/ElevenLabs token efficiency",
    )
    token_and_cost_optimizations: list[str] = Field(
        default_factory=list,
        description="2-4 concrete workflow/prompt/caching optimizations to save tokens, reduce failed revisions, and speed up production",
    )
    next_week_experiments: list[str] = Field(
        default_factory=list,
        description="2-3 concrete engagement experiments to test next week (hook style, CTA, pacing, topic angle)",
    )
    recommended_weekend_hours_wib: list[str] = Field(
        default_factory=lambda: ["12:00", "19:00"],
        description="Top 2 optimal HH:MM WIB upload times for Saturday/Sunday KARAT Research Edition based on analytics",
    )
