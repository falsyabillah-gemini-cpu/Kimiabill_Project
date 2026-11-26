"""Strict JSON state schemas for Kimiabill 6-Agent Pipeline.

Each agent only reads the fields it needs and outputs compact structured JSON
to avoid chat-history bloat and keep token usage minimal.
"""
from __future__ import annotations

import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


Discipline = Literal["kimia", "fisika", "matematika", "medisinal", "biologi"]

FORBIDDEN_VISUAL_PLACEHOLDERS: tuple[str, ...] = (
    "molekul a",
    "molekul b",
    "actor a",
    "actor b",
    "solusi:",
    "placeholder",
    "todo",
    "undefined",
)


def contains_forbidden_placeholder(text: str) -> Optional[str]:
    """Return matching forbidden placeholder token using word boundaries (avoids matching 'molekul bergerak')."""
    s = " ".join((text or "").strip().lower().split())
    if not s:
        return None
    for tok in FORBIDDEN_VISUAL_PLACEHOLDERS:
        if re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", s):
            return tok
    return None


def _has_forbidden_placeholder(text: str, allow_empty: bool = False) -> bool:
    s = " ".join((text or "").strip().lower().split())
    if not s:
        return not allow_empty
    if s in {"a", "b", "produk", "hasil", "none", "null", "n/a", "-", "struktur baru", "mekanisme sains"}:
        return True
    return contains_forbidden_placeholder(s) is not None


class IdeaCandidate(BaseModel):
    id: str
    discipline: Discipline = "kimia"
    topic_bucket: Literal["everyday", "research"] = Field(
        default="everyday",
        description="everyday = sains kehidupan sehari-hari (70% target), research = berita riset sains murni unik (30% target)",
    )
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


MechanismKind = Literal[
    "concept_comparison",
    "process_flow",
    "highlight_stat",
    "minimal_formula_badge",
    "mechanical_stress",
    "redox_reaction",
    "electron_transfer",
    "polymer_crosslink",
    "molecular_reaction",
    "receptor_binding",
    "force_wave",
    "math_geometry",
    "osmosis_membrane",
    "protein_cleavage",
    "phase_transition",
    "surface_surfactant",
    "acid_base_bubble",
    "crystal_lattice",
]


class MechanismVisual(BaseModel):
    kind: MechanismKind = "process_flow"
    title: str = Field(
        description="Concise Indonesian header <= 28 chars (NO placeholders like MEKANISME SAINS)",
    )
    actor_a: str = Field(
        description="Concrete short label <= 16 chars (e.g. 'Kapsaisin', 'Gelombang Otak', 'Bromelain')",
    )
    actor_b: str = Field(
        default="",
        description="Concrete second label <= 16 chars (required for two-actor diagrams; optional for highlight_stat/minimal_formula_badge)",
    )
    result: str = Field(
        description="Concrete outcome/stat <= 22 chars (NEVER 'Produk' or 'REAKSI')",
    )
    caption: str = Field(
        default="",
        description="Concise Indonesian caption under the animation (<= 48 chars)",
    )
    preset_id: Optional[str] = Field(
        default=None,
        description="Optional custom preset_id from data/mechanism_bank.json",
    )
    accent_color: Optional[str] = Field(
        default=None,
        description="Optional color theme hint (blue, green, orange, purple, cyan, red, amber)",
    )

    @model_validator(mode="after")
    def validate_no_placeholders_and_limits(self) -> "MechanismVisual":
        if _has_forbidden_placeholder(self.title):
            raise ValueError(f"MechanismVisual.title uses forbidden placeholder/empty text: '{self.title}'")
        if _has_forbidden_placeholder(self.actor_a):
            raise ValueError(f"MechanismVisual.actor_a uses forbidden placeholder/empty text: '{self.actor_a}'")
        needs_actor_b = self.kind not in ("highlight_stat", "minimal_formula_badge")
        if _has_forbidden_placeholder(self.actor_b, allow_empty=not needs_actor_b):
            raise ValueError(f"MechanismVisual.actor_b uses forbidden placeholder/missing text: '{self.actor_b}'")
        if _has_forbidden_placeholder(self.result):
            raise ValueError(f"MechanismVisual.result uses forbidden placeholder/empty text: '{self.result}'")
        if self.caption and contains_forbidden_placeholder(self.caption):
            raise ValueError(f"MechanismVisual.caption contains forbidden placeholder: '{self.caption}'")
        if len(self.title.strip()) > 32:
            raise ValueError(f"MechanismVisual.title exceeds 32 chars ({len(self.title)}): '{self.title}'")
        if len(self.actor_a.strip()) > 20:
            raise ValueError(f"MechanismVisual.actor_a exceeds 20 chars ({len(self.actor_a)}): '{self.actor_a}'")
        if len(self.actor_b.strip()) > 20:
            raise ValueError(f"MechanismVisual.actor_b exceeds 20 chars ({len(self.actor_b)}): '{self.actor_b}'")
        if len(self.result.strip()) > 24:
            raise ValueError(f"MechanismVisual.result exceeds 24 chars ({len(self.result)}): '{self.result}'")
        return self


