from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import yaml
from PIL import Image, ImageDraw

from src.state import (
    Agent3Output,
    ChiefEditorAuditOutput,
    ChiefEditorChecks,
    EpisodeState,
    MechanismVisual,
    PendingReviewRecord,
    SceneSpec,
)
from src.tools.asset_scout import DISALLOWED_TITLE_TOKENS
from src.tools.mechanistic_animator import (
    _anim_concept_comparison,
    _anim_highlight_stat,
    _anim_minimal_formula_badge,
    _anim_process_flow,
    _draw_loop_subscribe_pill,
    _fit_text_layout,
    _infer_mechanism_params,
    _load_fonts,
    TimelineSegment,
)
from src.tools.qa_gate import run_gate0_checks, run_pre_render_gate0_checks
from src.tools.reference_verifier import verify_and_clean_references
from src.tools.topic_guard import (
    check_topic_duplication,
    compute_topic_bucket_guidance,
    record_rejected_topic,
)
from src.tools.tts_engine import (
    LOCKED_DEFAULT_VOICE,
    sanitize_indonesian_script,
    to_tts_phonetic,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WIB = ZoneInfo("Asia/Jakarta")


def _sample_scene(
    scene_id: str = "scene_2",
    narration: str = "Kapsaisin menempel di reseptor panas lidah tanpa bereaksi dengan air.",
    kind: str = "concept_comparison",
    title: str = "Mekanisme Kapsaisin",
    actor_a: str = "Kapsaisin (Nonpolar)",
    actor_b: str = "Reseptor TRPV1",
    result: str = "Sinyal Panas Aktif",
) -> SceneSpec:
    return SceneSpec(
        scene_id=scene_id,
        purpose="mechanism",
        duration_sec=10.0,
        narration=narration,
        visual_query="red chili pepper",
        overlay_badge="EFEK RESEPTOR TRPV1",
        mechanism=MechanismVisual(
            kind=kind,
            title=title,
            actor_a=actor_a,
            actor_b=actor_b,
            result=result,
        ),
    )


def _sample_state(scene2: SceneSpec | None = None, cta_narration: str = "Subscribe sekarang agar kamu tidak ketinggalan fakta kimia seru berikutnya di channel ini!") -> EpisodeState:
    s1 = SceneSpec(
        scene_id="scene_1",
        purpose="hook",
        duration_sec=5.0,
        narration="Kenapa minum air es justru bikin rasa pedas cabai di lidah makin menyebar ke seluruh mulut dan terasa semakin panas?",
        visual_query="drinking cold water",
    )
    s2 = scene2 or _sample_scene(
        narration="Ternyata molekul bergerak bebas dari kapsaisin bersifat nonpolar, sehingga senyawa ini menempel erat di reseptor panas TRPV1 pada permukaan lidah tanpa bisa dilarutkan atau bereaksi dengan air dingin biasa."
    )
    s3 = SceneSpec(
        scene_id="scene_3",
        purpose="takeaway",
        duration_sec=8.0,
        narration="Sebaliknya, segelas susu dingin mengandung protein kasein dan lemak alami yang langsung mengikat molekul kapsaisin nonpolar lalu membilasnya dari reseptor lidah jauh lebih efektif.",
        visual_query="milk glass pour",
    )
    s4 = SceneSpec(
        scene_id="scene_4",
        purpose="cta",
        duration_sec=5.0,
        narration=cta_narration,
        visual_query="fresh red chili",
    )
    return EpisodeState(
        episode_id="ep_test_v2",
        agent3=Agent3Output(
            episode_slug="air-tidak-redam-pedas",
            topic_title="Kenapa Air Biasa Tidak Bisa Meredakan Pedas Cabai",
            title="Kenapa Air Gagal Redam Pedas?",
            caption_social="Kapsaisin bersifat nonpolar sehingga air justru menyebarkannya!",
            hashtags=["#kimiabill", "#sains"],
            target_duration_sec=36.0,
            scenes=[s1, s2, s3, s4],
        ),
    )


class TestWorkflowsAndConfigs(unittest.TestCase):
    def test_json_and_yaml_configs_valid(self) -> None:
        pdict = json.loads((REPO_ROOT / "config" / "pronunciation_dict.json").read_text(encoding="utf-8"))
        self.assertIn("capsaicin", pdict["phonetic_map"])
        self.assertIn("dna", pdict["phonetic_map"])

        brand = yaml.safe_load((REPO_ROOT / "config" / "brand.yaml").read_text(encoding="utf-8"))
        self.assertEqual(brand["brand"]["ideation_policy"]["everyday_ratio"], 0.70)
        self.assertEqual(brand["brand"]["ideation_policy"]["research_ratio"], 0.30)
        self.assertEqual(brand["voice"]["voice_name"], LOCKED_DEFAULT_VOICE)
        self.assertTrue(brand["voice"]["lock_single_voice"])
        self.assertEqual(brand["publishing"]["default_mode"], "draft")
        self.assertEqual(brand["publishing"]["publish_slots_wib"], ["12:00", "19:00"])
        self.assertEqual(brand["publishing"]["publish_time_wib"], "19:00")

        for state_name in ("rejected_topics.json", "pending_review.json"):
            data = json.loads((REPO_ROOT / "data" / state_name).read_text(encoding="utf-8"))
            self.assertIsNotNone(data)

    def test_workflows_node24_ready_and_scheduled_correctly(self) -> None:
        wf_dir = REPO_ROOT / ".github" / "workflows"
        wf_files = sorted(wf_dir.glob("*.yml"))
        self.assertGreaterEqual(len(wf_files), 5)

        for wf in wf_files:
            raw = wf.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw)
            self.assertIsInstance(parsed, dict, msg=f"Invalid YAML in {wf.name}")
            self.assertNotIn("actions/checkout@v4", raw, msg=f"Deprecated checkout@v4 in {wf.name}")
            self.assertNotIn("actions/setup-python@v5", raw, msg=f"Deprecated setup-python@v5 in {wf.name}")
            self.assertNotIn("actions/upload-artifact@v4", raw, msg=f"Deprecated upload-artifact@v4 in {wf.name}")

        daily_raw = (wf_dir / "daily_kimiabill.yml").read_text(encoding="utf-8")
        daily = yaml.safe_load(daily_raw)
        on_block = daily.get("on") or daily.get(True)
        crons = [item["cron"] for item in on_block["schedule"]]
        self.assertIn("30 3 * * *", crons)
        self.assertIn("30 10 * * *", crons)
        self.assertIn("telegram_rerender", on_block["repository_dispatch"]["types"])
        self.assertIn("telegram_reject_next", on_block["repository_dispatch"]["types"])
        self.assertEqual(on_block["workflow_dispatch"]["inputs"]["publish_mode"]["default"], "draft")
        self.assertIn('cache: "pip"', daily_raw)

        audit_raw = (wf_dir / "audit_fallback.yml").read_text(encoding="utf-8")
        audit = yaml.safe_load(audit_raw)
        audit_on = audit.get("on") or audit.get(True)
        audit_crons = [item["cron"] for item in audit_on["schedule"]]
        self.assertIn("30 4 * * *", audit_crons)
        self.assertIn("45 4 * * *", audit_crons)
        self.assertIn("30 11 * * *", audit_crons)
        self.assertIn("45 11 * * *", audit_crons)
        self.assertIn("telegram_approve", audit_on["repository_dispatch"]["types"])
        self.assertIn("telegram_ai_audit", audit_on["repository_dispatch"]["types"])

    def test_dual_slot_target_publish_time_selection(self) -> None:
        from src.tools.review_manager import compute_target_publish_iso

        with patch.dict(os.environ, {"TARGET_PUBLISH_TIME_WIB": "", "PUBLISH_TIME_WIB": ""}, clear=False):
            morn_iso = compute_target_publish_iso(now_wib=datetime(2026, 11, 26, 10, 30, tzinfo=WIB))
            eve_iso = compute_target_publish_iso(now_wib=datetime(2026, 11, 26, 17, 30, tzinfo=WIB))
        self.assertIn("T12:00:00", morn_iso)
        self.assertIn("T19:00:00", eve_iso)


