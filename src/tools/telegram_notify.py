"""Telegram Bot notification & Pola B owner-feedback bridge for Kimiabill Studio.

Pattern B behavior:
1. Automated episodes still publish on schedule (no blocking approval wait).
2. Rendered MP4 previews, weekly audit PDFs, and Google Flow prompts are delivered
   straight to the owner's Telegram chat (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).
3. Any feedback written by the owner in Telegram is stored into `data/owner_feedback.json`
   (either instantly via Google Apps Script webhook -> GitHub repository_dispatch OR
   automatically pulled via `getUpdates` before the next run starts) and injected into
   Agent 1, 2, 3, and 5 for upcoming videos.
4. If an episode is seriously off, `/rerender <notes>` (via Apps Script Telegram webhook
   -> `telegram_rerender` dispatch) immediately triggers a fresh production replacement.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

WIB = ZoneInfo("Asia/Jakarta")
BASE_DIR = Path(__file__).resolve().parent.parent.parent
OWNER_FEEDBACK_PATH = BASE_DIR / "data" / "owner_feedback.json"


def _bot_creds() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def telegram_enabled() -> bool:
    token, chat_id = _bot_creds()
    return bool(token and chat_id)


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def send_telegram_message(text: str, chat_id_override: Optional[str] = None) -> bool:
    """Send plain-text Telegram message(s), splitting cleanly below Telegram's 4096-char limit."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    if not token or not chat_id:
        return False
    body = (text or "").strip()
    if not body:
        return False

    chunks: list[str] = []
    remaining = body
    while len(remaining) > 3900:
        split_at = remaining.rfind("\n", 0, 3900)
        if split_at < 500:
            split_at = 3900
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)

    ok_all = True
    for chunk in chunks:
        try:
            resp = requests.post(
                _api_url(token, "sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": False,
                },
                timeout=25,
            )
            data = resp.json() if resp.status_code == 200 else {}
            if not data.get("ok"):
                ok_all = False
                print(f"[telegram] sendMessage gagal ({resp.status_code}): {resp.text[:160]}")
        except Exception as exc:
            ok_all = False
            print(f"[telegram] sendMessage error: {exc}")
    return ok_all


def send_telegram_video(
    mp4_path: Path,
    caption: str = "",
    chat_id_override: Optional[str] = None,
) -> bool:
    """Upload MP4 video directly to Telegram chat (<= 48 MB), falling back to message if oversized."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    if not token or not chat_id:
        return False
    if not mp4_path.exists():
        return send_telegram_message(caption, chat_id_override=chat_id) if caption else False
    size_mb = mp4_path.stat().st_size / (1024 * 1024)
    if size_mb > 48.0:
        print(f"[telegram] Video {mp4_path.name} ({size_mb:.1f} MB > 48 MB); mengirim pesan link saja.")
        return send_telegram_message(caption, chat_id_override=chat_id)

    short_cap = (caption or "").strip()[:980]
    try:
        with mp4_path.open("rb") as fh:
            resp = requests.post(
                _api_url(token, "sendVideo"),
                data={
                    "chat_id": chat_id,
                    "caption": short_cap,
                    "supports_streaming": "true",
                },
                files={"video": (mp4_path.name, fh, "video/mp4")},
                timeout=120,
            )
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("ok"):
            return True
        print(f"[telegram] sendVideo gagal ({resp.status_code}): {resp.text[:160]}; fallback ke sendMessage.")
    except Exception as exc:
        print(f"[telegram] sendVideo error: {exc}; fallback ke sendMessage.")
    return send_telegram_message(caption, chat_id_override=chat_id) if caption else False


def send_telegram_photo(
    photo_path: str | Path,
    caption: str = "",
    chat_id_override: Optional[str] = None,
) -> bool:
    """Send an image infographic (PNG/JPG) inline to owner's Telegram chat."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    img_path = Path(photo_path)
    if not token or not chat_id or not img_path.exists():
        return False
    short_cap = (caption or "").strip()[:1000]
    try:
        with img_path.open("rb") as fh:
            resp = requests.post(
                _api_url(token, "sendPhoto"),
                data={"chat_id": chat_id, "caption": short_cap},
                files={"photo": (img_path.name, fh)},
                timeout=60,
            )
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("ok"):
            return True
        print(f"[telegram] sendPhoto gagal ({resp.status_code}): {resp.text[:160]}")
    except Exception as exc:
        print(f"[telegram] sendPhoto error: {exc}")
    return send_telegram_document(img_path, caption=short_cap, chat_id_override=chat_id)