class SceneSpec(BaseModel):
    scene_id: str
    narration: str = Field(description="Clean baku Indonesian spoken line for this scene (NO 'follow')")
    visual_query: str = Field(description="Concrete 2-4 word English search query for REAL video footage")
    fallback_queries: list[str] = Field(default_factory=list)
    overlay_badge: Optional[str] = Field(
        default=None,
        description="Optional short formula or callout badge ONLY if helpful (e.g. 'Fe + O₂ + H₂O → Karat')",
    )
    mechanism: Optional[MechanismVisual] = Field(
        default=None,
        description="Structured scientific animation spec for visual mechanistic illustration (atoms, electrons, chains, receptors, waves, graphs)",
    )
    sfx_cue: Optional[Literal["whoosh", "pop", "riser", "chime", "ding", "spark", "none"]] = "whoosh"
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
    requested_by: Optional[str] = None
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
    animation_sfx_path: Optional[str] = None
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
    animation_sfx_path: Optional[str] = None
    sfx_gain_db: float = -10.0
    motion: str = "slow_zoom_in"


class RenderPlan(BaseModel):
    version: int = 1
    resolution_mode: Literal["proxy_480p", "final_1080p"] = "proxy_480p"
    title: str = ""
    discipline: str = "kimia"
    section_tag: str = "REGULER"
    section_tagline: str = ""
    requested_by: Optional[str] = None
    use_mascot: bool = True
    references: list[str] = Field(default_factory=list)
    cover_thumbnail_path: Optional[str] = None
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
    pinned_comment: Optional[str] = Field(
        default=None,
        description="Lightweight conversational Indonesian comment starting with an easy 1-3 word question tied to the video topic plus a friendly science topic request prompt",
    )


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
    requested_by: Optional[str] = None
    request_id: Optional[str] = None
    request_source_comment: Optional[str] = None
    request_comment_id: Optional[str] = None
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


class AudienceRequestItem(BaseModel):
    request_id: str = ""
    requester_name: str = Field(description="Commenter @handle or display name to mention when fulfilled (e.g. '@BudiSains' or 'Kak Sari')")
    source_comment: str = Field(description="Exact short excerpt of the audience comment requesting the topic or question")
    source_video_id: Optional[str] = None
    source_comment_id: Optional[str] = None
    topic_title: str = Field(description="Concise, engaging Indonesian pure-science Shorts topic fulfilling this viewer request")
    discipline: Discipline = "kimia"
    status: Literal["pending", "published", "rejected"] = "pending"
    created_week: str = ""
    fulfilled_episode_id: Optional[str] = None
    fulfilled_youtube_url: Optional[str] = None


class NextWeekSlotPlan(BaseModel):
    day_label: str = Field(description="Nama hari WIB: Senin, Selasa, Rabu, Kamis, Jumat, Sabtu, atau Minggu")
    slot_time_wib: Literal["12:00", "19:00"]
    section: Literal["DAPUR", "REGULER", "KARAT"]
    discipline: str = Field(description="Salah satu: kimia, fisika, biologi, medisinal, matematika")
    planned_topic: str = Field(description="Judul/konsep topik konkret (< 75 karakter) untuk diproduksi minggu depan")
    rationale: str = Field(description="Alasan singkat (< 90 karakter): mis. teruskan pola rame / request penonton / isi gap disiplin")


class FlowClipPromptPlan(BaseModel):
    slot_label: str = Field(description="Target jadwal, mis. 'Selasa 12:00 WIB (DAPUR)' atau 'Sabtu 19:00 WIB (KARAT)'")
    topic_title: str = Field(description="Judul singkat topik episode")
    suggested_filename: str = Field(description="Nama file MP4 huruf kecil + underscore agar Agent 6 otomatis mengenali (mis. nanas_serat_daging.mp4)")
    why_flow_needed: str = Field(description="Alasan singkat (< 100 char) mengapa adegan ini paling efektif digenerate di Google Flow")
    flow_prompt_en: str = Field(description="Prompt bahasa Inggris siap salin ke Google Flow (9:16 vertical, photorealistic macro/laboratory B-roll, no text/watermark)")
    recommended_model: str = Field(default="Omni Flash 1.2 (720p, ~18-24 cr)", description="Model hemat kredit di Google Flow (Veo 3.1 Lite 10 cr atau Omni Flash 1.2)")
    estimated_credits: int = Field(default=20, description="Perkiraan konsumsi kredit Flow untuk 1 klip (~10 cr Lite / ~18-24 cr Omni Flash)")


class NextWeekTopicMapOutput(BaseModel):
    next_week_topic_map: list[NextWeekSlotPlan] = Field(
        default_factory=list,
        description="Tepat 14 slot roadmap (Senin-Minggu × 12:00 & 19:00 WIB): 12:00 selalu DAPUR, 19:00 Sen-Jum REGULER (termasuk request penonton), 19:00 Sab-Min KARAT",
    )
    flow_clip_prompts: list[FlowClipPromptPlan] = Field(
        default_factory=list,
        description="Hanya 4-6 adegan pilihan paling berdampak minggu depan yang disarankan digenerate manual di Google AI Flow (tidak semuanya)",
    )