class TestTopicGuardAndReferences(unittest.TestCase):
    def test_bilingual_dedup_url_and_rejection_blacklist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "data").mkdir(parents=True, exist_ok=True)
            (tmp / "data" / "published_history.json").write_text(
                json.dumps(
                    [
                        {
                            "episode_id": "ep_old_1",
                            "title": "Sunscreen Rahasia DNA yang Bikin Kehidupan Purba Selamat dari Radiasi!",
                            "source_url": "https://phys.org/news/ancient-dna-uv-sunscreen.html?utm_source=rss",
                            "topic_bucket": "research",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (tmp / "data" / "rejected_topics.json").write_text("[]", encoding="utf-8")

            dup_url, _ = check_topic_duplication(
                "Totally Different Title",
                source_url="https://phys.org/news/ancient-dna-uv-sunscreen.html",
                base_dir=tmp,
            )
            self.assertTrue(dup_url)

            dup_concept, reason = check_topic_duplication(
                "Ancient DNA Acted as Natural Ultraviolet Sunscreen on Early Earth",
                base_dir=tmp,
            )
            self.assertTrue(dup_concept, msg=reason)

            ok_new, _ = check_topic_duplication(
                "Kenapa Nanas Bikin Lidah Gatal Saat Dimakan?",
                source_url="https://example.org/bromelain-pineapple",
                base_dir=tmp,
            )
            self.assertFalse(ok_new)

            record_rejected_topic(
                episode_id="ep_rej_1",
                title="Kenapa Nanas Bikin Lidah Gatal Saat Dimakan?",
                source_url="https://example.org/bromelain-pineapple",
                base_dir=tmp,
            )
            dup_rej, _ = check_topic_duplication(
                "Misteri Enzim Bromelain Nanas yang Mencerna Lidah",
                base_dir=tmp,
            )
            self.assertTrue(dup_rej)

            mix = compute_topic_bucket_guidance(base_dir=tmp)
            self.assertEqual(mix["research_count"], 1)
            self.assertEqual(mix["preferred_next_bucket"], "everyday")

    def test_reference_verifier_strips_dead_urls_keeps_valid(self) -> None:
        with patch("src.tools.reference_verifier.check_url_alive") as m_check:
            m_check.side_effect = lambda u: "good.org" in u
            cleaned = verify_and_clean_references(
                [
                    "Journal of Science (2025) https://good.org/paper",
                    "Hallucinated Paper https://broken.org/404",
                    "Nature Reviews Microbiology doi:10.9999/dead",
                    "Footage video: Pexels",
                ],
                check_http=True,
            )
        self.assertEqual(len(cleaned), 3)
        self.assertIn("https://good.org/paper", cleaned[0])
        self.assertEqual(cleaned[1], "Hallucinated Paper")
        self.assertEqual(cleaned[2], "Nature Reviews Microbiology")


class TestTTSSanitizationAndPhonetics(unittest.TestCase):
    def test_typo_fixes_and_phonetics_separate_subtitle_from_tts(self) -> None:
        raw = "Taro Handei! Bag kapsaikin di cabai beda pH sama DNA dan sinar UV. Jangan lupa follow!"
        subtitle = sanitize_indonesian_script(raw)
        self.assertIn("Taruhan deh", subtitle)
        self.assertNotIn("Taro Handei", subtitle)
        self.assertNotIn(" Bag ", f" {subtitle} ")
        self.assertNotIn("follow", subtitle.lower())
        self.assertIn("subscribe", subtitle.lower())

        tts_text = to_tts_phonetic(subtitle)
        self.assertIn("kapsaisin", tts_text.lower())
        self.assertIn("pe-ha", tts_text.lower())
        self.assertIn("d-n-a", tts_text.lower())
        self.assertIn("yu-vi", tts_text.lower())

        gly = to_tts_phonetic("Sistem glymphatic membersihkan limbah saat tidur dan paclitaxel bekerja di sel.")
        self.assertIn("glimfatik", gly.lower())
        self.assertIn("paklitaksel", gly.lower())


class TestSchemaAndGate0(unittest.TestCase):
    def test_mechanism_visual_rejects_placeholders(self) -> None:
        for bad_val in ("Molekul A", "Molekul B", "SOLUSI: Uji Lab", "A", "Produk", "TODO"):
            with self.assertRaises(Exception, msg=f"Expected rejection for '{bad_val}'"):
                MechanismVisual(
                    kind="process_flow",
                    title="Proses Normal",
                    actor_a=bad_val if bad_val != "Molekul B" else "Enzim Aktif",
                    actor_b="Molekul B" if bad_val == "Molekul B" else "Reseptor",
                    result="Sinyal Aktif",
                )

    def test_pre_render_gate0_enforces_domain_cta_and_char_limits(self) -> None:
        good_state = _sample_state()
        ok, issues = run_pre_render_gate0_checks(good_state)
        self.assertTrue(ok, msg=issues)

        bad_cta = _sample_state(cta_narration="Follow akun ini untuk info kimia lainnya!")
        ok_cta, issues_cta = run_pre_render_gate0_checks(bad_cta)
        self.assertFalse(ok_cta)
        self.assertTrue(any("follow" in i.lower() for i in issues_cta))

        bad_mech = _sample_state(
            scene2=_sample_scene(
                narration="Sistem glimfatik membersihkan racun otak selama tidur nyenyak.",
                kind="mechanical_stress",
                title="Aliran Otak",
                actor_a="Cairan Otak",
                actor_b="Limbah Protein",
                result="Otak Bersih",
            )
        )
        ok_mech, issues_mech = run_pre_render_gate0_checks(bad_mech)
        self.assertFalse(ok_mech)
        self.assertTrue(any("mechanical_stress" in i for i in issues_mech))

        bad_redox = _sample_state(
            scene2=_sample_scene(
                narration="Kapsaisin menempel di reseptor panas lidah manusia.",
                kind="redox_reaction",
                title="Reaksi Lidah",
                actor_a="Kapsaisin",
                actor_b="Reseptor Lidah",
                result="Sensasi Panas",
            )
        )
        ok_redox, issues_redox = run_pre_render_gate0_checks(bad_redox)
        self.assertFalse(ok_redox)
        self.assertTrue(any("redox_reaction" in i for i in issues_redox))

    def test_post_render_gate0_ffmpeg_checks_duration_resolution_and_silence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            good_mp4 = tmp / "good.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=orange:s=480x854:d=28:r=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=28",
                    "-af", "volume=-3dB",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(good_mp4),
                ],
                check=True,
                capture_output=True,
            )
            res_ok = run_gate0_checks(good_mp4, 480, 854)
            self.assertTrue(res_ok.passed, msg=res_ok.issues)

            res_bad_dim = run_gate0_checks(good_mp4, 1080, 1920)
            self.assertFalse(res_bad_dim.passed)
            self.assertTrue(any("Resolution mismatch" in i for i in res_bad_dim.issues))

            silent_mp4 = tmp / "silent_gap.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=orange:s=480x854:d=28:r=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=28",
                    "-af", "volume=-3dB,volume=enable='between(t,10,12)':volume=0",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(silent_mp4),
                ],
                check=True,
                capture_output=True,
            )
            res_sil = run_gate0_checks(silent_mp4, 480, 854)
            self.assertFalse(res_sil.passed)
            self.assertTrue(any("Dead silence gap" in i for i in res_sil.issues))


