"""Memory DB (topic deduplication) and local reusable SFX + BGM cache."""
from __future__ import annotations

import json
import math
import sqlite3
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np


class StudioMemory:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "data" / "memory.db"
        self.sfx_dir = self.base_dir / "data" / "sfx_cache"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.sync_idea_bank_from_json()
        self.ensure_sfx_cache()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    phenomenon TEXT NOT NULL,
                    discipline TEXT DEFAULT 'kimia',
                    status TEXT NOT NULL,
                    final_score REAL,
                    gdrive_url TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Migrate older DBs smoothly if discipline column is missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)").fetchall()}
            if "discipline" not in cols:
                conn.execute("ALTER TABLE episodes ADD COLUMN discipline TEXT DEFAULT 'kimia'")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idea_bank (
                    id TEXT PRIMARY KEY,
                    discipline TEXT NOT NULL,
                    title TEXT NOT NULL,
                    phenomenon TEXT NOT NULL,
                    hook_3s TEXT NOT NULL,
                    real_footage_keywords TEXT NOT NULL,
                    used_by_episode TEXT DEFAULT NULL
                )
                """
            )
            conn.commit()

    def sync_idea_bank_from_json(self) -> int:
        bank_json = self.base_dir / "data" / "idea_bank.json"
        if not bank_json.exists():
            return 0
        payload = json.loads(bank_json.read_text(encoding="utf-8"))
        ideas = payload.get("ideas", [])
        with sqlite3.connect(self.db_path) as conn:
            for item in ideas:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO idea_bank
                    (id, discipline, title, phenomenon, hook_3s, real_footage_keywords)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["discipline"],
                        item["title"],
                        item["phenomenon"],
                        item["hook_3s"],
                        json.dumps(item.get("real_footage_keywords", []), ensure_ascii=False),
                    ),
                )
            conn.commit()
        return len(ideas)

    def mark_idea_used(self, idea_id: str, episode_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE idea_bank SET used_by_episode = ? WHERE id = ?",
                (episode_id, idea_id),
            )
            conn.commit()

    def discipline_counts(self) -> dict[str, int]:
        counts = {d: 0 for d in ("kimia", "fisika", "matematika", "medisinal", "biologi")}
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT discipline, COUNT(*) FROM episodes GROUP BY discipline"
            ).fetchall()
        for d, cnt in rows:
            if d in counts:
                counts[d] = cnt
        return counts

    def get_balanced_unused_ideas(self, count: int = 5) -> list[dict]:
        """Pick unused ideas from idea_bank prioritizing underrepresented pure-science disciplines."""
        counts = self.discipline_counts()
        ordered_disciplines = sorted(counts.keys(), key=lambda d: counts[d])
        picked: list[dict] = []
        picked_ids: set[str] = set()

        with sqlite3.connect(self.db_path) as conn:
            # First pass: 1 unused idea from each least-covered discipline
            for disc in ordered_disciplines:
                if len(picked) >= count:
                    break
                row = conn.execute(
                    """
                    SELECT id, discipline, title, phenomenon, hook_3s, real_footage_keywords
                    FROM idea_bank
                    WHERE used_by_episode IS NULL AND discipline = ?
                    ORDER BY id ASC LIMIT 1
                    """,
                    (disc,),
                ).fetchone()
                if row and row[0] not in picked_ids:
                    picked_ids.add(row[0])
                    picked.append(
                        {
                            "id": row[0],
                            "discipline": row[1],
                            "title": row[2],
                            "phenomenon": row[3],
                            "hook_3s": row[4],
                            "real_footage_keywords": json.loads(row[5]),
                        }
                    )
            # Fill remaining slots if needed
            if len(picked) < count:
                rows = conn.execute(
                    """
                    SELECT id, discipline, title, phenomenon, hook_3s, real_footage_keywords
                    FROM idea_bank
                    WHERE used_by_episode IS NULL
                    ORDER BY id ASC
                    """
                ).fetchall()
                for row in rows:
                    if len(picked) >= count:
                        break
                    if row[0] not in picked_ids:
                        picked_ids.add(row[0])
                        picked.append(
                            {
                                "id": row[0],
                                "discipline": row[1],
                                "title": row[2],
                                "phenomenon": row[3],
                                "hook_3s": row[4],
                                "real_footage_keywords": json.loads(row[5]),
                            }
                        )
        return picked

    def list_past_topics(self, limit: int = 30) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT slug, title, phenomenon, discipline, status FROM episodes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"slug": r[0], "title": r[1], "phenomenon": r[2], "discipline": r[3], "status": r[4]}
            for r in rows
        ]

    def record_episode(
        self,
        episode_id: str,
        slug: str,
        title: str,
        phenomenon: str,
        status: str,
        discipline: str = "kimia",
        final_score: Optional[float] = None,
        gdrive_url: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes
                (episode_id, slug, title, phenomenon, discipline, status, final_score, gdrive_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (episode_id, slug, title, phenomenon, discipline, status, final_score, gdrive_url, now),
            )
            conn.commit()

    def get_sfx_path(self, cue: Optional[str]) -> Optional[str]:
        if not cue or cue == "none":
            return None
        path = self.sfx_dir / f"{cue}.wav"
        return str(path) if path.exists() else None

    def get_bgm_path(self) -> str:
        path = self.sfx_dir / "bgm_curious_science.wav"
        if not path.exists():
            self.ensure_sfx_cache()
        return str(path)

    def ensure_sfx_cache(self) -> None:
        """Synthesize clean, royalty-free reusable SFX & subtle science BGM bed once."""
        sr = 44100

        def save_wav(path: Path, samples: np.ndarray) -> None:
            if path.exists():
                return
            peak = float(np.max(np.abs(samples))) if len(samples) else 1.0
            if peak > 0.95:
                samples = samples / peak * 0.92
            pcm = (samples * 32767).astype(np.int16)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(pcm.tobytes())

        # 1. Whoosh (soft filtered noise sweep)
        t = np.linspace(0, 0.42, int(sr * 0.42), endpoint=False)
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 1, len(t))
        # Smooth bandpass-like moving average + envelope
        kernel = np.hanning(35)
        kernel /= kernel.sum()
        filtered = np.convolve(noise, kernel, mode="same")
        env = np.sin(np.pi * t / t[-1]) ** 1.6
        tone = 0.25 * np.sin(2 * np.pi * (220 + 520 * (t / t[-1])) * t)
        save_wav(self.sfx_dir / "whoosh.wav", (filtered * 0.75 + tone) * env * 0.85)

        # 2. Pop (clean upbeat bubble pop)
        t = np.linspace(0, 0.16, int(sr * 0.16), endpoint=False)
        freq = 320 + 680 * np.exp(-t * 28)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        env = np.exp(-t * 26) * np.minimum(1.0, t / 0.006)
        save_wav(self.sfx_dir / "pop.wav", np.sin(phase) * env * 0.85)

        # 3. Ding (bright educational discovery bell)
        t = np.linspace(0, 0.65, int(sr * 0.65), endpoint=False)
        sig = (
            0.65 * np.sin(2 * np.pi * 1046.5 * t)
            + 0.25 * np.sin(2 * np.pi * 2093.0 * t)
            + 0.12 * np.sin(2 * np.pi * 3136.0 * t)
        )
        env = np.exp(-t * 5.5) * np.minimum(1.0, t / 0.008)
        save_wav(self.sfx_dir / "ding.wav", sig * env * 0.8)

        # 4. Chime (warm 2-note science reveal C6 -> E6)
        t = np.linspace(0, 0.75, int(sr * 0.75), endpoint=False)
        n1 = np.sin(2 * np.pi * 523.25 * t) * np.exp(-t * 4.5)
        t2 = np.maximum(0.0, t - 0.14)
        mask2 = (t >= 0.14).astype(float)
        n2 = mask2 * np.sin(2 * np.pi * 659.25 * t2) * np.exp(-t2 * 4.5)
        n3 = mask2 * 0.4 * np.sin(2 * np.pi * 783.99 * t2) * np.exp(-t2 * 5.0)
        save_wav(self.sfx_dir / "chime.wav", (n1 * 0.55 + n2 * 0.6 + n3 * 0.4) * 0.8)

        # 5. Riser (subtle curiosity swell)
        t = np.linspace(0, 0.75, int(sr * 0.75), endpoint=False)
        freq = 180 + 420 * (t / t[-1]) ** 2
        phase = 2 * np.pi * np.cumsum(freq) / sr
        env = (t / t[-1]) ** 1.8 * np.minimum(1.0, (t[-1] - t) / 0.05)
        save_wav(self.sfx_dir / "riser.wav", np.sin(phase) * env * 0.65)

        # 6. BGM: upbeat, subtle curiosity plucks + warm pad (loopable 16s bed)
        bgm_path = self.sfx_dir / "bgm_curious_science.wav"
        if not bgm_path.exists():
            dur = 16.0
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            chords = [
                (261.63, 329.63, 392.00),  # C major
                (220.00, 261.63, 329.63),  # A minor
                (174.61, 220.00, 261.63),  # F major
                (196.00, 246.94, 293.66),  # G major
            ]
            pad = np.zeros_like(t)
            pluck = np.zeros_like(t)
            for idx, chord in enumerate(chords):
                c_start = idx * 4.0
                c_end = (idx + 1) * 4.0
                mask = (t >= c_start) & (t < c_end)
                lt = t[mask] - c_start
                c_env = np.sin(np.pi * lt / 4.0) ** 0.4
                for f in chord:
                    pad[mask] += 0.14 * np.sin(2 * np.pi * f * lt) * c_env
                # Gentle upbeat marimba-like eighth-note curiosity pattern
                notes = [chord[0] * 2, chord[1] * 2, chord[2] * 2, chord[1] * 2] * 2
                for step, nf in enumerate(notes):
                    s_start = c_start + step * 0.5
                    s_end = min(dur, s_start + 0.45)
                    smask = (t >= s_start) & (t < s_end)
                    st = t[smask] - s_start
                    senv = np.exp(-st * 9.5) * np.minimum(1.0, st / 0.008)
                    pluck[smask] += 0.22 * (
                        np.sin(2 * np.pi * nf * st) + 0.25 * np.sin(2 * np.pi * nf * 2 * st)
                    ) * senv
            mix = (pad * 0.55 + pluck * 0.45) * 0.65
            save_wav(bgm_path, mix)