class MechanismBankPresetProposal(BaseModel):
    preset_id: str = Field(description="Lowercase snake_case unique preset ID, e.g. 'maillard_caramel_browning'")
    label_title: str = Field(description="Concise Indonesian header (<= 28 chars), e.g. 'REAKSI MAILLARD & AROMA'")
    engine_kind: MechanismKind = "process_flow"
    accent_color: Literal["blue", "green", "orange", "purple", "cyan", "red", "amber"] = "cyan"
    trigger_keywords: list[str] = Field(
        default_factory=list,
        description="3-6 Indonesian/English topic keywords triggering this preset",
    )
    default_actor_a: str = Field(default="Zat Utama", description="Short default actor A (<= 14 chars)")
    default_actor_b: str = Field(default="Target", description="Short default actor B (<= 14 chars)")
    default_result: str = Field(default="Efek Utama", description="Short default result label (<= 20 chars)")
    rationale: str = Field(default="", description="Brief Indonesian reason why this preset expands the bank")


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
    topic_performance_highlights: list[str] = Field(
        default_factory=list,
        description="3-5 Indonesian findings comparing which specific produced topics got highest views/engagement (paling rame) vs lowest (paling sepi) and why",
    )
    next_week_topic_map: list[NextWeekSlotPlan] = Field(
        default_factory=list,
        description="14-slot weekly roadmap (Senin-Minggu × 12:00 & 19:00 WIB): 12:00 always DAPUR, 19:00 Mon-Fri REGULER (with viewer requests), 19:00 Sat-Sun KARAT",
    )
    flow_clip_prompts: list[FlowClipPromptPlan] = Field(
        default_factory=list,
        description="4-6 high-impact weekly prompts selected for manual Google AI Flow generation",
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
    agent_4_animator_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions specifically for Agent 3/4 Mechanistic Animator to prevent repetitive visuals & ensure topic accuracy",
    )
    agent_5_directives: list[str] = Field(
        default_factory=list,
        description="2-4 concrete instructions for Agent 5 (Video QA Auditor) for next week",
    )
    agent_6_directives: list[str] = Field(
        default_factory=list,
        description="2-3 concrete instructions for Agent 6 (Footage Scout) to reduce wasted screening & find better clips faster",
    )
    mechanism_audit_summary: str = Field(
        default="",
        description="Indonesian audit verdict on whether this week's mechanistic animations matched each topic or overused rigid/lock-and-key/electron templates",
    )
    mechanism_mismatches: list[str] = Field(
        default_factory=list,
        description="Specific episodes/scenes whose mechanism animation mismatched the narration or repeated generic templates",
    )
    new_mechanism_presets: list[MechanismBankPresetProposal] = Field(
        default_factory=list,
        description="1-3 new topic-specific mechanism bank presets to auto-save into data/mechanism_bank.json",
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
    karat_weekend_directives: list[str] = Field(
        default_factory=list,
        description="2-3 concrete research-paper themes & search angles for next weekend's KARAT edition based on this week's highest-engagement topics & viewer comments",
    )
    audience_requests: list[AudienceRequestItem] = Field(
        default_factory=list,
        description="Up to 5 valid pure-science content/topic requests extracted from viewer comments (ignoring spam/greetings), ready to produce & mention next week",
    )
    international_potential: str = Field(
        default="",
        description="Indonesian assessment of non-Indonesian viewer share, foreign-language comments, and which science topics/KARAT themes show global appeal for a future English channel",
    )


PendingReviewStatus = Literal[
    "awaiting_review",
    "approved",
    "approved_by_ai",
    "revising",
    "rejected",
    "held_by_ai",
]


class PendingReviewRecord(BaseModel):
    episode_id: str
    topic_title: str
    topic_slug: str
    source_url: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    topic_bucket: Literal["everyday", "research"] = "everyday"
    discipline: str = "kimia"
    section: str = "REGULER"
    duration_sec: float = 0.0
    references: list[str] = Field(default_factory=list)
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    drive_url: Optional[str] = None
    artifact_path_or_url: Optional[str] = None
    revision_count: int = 0
    qa_summary: str = ""
    target_publish_iso: str = ""
    telegram_message_id: Optional[int] = None
    status: PendingReviewStatus = "awaiting_review"
    reminder_sent: bool = False
    created_at: str = ""
    updated_at: str = ""
    decision_note: str = ""


class ChiefEditorChecks(BaseModel):
    science_accuracy_and_diagram_match: Literal["PASS", "FAIL"]
    no_placeholder_or_clipping: Literal["PASS", "FAIL"]
    broll_relevance_and_no_foreign_bumper: Literal["PASS", "FAIL"]
    audio_consistency_and_pronunciation: Literal["PASS", "FAIL"]
    shorts_pacing_and_youtube_cta: Literal["PASS", "FAIL"]


class ChiefEditorAuditOutput(BaseModel):
    decision: Literal["APPROVE", "REVISE", "HOLD"]
    overall_score: float = Field(ge=0, le=10)
    fatal_flaws: list[str] = Field(default_factory=list)
    checks: ChiefEditorChecks
    revision_instruction: str = ""