class TestAssetScoutAndAnimator(unittest.TestCase):
    def test_disallowed_wikimedia_tokens_block_bumpers_and_lectures(self) -> None:
        for bad_title in (
            "PennState Metallic Glass Lecture.webm",
            "LIFE IN THE LAB University Presentation.ogv",
            "Arduino LCD sensor demo.mp4",
            "Rock hyrax sleeping on branch.webm",
        ):
            low = bad_title.lower()
            self.assertTrue(
                any(tok in low for tok in DISALLOWED_TITLE_TOKENS),
                msg=f"Failed to block '{bad_title}'",
            )

    def test_clean_stock_query_and_photo_ken_burns_fallback(self) -> None:
        from io import BytesIO
        from src.tools.asset_scout import (
            VideoCandidate,
            _clean_stock_query,
            _extract_pexels_slug,
            _render_photo_ken_burns_mp4,
            probe_video,
        )

        self.assertEqual(_clean_stock_query("greenhouse gas emissions animation"), "greenhouse gas emissions")
        self.assertEqual(_clean_stock_query("firefly glowing closeup"), "firefly glowing")
        self.assertEqual(
            _extract_pexels_slug("https://www.pexels.com/video/parched-dry-earth-98765/", "fallback"),
            "parched dry earth",
        )

        img = Image.new("RGB", (1280, 720), (220, 140, 40))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        fake_resp = MagicMock(content=buf.getvalue())
        fake_resp.raise_for_status = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "scene_kb.mp4"
            cand = VideoCandidate(
                source="pexels_photo",
                title="PexelsPhoto #1 - parched earth",
                download_url="https://images.pexels.com/photos/1/pexels-photo-1.jpeg",
                page_url="https://www.pexels.com/photo/parched-earth-1/",
                license="Pexels License (Free Real Photo)",
                duration_sec=5.5,
                width=1280,
                height=720,
            )
            with patch("src.tools.asset_scout.requests.get", return_value=fake_resp):
                info = _render_photo_ken_burns_mp4(cand, dest, duration_sec=3.0)
            self.assertIsNotNone(info)
            probed = probe_video(dest)
            self.assertEqual(probed["width"], 1080)
            self.assertEqual(probed["height"], 1920)
            self.assertGreaterEqual(probed["duration"], 2.8)

        from src.tools.asset_scout import _resolve_pexels_api_key, _resolve_pixabay_api_key
        with patch.dict(os.environ, {"PIXABAY_API_KEY": "", "PEXELS_API_KEY": "41234567-abcdef1234567890abcd"}, clear=False):
            self.assertEqual(_resolve_pixabay_api_key(), "41234567-abcdef1234567890abcd")
            self.assertEqual(_resolve_pexels_api_key(), "")

    def test_animator_neutral_templates_and_text_fit(self) -> None:
        fonts = _load_fonts()
        canvas = Image.new("RGBA", (1080, 1920), (20, 28, 44, 255))
        draw = ImageDraw.Draw(canvas)
        box = (72, 1380, 1008, 1715)

        lines, used_f, max_w, _ = _fit_text_layout(
            draw,
            "Enzim Khitinase Pelindung",
            fonts["small_bold"],
            max_w=190,
            max_lines=2,
            min_size=13,
        )
        self.assertGreaterEqual(len(lines), 1)
        self.assertLessEqual(max_w, 190)

        for anim_fn, kind in (
            (_anim_concept_comparison, "concept_comparison"),
            (_anim_process_flow, "process_flow"),
            (_anim_highlight_stat, "highlight_stat"),
            (_anim_minimal_formula_badge, "minimal_formula_badge"),
        ):
            params = {
                "kind": kind,
                "title": "Uji Visual Sains",
                "actor_a": "Kapsaisin Nonpolar",
                "actor_b": "Reseptor TRPV1",
                "result": "Sinyal Panas Aktif",
                "caption": "Fakta Kimia Sehari-hari",
                "accent_color": "orange",
            }
            anim_fn(draw, box, 2.0, 5.0, params, fonts)

        _draw_loop_subscribe_pill(draw, fonts, local_t=4.2, seg_dur=4.8, requested_by=None)

        bad_seg = TimelineSegment(
            scene_id="scene_bad",
            purpose="mechanism",
            start_sec=0.0,
            end_sec=4.0,
            duration_sec=4.0,
            narration="Uji placeholder",
            footage_path="/tmp/dummy.mp4",
            mechanism=None,
            overlay_badge="Molekul B",
        )
        with self.assertRaises(ValueError):
            _infer_mechanism_params(bad_seg)


