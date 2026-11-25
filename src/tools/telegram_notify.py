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


def save_owner_feedback_state(state: dict[str, Any]) -> None:
    OWNER_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_FEEDBACK_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_owner_feedback(
    text: str,
    mode: str = "next_video",
    target_episode_id: str = "",
    update_id: Optional[int] = None,
) -> dict[str, Any]:
    """Persist owner feedback from Telegram so Agent 1/2/3/5 apply it on upcoming videos."""
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
    return st


def clear_owner_feedback() -> None:
    st = load_owner_feedback_state()
    st["items"] = []
    save_owner_feedback_state(st)


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
        if low.startswith("/clear"):
            clear_owner_feedback()
            send_telegram_message("🧹 Daftar masukan owner untuk video berikutnya sudah dikosongkan.")
            continue
        if low.startswith("/rerender"):
            note = text[len("/rerender"):].strip()
            if note:
                record_owner_feedback(note, mode="rerender", update_id=uid)
                saved_count += 1
                send_telegram_message(f"🔄 Catatan render ulang diterapkan ke produksi kali ini: {note}")
            continue
        if low.startswith("/feedback"):
            text = text[len("/feedback"):].strip()
        if text:
            record_owner_feedback(text, mode="next_video", update_id=uid)
            saved_count += 1
            send_telegram_message(f"✅ Masukan dicatat untuk video ini & berikutnya:\n\"{text}\"")

    if max_uid > int(st.get("last_update_id") or 0):
        st_latest = load_owner_feedback_state()
        st_latest["last_update_id"] = max_uid
        save_owner_feedback_state(st_latest)
    return {"ok": True, "saved_count": saved_count, "last_update_id": max_uid}