def send_telegram_document(
    file_path: Path,
    caption: str = "",
    chat_id_override: Optional[str] = None,
) -> bool:
    """Upload PDF/TXT document directly to Telegram chat."""
    token, default_chat = _bot_creds()
    chat_id = (chat_id_override or default_chat).strip()
    if not token or not chat_id or not file_path.exists():
        return False
    if file_path.stat().st_size > 48 * 1024 * 1024:
        return False
    short_cap = (caption or "").strip()[:980]
    try:
        with file_path.open("rb") as fh:
            resp = requests.post(
                _api_url(token, "sendDocument"),
                data={"chat_id": chat_id, "caption": short_cap},
                files={"document": (file_path.name, fh)},
                timeout=90,
            )
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("ok"):
            return True
        print(f"[telegram] sendDocument gagal ({resp.status_code}): {resp.text[:160]}")
    except Exception as exc:
        print(f"[telegram] sendDocument error: {exc}")
    return False


def load_owner_feedback_state() -> dict[str, Any]:
    if not OWNER_FEEDBACK_PATH.exists():
        return {"last_update_id": 0, "items": []}
    try:
        raw = json.loads(OWNER_FEEDBACK_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("last_update_id", 0)
            raw.setdefault("items", [])
            return raw
    except Exception:
        pass
    return {"last_update_id": 0, "items": []}


DEFAULT_RUNTIME_OVERRIDES: dict[str, Any] = {
    "force_sfx_every_transition": True,
    "force_sfx_every_animation": True,
    "sfx_gain_db_delta": 0.0,
    "bgm_gain_db_delta": 0.0,
    "voice_speed_delta_pct": 0,
    "subtitle_scale_delta": 0.0,
    "visual_style_bias": "",
    "target_duration_sec": None,
}


def load_owner_feedback_state() -> dict[str, Any]:
    base = {
        "last_update_id": 0,
        "items": [],
        "agent_directives": {},
        "runtime_overrides": dict(DEFAULT_RUNTIME_OVERRIDES),
    }
    if not OWNER_FEEDBACK_PATH.exists():
        return base
    try:
        raw = json.loads(OWNER_FEEDBACK_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("last_update_id", 0)
            raw.setdefault("items", [])
            raw.setdefault("agent_directives", {})
            ro = dict(DEFAULT_RUNTIME_OVERRIDES)
            if isinstance(raw.get("runtime_overrides"), dict):
                ro.update(raw["runtime_overrides"])
            raw["runtime_overrides"] = ro
            return raw
    except Exception:
        pass
    return base


def save_owner_feedback_state(state: dict[str, Any]) -> None:
    OWNER_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_FEEDBACK_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_deterministic_overrides_and_notes(text: str) -> tuple[dict[str, Any], dict[str, list[str]], list[str]]:
    """Deterministic fallback + hard engine guardrails for any owner Telegram feedback."""
    low = (text or "").lower()
    overrides: dict[str, Any] = {}
    directives: dict[str, list[str]] = {}
    notes: list[str] = []

    if any(k in low for k in ("sfx", "efek suara", "sound effect", "whoosh", "pop", "ganti footage", "animasi")):
        overrides["force_sfx_every_transition"] = True
        overrides["force_sfx_every_animation"] = True
        directives.setdefault("agent_3", []).append(
            "WAJIB isi `sfx_cue` non-'none' (pilih whoosh/riser/pop/spark/ding/chime bebas copyright) di SETIAP scene, terutama setiap pergantian footage & kemunculan animasi."
        )
        directives.setdefault("agent_5", []).append(
            "Pastikan seluruh pergantian footage & scene animasi memakai SFX bebas copyright yang pas dan seimbang."
        )
        notes.append("Engine Audio: SFX bebas copyright dikunci aktif di setiap pergantian footage & animasi")

    if "sfx" in low and any(k in low for k in ("kecil", "pelan", "turun", "kurangin")):
        overrides["sfx_gain_db_delta"] = -2.5
        notes.append("Engine Audio: volume SFX diturunkan -2.5 dB")
    elif "sfx" in low and any(k in low for k in ("besar", "keras", "naik", "kencang")):
        overrides["sfx_gain_db_delta"] = 2.0
        notes.append("Engine Audio: volume SFX dinaikkan +2.0 dB")

    if any(k in low for k in ("musik", "bgm", "backsound")) and any(k in low for k in ("kecil", "pelan", "turun", "kebesaran", "berisik")):
        overrides["bgm_gain_db_delta"] = -3.0
        notes.append("Engine Audio: BGM diturunkan -3.0 dB agar narasi makin jelas")
    elif any(k in low for k in ("musik", "bgm", "backsound")) and any(k in low for k in ("besar", "naik", "keras")):
        overrides["bgm_gain_db_delta"] = 2.5
        notes.append("Engine Audio: BGM dinaikkan +2.5 dB")

    if any(k in low for k in ("suara", "ngomong", "narasi", "tempo", "voice")) and any(k in low for k in ("terlalu cepat", "kecepetan", "pelan", "lambat")):
        overrides["voice_speed_delta_pct"] = -6
        notes.append("Engine Voice: kecepatan narasi diturunkan -6%")
    elif any(k in low for k in ("suara", "ngomong", "narasi", "tempo", "voice")) and any(k in low for k in ("terlalu lambat", "kelamaan", "cepatin", "lebih cepat")):
        overrides["voice_speed_delta_pct"] = 6
        notes.append("Engine Voice: kecepatan narasi dinaikkan +6%")

    if any(k in low for k in ("subtitle", "teks", "tulisan", "caption layar")) and any(k in low for k in ("kecil", "kebesaran", "besar")):
        if "kebesaran" in low or "terlalu besar" in low or "kecilkan" in low:
            overrides["subtitle_scale_delta"] = -0.006
            notes.append("Engine Visual: ukuran subtitle diperkecil sedikit")
        else:
            overrides["subtitle_scale_delta"] = 0.006
            notes.append("Engine Visual: ukuran subtitle diperbesar agar makin terbaca")

    import re as _re
    m_dur = _re.search(r"\b(3[0-9]|4[0-9]|5[0-9])\s*(detik|dtk|s|sec)\b", low)
    if m_dur:
        sec = int(m_dur.group(1))
        lo_s = max(28, sec - 5)
        hi_s = min(58, sec + 4)
        overrides["target_duration_sec"] = [lo_s, hi_s]
        directives.setdefault("agent_3", []).append(
            f"Target durasi total narasi WAJIB sekitar {lo_s}-{hi_s} detik sesuai arahan owner."
        )
        notes.append(f"Engine & Agent 3: target durasi disesuaikan ke {lo_s}-{hi_s} detik")

    if any(k in low for k in ("footage", "visual", "klip", "gambar", "animasi", "diagram")):
        directives.setdefault("agent_6", []).append(f"[ARAHAN OWNER]: {text[:260]}")

    if any(k in low for k in ("judul", "thumbnail", "cover", "caption", "komen", "hashtag")):
        directives.setdefault("agent_7", []).append(f"[ARAHAN OWNER]: {text[:260]}")

    return overrides, directives, notes


def compile_owner_feedback_state(use_llm: bool = True) -> dict[str, Any]:
    """Compile all saved owner feedback items into concrete directives for Agents 1,2,3,5,6,7,8 + runtime overrides."""
    st = load_owner_feedback_state()
    items = [x for x in (st.get("items") or []) if isinstance(x, dict) and str(x.get("text") or "").strip()]

    ro = dict(DEFAULT_RUNTIME_OVERRIDES)
    merged_dirs: dict[str, list[str]] = {f"agent_{n}": [] for n in (1, 2, 3, 5, 6, 7, 8)}
    compile_notes: list[str] = []

    if not items:
        st["agent_directives"] = merged_dirs
        st["runtime_overrides"] = ro
        save_owner_feedback_state(st)
        return {"state": st, "notes": []}

    for it in items:
        d_ro, d_dirs, d_notes = _extract_deterministic_overrides_and_notes(str(it.get("text") or ""))
        for k, v in d_ro.items():
            if v not in (None, ""):
                ro[k] = v
        for ak, lst in d_dirs.items():
            for line in lst:
                if line not in merged_dirs.setdefault(ak, []):
                    merged_dirs[ak].append(line)
        for n in d_notes:
            if n not in compile_notes:
                compile_notes.append(n)

    if use_llm:
        try:
            from pydantic import BaseModel, Field
            from src.providers import call_structured_agent

            class _CompiledFeedbackPlan(BaseModel):
                summary_id: str = Field(description="1-2 kalimat ringkasan perubahan yang diterapkan untuk dikirim ke Telegram owner")
                agent_1: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 1 (Ideasi/Topik)")
                agent_2: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 2 (Seleksi Ide & Hook)")
                agent_3: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 3 (Skrip, Durasi, Diag. Animasi & SFX)")
                agent_5: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 5 (Video QA Auditor)")
                agent_6: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 6 (Pencari Footage Real & Klip)")
                agent_7: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 7 (Judul, Thumbnail, Caption & Pin Comment)")
                agent_8: list[str] = Field(default_factory=list, description="Instruksi konkret untuk Agent 8 (Audit Mingguan & Peta Topik)")
                visual_style_bias: str = Field(default="", description="Keyword/arahan visual singkat bahasa Inggris untuk Agent 6 jika relevan, atau ''")

            compiled = call_structured_agent(
                provider="auto",
                model="gemini-3.8-flash",
                system_prompt=(
                    "Kamu adalah Koordinator Studio Kimiabill yang menerjemahkan masukan natural owner di Telegram "
                    "menjadi instruksi operasional yang langsung dijalankan 100% oleh seluruh agent (Agent 1, 2, 3, 5, 6, 7, 8) "
                    "tanpa perlu campur tangan manusia.\n"
                    "Baca semua `owner_feedback_items`, lalu isi HANYA agent yang relevan dengan arahan singkat, tegas, dan actionable "
                    "dalam Bahasa Indonesia. Jangan mengarang aturan yang tidak diminta owner; kalau masukan berkaitan dengan SFX, "
                    "tekankan pada Agent 3 & 5 bahwa studio memakai SFX sintetis internal 100% bebas copyright (whoosh, pop, spark, ding, chime, riser)."
                ),
                user_payload={
                    "owner_feedback_items": items[-8:],
                    "existing_runtime_overrides": ro,
                },
                response_model=_CompiledFeedbackPlan,
            )
            for ak in ("agent_1", "agent_2", "agent_3", "agent_5", "agent_6", "agent_7", "agent_8"):
                for line in getattr(compiled, ak, []) or []:
                    clean_l = " ".join(str(line).strip().split())
                    if clean_l and clean_l not in merged_dirs[ak]:
                        merged_dirs[ak].append(clean_l)
            if (compiled.visual_style_bias or "").strip():
                ro["visual_style_bias"] = compiled.visual_style_bias.strip()[:160]
            if (compiled.summary_id or "").strip():
                compile_notes.insert(0, compiled.summary_id.strip())
        except Exception as exc:
            print(f"[telegram] Kompilasi LLM feedback dilewati (pakai rule deterministik): {exc}")

    # Guarantee raw owner lines are also preserved cleanly for core creative agents if no specific LLM split happened
    for it in reversed(items[-5:]):
        raw_txt = str(it.get("text") or "").strip()
        ts = str(it.get("created_at_wib") or "").strip()
        tag = f"[MASUKAN OWNER • {ts}]: {raw_txt}"
        for ak in ("agent_1", "agent_2", "agent_3", "agent_5", "agent_7", "agent_8"):
            if not any(raw_txt[:40].lower() in existing.lower() for existing in merged_dirs[ak]):
                merged_dirs[ak].append(tag)

    st["agent_directives"] = {k: v[:10] for k, v in merged_dirs.items()}
    st["runtime_overrides"] = ro
    save_owner_feedback_state(st)
    return {"state": st, "notes": compile_notes}


def record_owner_feedback(
    text: str,
    mode: str = "next_video",
    target_episode_id: str = "",
    update_id: Optional[int] = None,
    compile_agents: bool = True,
    use_llm_compiler: bool = True,
) -> dict[str, Any]:
    """Persist owner feedback from Telegram and compile it directly into all Agents + runtime engine overrides."""
    clean = " ".join((text or "").strip().split())
    st = load_owner_feedback_state()
    if update_id is not None and int(update_id) > int(st.get("last_update_id") or 0):
        st["last_update_id"] = int(update_id)
    if not clean:
        save_owner_feedback_state(st)
        return st

    items: list[dict[str, Any]] = [x for x in (st.get("items") or []) if isinstance(x, dict)]
    if not any(str(x.get("text", "")).strip().lower() == clean.lower() and x.get("mode") == mode for x in items[-5:]):
        now_wib = datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
        items.append(
            {
                "created_at_wib": now_wib,
                "mode": mode,
                "target_episode_id": target_episode_id,
                "text": clean[:500],
            }
        )
    st["items"] = items[-20:]
    save_owner_feedback_state(st)
    if compile_agents:
        res = compile_owner_feedback_state(use_llm=use_llm_compiler)
        return res["state"]
    return st


def clear_owner_feedback() -> None:
    st = load_owner_feedback_state()
    st["items"] = []
    st["agent_directives"] = {f"agent_{n}": [] for n in (1, 2, 3, 5, 6, 7, 8)}
    st["runtime_overrides"] = dict(DEFAULT_RUNTIME_OVERRIDES)
    save_owner_feedback_state(st)


def get_owner_runtime_overrides() -> dict[str, Any]:
    st = load_owner_feedback_state()
    ro = dict(DEFAULT_RUNTIME_OVERRIDES)
    if isinstance(st.get("runtime_overrides"), dict):
        ro.update(st["runtime_overrides"])
    return ro


def get_active_owner_feedback_directives(limit: int = 6) -> list[str]:
    """Return owner feedback lines ordered most recent first, including any immediate override env."""
    out: list[str] = []
    override = " ".join(os.environ.get("OWNER_FEEDBACK_OVERRIDE", "").strip().split())
    if override:
        out.append(f"[MASUKAN PRIORITAS OWNER - RENDER ULANG]: {override}")
    st = load_owner_feedback_state()
    for item in reversed((st.get("items") or [])[-limit:]):
        txt = str(item.get("text") or "").strip()
        if not txt:
            continue
        ts = str(item.get("created_at_wib") or "").strip()
        mode_tag = "RENDER ULANG" if item.get("mode") == "rerender" else "VIDEO BERIKUTNYA"
        out.append(f"[MASUKAN OWNER ({mode_tag} • {ts})]: {txt}")
    return out


def get_agent_specific_owner_directives(agent_key: str, limit: int = 8) -> list[str]:
    """Return compiled agent-specific directives merged with global owner feedback."""
    st = load_owner_feedback_state()
    if st.get("items") and not (st.get("agent_directives") or {}).get(agent_key):
        try:
            st = compile_owner_feedback_state(use_llm=False)["state"]
        except Exception:
            pass
    out: list[str] = []
    override = " ".join(os.environ.get("OWNER_FEEDBACK_OVERRIDE", "").strip().split())
    if override:
        out.append(f"[MASUKAN PRIORITAS OWNER - RENDER ULANG]: {override}")
    for line in (st.get("agent_directives") or {}).get(agent_key, []):
        s = str(line).strip()
        if s and s not in out:
            out.append(s)
    for raw_line in get_active_owner_feedback_directives(limit=4):
        if raw_line not in out:
            out.append(raw_line)
    return out[:limit]


def format_active_owner_rules_text() -> str:
    st = load_owner_feedback_state()
    items = st.get("items") or []
    ro = get_owner_runtime_overrides()
    lines = ["📋 ATURAN & MASUKAN OWNER AKTIF DI SEMUA AGENT:"]
    if not items:
        lines.append("• Belum ada masukan teks khusus terbaru.")
    else:
        for i, it in enumerate(items[-6:], 1):
            lines.append(f"{i}. ({it.get('created_at_wib', '-')}) {it.get('text', '')}")
    lines.extend(
        [
            "",
            "⚙️ SETELAN OTOMATIS ENGINE SAAT INI:",
            f"• SFX ganti footage: {'AKTIF' if ro.get('force_sfx_every_transition', True) else 'Opsional'}",
            f"• SFX setiap animasi/badge: {'AKTIF' if ro.get('force_sfx_every_animation', True) else 'Opsional'}",
            f"• Delta volume SFX: {ro.get('sfx_gain_db_delta', 0.0):+.1f} dB | BGM: {ro.get('bgm_gain_db_delta', 0.0):+.1f} dB",
            f"• Delta kecepatan suara: {int(ro.get('voice_speed_delta_pct', 0)):+d}%",
        ]
    )
    if ro.get("target_duration_sec"):
        lines.append(f"• Target durasi khusus: {ro['target_duration_sec'][0]}-{ro['target_duration_sec'][1]} detik")
    lines.append("\nKirim /clear_rules kalau ingin reset aturan tambahan.")
    return "\n".join(lines)


def handle_telegram_dispatch_feedback(text: str, mode: str = "next_video") -> dict[str, Any]:
    """Process an incoming webhook dispatch (normal feedback, /rules, or /clear_rules) directly to Agents 1-8."""
    clean = " ".join((text or "").strip().split())
    low = clean.lower()
    if not clean:
        return {"ok": True, "action": "empty"}

    if low.startswith("/rules") or low == "/aturan":
        msg = format_active_owner_rules_text()
        send_telegram_message(msg)
        return {"ok": True, "action": "rules_listed"}

    if low.startswith("/clear_rules") or low.startswith("/clear"):
        clear_owner_feedback()
        send_telegram_message("🧹 Semua aturan/masukan sementara owner berhasil di-reset ke setelan standar studio (SFX transisi & animasi tetap aktif).")
        return {"ok": True, "action": "rules_cleared"}

    record_owner_feedback(clean, mode=mode, compile_agents=False)
    comp = compile_owner_feedback_state(use_llm=True)
    notes = comp.get("notes") or []
    ro = (comp.get("state") or {}).get("runtime_overrides") or {}
    summary_bullets = "\n".join(f"• {n}" for n in notes[:4]) if notes else "• Arahan langsung disimpan & diterapkan ke prompt Agent 1, 2, 3, 5, 6, 7, dan 8."
    send_telegram_message(
        "\n".join(
            [
                "🧠 100% Diterapkan Otomatis ke Sistem Agent (1, 2, 3, 5, 6, 7, 8 + Engine):",
                summary_bullets,
                f"🎛️ Engine: SFX transisi={'ON' if ro.get('force_sfx_every_transition', True) else 'AUTO'}, SFX animasi={'ON' if ro.get('force_sfx_every_animation', True) else 'AUTO'}",
                "Ketik /rules untuk lihat daftar aturan aktif.",
            ]
        )
    )
    return {"ok": True, "action": "compiled", "notes": notes}


def sync_telegram_feedback_updates() -> dict[str, Any]:
    """Poll new Telegram owner messages when no Apps Script webhook is registered."""
    token, allowed_chat = _bot_creds()
    if not token or not allowed_chat:
        return {"ok": False, "skipped": "not_configured"}

    try:
        wh = requests.get(_api_url(token, "getWebhookInfo"), timeout=10).json()
        if wh.get("ok") and (wh.get("result") or {}).get("url"):
            return {"ok": True, "skipped": "webhook_active"}
    except Exception:
        pass

    st = load_owner_feedback_state()
    offset = int(st.get("last_update_id") or 0) + 1
    try:
        resp = requests.get(
            _api_url(token, "getUpdates"),
            params={"offset": offset, "timeout": 1, "limit": 20},
            timeout=15,
        )
        data = resp.json() if resp.status_code == 200 else {}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if not data.get("ok"):
        return {"ok": False, "error": data}

    saved_count = 0
    max_uid = int(st.get("last_update_id") or 0)
    for upd in data.get("result") or []:
        uid = int(upd.get("update_id") or 0)
        if uid > max_uid:
            max_uid = uid
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        if chat_id != str(allowed_chat):
            continue
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        if not text:
            continue
        low = text.lower()
        if low in ("/start", "/status", "/help"):
            continue
        if low.startswith("/rules") or low == "/aturan":
            send_telegram_message(format_active_owner_rules_text())
            continue
        if low.startswith("/clear"):
            clear_owner_feedback()
            send_telegram_message("🧹 Daftar masukan owner untuk video berikutnya sudah dikosongkan.")
            continue
        if low.startswith("/rerender"):
            note = text[len("/rerender"):].strip()
            if note:
                record_owner_feedback(note, mode="rerender", update_id=uid, use_llm_compiler=True)
                saved_count += 1
                send_telegram_message(f"🔄 Catatan render ulang diterapkan langsung ke semua Agent & produksi kali ini: {note}")
            continue
        if low.startswith("/feedback"):
            text = text[len("/feedback"):].strip()
        if text:
            record_owner_feedback(text, mode="next_video", update_id=uid, use_llm_compiler=True)
            saved_count += 1
            send_telegram_message(f"✅ Masukan dicatat & dikompilasi ke Agent 1-8:\n\"{text}\"")

    if max_uid > int(st.get("last_update_id") or 0):
        st_latest = load_owner_feedback_state()
        st_latest["last_update_id"] = max_uid
        save_owner_feedback_state(st_latest)
    return {"ok": True, "saved_count": saved_count, "last_update_id": max_uid}


def notify_production_started(
    episode_id: str,
    section_label: str = "REGULER",
    pending_request: Optional[dict[str, Any]] = None,
) -> bool:
    """Send Notification #1 to owner Telegram right when GitHub Actions starts Agent 1 production."""
    if not telegram_enabled():
        return False

    now_wib = datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    ev_name = os.environ.get("GITHUB_EVENT_NAME", "").strip().lower()
    if ev_name == "schedule":
        trigger_label = "Jadwal Otomatis (Cron)"
    elif ev_name == "repository_dispatch":
        trigger_label = "Perintah Telegram (/rerender)"
    elif ev_name == "workflow_dispatch":
        trigger_label = "Trigger Manual GitHub"
    else:
        trigger_label = "Scheduler Lokal"

    target_pub = (
        os.environ.get("TARGET_PUBLISH_TIME_WIB", "").strip()
        or os.environ.get("PUBLISH_TIME_WIB", "").strip()
    )
    pub_mode_now = os.environ.get("PUBLISH_MODE", "draft").strip().lower()
    if pub_mode_now == "draft":
        pub_label = target_pub or "18:30"
        target_line = f"🔍 Mode Audit Manual (Draft Unlisted ~17:06 WIB → Target Tayang {pub_label} WIB)"
    elif target_pub:
        target_line = f"⏰ Target Tayang: {target_pub} WIB"
    else:
        target_line = "⚡ Publish segera setelah lolos QA"

    req_line = ""
    if pending_request and pending_request.get("topic_title"):
        req_line = f"🙋 Request Penonton: {pending_request.get('requester_name', '')} → {pending_request.get('topic_title')}"

    st = load_owner_feedback_state()
    owner_items = st.get("items") or []
    override_txt = os.environ.get("OWNER_FEEDBACK_OVERRIDE", "").strip()
    fb_notes_count = len(owner_items) + (1 if override_txt else 0)
    fb_line = (
        f"🧠 Aturan/Masukan Owner Aktif: {fb_notes_count} catatan diterapkan di Agent 1-8"
        if fb_notes_count
        else "🎛️ Setelan Standar Studio + SFX Transisi & Animasi Aktif"
    )

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if (repo and run_id) else ""

    lines = [
        f"🚀 MULAI PRODUKSI VIDEO ({section_label})",
        f"• Episode: {episode_id}",
        f"• Mulai: {now_wib} | Pemicu: {trigger_label}",
        f"• {target_line}",
        f"• {fb_line}",
    ]
    if req_line:
        lines.append(f"• {req_line}")
    if run_url:
        lines.append(f"• Log GitHub: {run_url}")
    lines.append("")
    lines.append("⏳ Sedang memproses Agent 1 (Ideasi) → Skrip → Footage → Render & QA. Info ke-2 akan dikirim otomatis saat video siap & terupload!")
    return send_telegram_message("\n".join(lines))