class TestReviewManagerAndChiefEditor(unittest.TestCase):
    def test_review_lifecycle_approve_and_reject(self) -> None:
        import src.tools.review_manager as rm
        import src.tools.topic_guard as tg

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pr_path = tmp / "pending_review.json"
            future_iso = (datetime.now(WIB) + timedelta(minutes=45)).isoformat(timespec="seconds")
            rec = PendingReviewRecord(
                episode_id="ep_review_test",
                topic_title="Kenapa Es Mengapung di Air",
                topic_slug="kenapa-es-mengapung-di-air",
                source_url="https://example.org/ice-density",
                youtube_video_id="yt_12345",
                youtube_url="https://youtube.com/shorts/yt_12345",
                status="awaiting_review",
                target_publish_time="18:30 WIB",
                target_publish_iso=future_iso,
            )

            with (
                patch.object(rm, "PENDING_REVIEW_PATH", pr_path),
                patch.object(rm, "BASE_DIR", tmp),
                patch.object(tg, "BASE_DIR", tmp),
                patch.object(rm, "_bot_creds", return_value=("tok", "chat")),
                patch.object(rm, "send_telegram_message", return_value=True),
                patch.object(rm, "clear_audit_buttons_on_telegram"),
                patch("src.tools.weekly_growth_auditor.record_published_episode"),
                patch.object(rm, "update_youtube_publish_status", return_value={"ok": True}) as m_yt,
            ):
                rm.upsert_review_record(rec)
                rem1 = rm.send_pending_review_reminder()
                self.assertEqual(rem1["action"], "reminder_sent")
                rem2 = rm.send_pending_review_reminder()
                self.assertEqual(rem2["action"], "reminder_already_sent")

                app1 = rm.handle_telegram_approve("ep_review_test")
                self.assertEqual(app1["status"], "approved")
                m_yt.assert_called_once()
                self.assertEqual(m_yt.call_args.kwargs["privacy_status"], "private")
                self.assertEqual(m_yt.call_args.kwargs["publish_at_iso"], future_iso)

                app2 = rm.handle_telegram_approve("ep_review_test")
                self.assertTrue(app2.get("idempotent_skip"))

                # Test reject flow on a fresh draft
                rec2 = rec.model_copy(update={"episode_id": "ep_reject_test", "status": "awaiting_review"})
                rm.upsert_review_record(rec2)
                with (
                    patch.object(rm, "delete_youtube_video", return_value={"ok": True}) as m_del,
                    patch.object(rm, "record_rejected_topic", side_effect=lambda **kw: tg.record_rejected_topic(**kw, base_dir=tmp)),
                    patch("src.scheduler.run_one_automated_episode") as m_sched,
                ):
                    rej = rm.handle_telegram_reject_next("ep_reject_test")
                self.assertEqual(rej["rejected_episode_id"], "ep_reject_test")
                m_del.assert_called_once_with("yt_12345")
                m_sched.assert_called_once()
                rej_list = json.loads((tmp / "data" / "rejected_topics.json").read_text(encoding="utf-8"))
                self.assertEqual(len(rej_list), 1)
                self.assertEqual(rej_list[0]["episode_id"], "ep_reject_test")

    def test_chief_editor_enforces_zero_tolerance(self) -> None:
        import src.chief_editor_audit as ce

        verdict_fake = ChiefEditorAuditOutput(
            decision="APPROVE",
            overall_score=8.7,
            checks=ChiefEditorChecks(
                science_accuracy_and_diagram_match="PASS",
                no_placeholder_or_clipping="FAIL",
                broll_relevance_and_no_foreign_bumper="PASS",
                audio_consistency_and_pronunciation="PASS",
                shorts_pacing_and_youtube_cta="PASS",
            ),
            fatal_flaws=["Ada teks diagram terpotong"],
            revision_instruction="Perpendek label diagram",
            summary="Model keliru memberi APPROVE walau ada FAIL",
        )
        rec = PendingReviewRecord(
            episode_id="ep_ce_test",
            topic_title="Test Zero Tolerance",
            topic_slug="test-zero-tolerance",
            status="awaiting_review",
            revision_count=1,
        )
        with tempfile.TemporaryDirectory() as td:
            fake_mp4 = Path(td) / "FINAL_ep_ce_test.mp4"
            fake_mp4.write_bytes(b"00")
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False),
                patch.object(ce, "get_review_record", return_value=rec),
                patch.object(ce, "_find_or_fetch_video_for_episode", return_value=fake_mp4),
                patch.object(ce, "_audit_keyframes_fallback", return_value=verdict_fake),
                patch.object(ce, "clear_audit_buttons_on_telegram"),
                patch.object(ce, "upsert_review_record"),
                patch.object(ce, "send_telegram_message"),
            ):
                res = ce.run_chief_editor_audit("ep_ce_test", force=True)
        # Because revision_count==1 (budget exhausted), downgraded APPROVE -> HOLD (never APPROVE!)
        self.assertEqual(res["decision"], "HOLD")
        self.assertEqual(res["verdict"]["decision"], "REVISE")

    def test_backup_queue_save_and_promote_on_reject(self) -> None:
        import src.tools.backup_queue as bq
        import src.tools.review_manager as rm
        import src.tools.topic_guard as tg

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            pr_path = tmp / "pending_review.json"
            future_iso = (datetime.now(WIB) + timedelta(minutes=60)).isoformat(timespec="seconds")

            rec_backup = PendingReviewRecord(
                episode_id="ep_backup_1",
                topic_title="Kenapa Es Licin Saat Disentuh",
                topic_slug="kenapa-es-licin",
                youtube_video_id="yt_bk_1",
                youtube_url="https://youtube.com/shorts/yt_bk_1",
                status="awaiting_review",
                target_publish_iso=future_iso,
            )
            rec_bad = PendingReviewRecord(
                episode_id="ep_bad_today",
                topic_title="Topik Jelek Hari Ini",
                topic_slug="topik-jelek-hari-ini",
                youtube_video_id="yt_bad_1",
                youtube_url="https://youtube.com/shorts/yt_bad_1",
                status="awaiting_review",
                target_publish_iso=future_iso,
            )

            with (
                patch.object(rm, "PENDING_REVIEW_PATH", pr_path),
                patch.object(rm, "BASE_DIR", tmp),
                patch.object(rm, "_bot_creds", return_value=("tok", "chat")),
                patch.object(rm, "send_telegram_message", return_value=True),
                patch.object(rm, "clear_audit_buttons_on_telegram"),
                patch.object(rm, "push_backup_video", side_effect=lambda r, **kw: bq.push_backup_video(r, base_dir=tmp, **kw)),
                patch.object(rm, "pop_next_backup_video", side_effect=lambda: bq.pop_next_backup_video(base_dir=tmp)),
                patch.object(rm, "peek_backup_count", side_effect=lambda: bq.peek_backup_count(base_dir=tmp)),
                patch.object(rm, "record_rejected_topic", side_effect=lambda **kw: tg.record_rejected_topic(**kw, base_dir=tmp)),
                patch.object(rm, "delete_youtube_video", return_value={"ok": True}) as m_del,
                patch("src.tools.review_manager.requests.post") as m_post,
            ):
                m_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 777}})

                # 1. Save active trial draft as standby backup
                rm.upsert_review_record(rec_backup)
                res_sb = rm.handle_telegram_save_backup("ep_backup_1")
                self.assertEqual(res_sb["backup_count"], 1)
                self.assertIsNone(rm.load_pending_review_store()["current"])

                # 2. Next episode arrives and owner clicks Reject -> Pakai Video Cadangan
                rm.upsert_review_record(rec_bad)
                res_rej = rm.handle_telegram_reject_use_backup("ep_bad_today")
                self.assertTrue(res_rej["used_backup"])
                self.assertEqual(res_rej["promoted_episode_id"], "ep_backup_1")
                m_del.assert_called_once_with("yt_bad_1")

                # Verify promoted backup is now awaiting review with 0 left in backup queue
                self.assertEqual(bq.peek_backup_count(base_dir=tmp), 0)
                curr = rm.load_pending_review_store()["current"]
                self.assertEqual(curr["episode_id"], "ep_backup_1")
                self.assertEqual(curr["status"], "awaiting_review")


class TestGapFillWaterfallAndContracts(unittest.TestCase):
    def test_kimiabill_phonetic_and_consecutive_mechanism_guard(self) -> None:
        self.assertIn("Kimia Bil", to_tts_phonetic("Selamat datang di Kimiabill!"))

        from src.pipeline import _sanitize_agent3_output

        s1 = _sample_scene("scene_1", "Kenapa es mengapung di air padahal wujudnya padat dan lebih keras dari air cair di bawahnya setiap hari?", kind="process_flow")
        s2 = _sample_scene("scene_2", "Saat air membeku, ikatan hidrogen membentuk kisi kristal heksagonal yang lebih renggang sehingga volume es membesar dan massa jenisnya turun.", kind="process_flow")
        s3 = _sample_scene("scene_3", "Rongga antar molekul es membuat volume es mengembang sekitar sembilan persen lebih besar daripada volume air cair sebelumnya.", kind="process_flow")
        s4 = _sample_scene("scene_4", "Akibat massa jenis es turun menjadi lebih ringan dari air cair, lapisan es selalu mengapung dan Subscribe channel ini sekarang!", kind="process_flow")

        ep_raw = EpisodeState(
            episode_id="ep_mech_dup",
            agent3=Agent3Output(
                episode_slug="es-mengapung-di-air",
                topic_title="Kenapa Es Mengapung Di Air?",
                title="Kenapa Es Mengapung Di Air?",
                caption_social="Subscribe untuk fakta kimia harian!",
                hashtags=["#kimiabill"],
                target_duration_sec=36.0,
                scenes=[s1, s2, s3, s4],
            ),
        )
        ok_before, issues_before = run_pre_render_gate0_checks(ep_raw)
        self.assertFalse(ok_before)
        self.assertTrue(any("repeats previous scene" in iss for iss in issues_before))

        fixed_a3 = _sanitize_agent3_output(ep_raw.agent3)
        for idx in range(1, len(fixed_a3.scenes)):
            self.assertNotEqual(fixed_a3.scenes[idx - 1].mechanism.kind, fixed_a3.scenes[idx].mechanism.kind)
        ep_fixed = EpisodeState(episode_id="ep_mech_dup", agent3=fixed_a3)
        ok_after, issues_after = run_pre_render_gate0_checks(ep_fixed)
        self.assertTrue(ok_after, msg=f"Unexpected issues: {issues_after}")

    def test_negative_keywords_and_24h_pixabay_cache_and_coverr_unsplash_skip(self) -> None:
        import src.tools.asset_scout as asc

        sc = _sample_scene("scene_1")
        sc.visual_query = "coffee pouring mug"
        self.assertIn("watermark", sc.negative_keywords)
        good_cand = asc.VideoCandidate("pixabay", "Pixabay #1 - coffee pouring mug slow motion", "https://ex/1.mp4", "https://ex/p1", "Pixabay", 8.0, 1080, 1920)
        bad_cand = asc.VideoCandidate("pixabay", "Pixabay #2 - coffee presentation logo lecture slide", "https://ex/2.mp4", "https://ex/p2", "Pixabay", 8.0, 1080, 1920)
        terms = asc._extract_scene_topic_terms(sc)
        self.assertGreater(asc._metadata_relevance_score(good_cand, sc, terms), 0)
        self.assertEqual(asc._metadata_relevance_score(bad_cand, sc, terms), -10)

        # Silent empty key skips
        with patch.dict(os.environ, {"COVERR_API_KEY": "", "UNSPLASH_ACCESS_KEY": ""}, clear=False):
            self.assertEqual(asc.search_coverr_videos("coffee mug"), [])
            self.assertEqual(asc.search_unsplash_photos("coffee mug"), [])

        # Pixabay 24h cache avoids duplicate HTTP call
        with tempfile.TemporaryDirectory() as td:
            cache_p = Path(td) / "pixabay_cache.json"
            fake_hit = {
                "id": 99,
                "duration": 9,
                "tags": "coffee mug pour",
                "pageURL": "https://pixabay.com/v/99",
                "videos": {"medium": {"url": "https://cdn.pixabay.com/99.mp4", "width": 1080, "height": 1920}},
            }
            mock_resp = MagicMock(status_code=200, json=lambda: {"hits": [fake_hit]})
            with (
                patch.object(asc, "PIXABAY_CACHE_PATH", cache_p),
                patch.dict(os.environ, {"PIXABAY_API_KEY": "12345-abcdef1234567890"}, clear=False),
                patch("src.tools.asset_scout.requests.get", return_value=mock_resp) as m_get,
            ):
                first = asc.search_pixabay("coffee mug")
                second = asc.search_pixabay("coffee mug")
                self.assertEqual(len(first), 1)
                self.assertEqual(len(second), 1)
                m_get.assert_called_once()

        # Unsplash download_location ping is sent when UNSPLASH_ACCESS_KEY is set
        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                jpg_buf = tmp / "src.jpg"
                Image.new("RGB", (400, 600), (40, 80, 120)).save(jpg_buf)
                img_bytes = jpg_buf.read_bytes()
                u_cand = asc.VideoCandidate(
                    "unsplash_photo",
                    "Unsplash - coffee cup",
                    "https://images.unsplash.com/photo-1",
                    "https://unsplash.com/photos/1",
                    "Unsplash",
                    duration_sec=7.0,
                    width=1080,
                    height=1920,
                    ping_url="https://api.unsplash.com/photos/1/download?ixid=abc",
                )
                with (
                    patch.dict(os.environ, {"UNSPLASH_ACCESS_KEY": "u_key_123"}, clear=False),
                    patch("src.tools.asset_scout.requests.get") as m_uget,
                ):
                    m_uget.return_value = MagicMock(status_code=200, content=img_bytes, raise_for_status=lambda: None)
                    info = asc._render_photo_ken_burns_mp4(u_cand, tmp / "out_kb.mp4", duration_sec=2.5)
                    self.assertIsNotNone(info)
                    called_urls = [c.args[0] for c in m_uget.call_args_list]
                    self.assertIn("https://api.unsplash.com/photos/1/download?ixid=abc", called_urls)

    def test_batch_contact_sheet_audit_and_tier3_ambient_zero_crash(self) -> None:
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg missing")
        import src.tools.asset_scout as asc

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sc1 = _sample_scene("scene_1")
            sc1.visual_query = "boiling water pot"
            sc2 = _sample_scene("scene_2")
            sc2.visual_query = "steam rising kitchen"

            amb1 = asc._render_tier3_ambient_mp4(sc1, tmp / "s1.mp4", duration_sec=2.5)
            amb2 = asc._render_tier3_ambient_mp4(sc2, tmp / "s2.mp4", duration_sec=2.5)
            self.assertIsNotNone(amb1)
            self.assertIsNotNone(amb2)
            assert amb1 is not None and amb2 is not None
            self.assertEqual(amb1[1]["width"], 1080)
            self.assertEqual(amb1[1]["height"], 1920)

            fake_verdict = MagicMock()
            fake_verdict.shots = [
                MagicMock(shot_index=1, family_safe=True, has_watermark_or_bumper=False, relevance_score=7.0, reason="hook < 7.5"),
                MagicMock(shot_index=2, family_safe=True, has_watermark_or_bumper=False, relevance_score=6.4, reason="body >= 6.0"),
            ]
            with (
                patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key", "ASSET_VISION_SCREEN": "true"}, clear=False),
                patch("src.providers.call_structured_agent", return_value=fake_verdict) as m_vision,
            ):
                res = asc.batch_audit_broll_contact_sheet(
                    [(1, sc1, tmp / "s1.mp4"), (2, sc2, tmp / "s2.mp4")],
                    tmp / "sheet.jpg",
                )
            m_vision.assert_called_once()
            self.assertFalse(res[1][0])  # shot 1 needs >= 7.5
            self.assertTrue(res[2][0])   # shot 2 needs >= 6.0

            # Full scout_assets_for_scenes never hard-crashes even when all stock/photo/AI sources return nothing
            fake_mem = MagicMock()
            fake_mem.get_used_flow_clip_ids.return_value = set()
            fake_mem.get_bgm_path.return_value = None
            fake_mem.get_sfx_path.return_value = None
            with (
                patch.dict(os.environ, {"PIXABAY_API_KEY": "", "PEXELS_API_KEY": "", "COVERR_API_KEY": "", "UNSPLASH_ACCESS_KEY": "", "GEMINI_API_KEY": "", "CF_ACCOUNT_ID": "", "CF_API_TOKEN": ""}, clear=False),
                patch.object(asc, "list_flow_clip_bank", return_value=[]),
                patch.object(asc, "search_wikimedia_videos", return_value=[]),
                patch.object(asc, "search_wikimedia_photos", return_value=[]),
                patch.object(asc, "search_openverse_photos", return_value=[]),
                patch.object(asc, "search_nasa_videos", return_value=[]),
            ):
                out = asc.scout_assets_for_scenes([sc1], tmp / "ep_zero" / "assets", fake_mem)
            self.assertEqual(len(out.assets), 1)
            self.assertEqual(out.assets[0].source, "tier3_ambient")
            self.assertTrue(Path(out.assets[0].footage_path).exists())


if __name__ == "__main__":
    unittest.main()
