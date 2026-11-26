"""Locked Indonesian male voiceover engine (Edge-TTS id-ID-ArdiNeural).

Features:
- Separates clean Indonesian baku subtitle text from phonetic TTS text via `config/pronunciation_dict.json`.
- Locks ONE voice (`id-ID-ArdiNeural`), rate, pitch, and volume across every scene in an episode
  (zero silent fallback to any other voice/engine mid-video; uses retry + exponential backoff).
- Aggressive FFmpeg silence trimming (max 0.15-0.20s gaps and zero tail dead-air) for crisp Shorts pacing.
- Concurrent Edge-TTS generation across scenes for fast execution.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import edge_tts

from src.state import SceneSpec, SubtitleChunk

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PRONUNCIATION_DICT_PATH = BASE_DIR / "config" / "pronunciation_dict.json"
LOCKED_DEFAULT_VOICE = "id-ID-ArdiNeural"


@dataclass
class SceneVoiceResult:
    scene_id: str
    audio_path: str
    start_sec: float
    end_sec: float
    duration_sec: float
    subtitles: list[SubtitleChunk]


@lru_cache(maxsize=4)
def load_pronunciation_dict(dict_path: str = "") -> dict[str, Any]:
    path = Path(dict_path) if dict_path else PRONUNCIATION_DICT_PATH
    if not path.exists():
        return {"phonetic_map": {}, "typo_fixes": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("phonetic_map", {})
            data.setdefault("typo_fixes", [])
            return data
    except Exception:
        pass
    return {"phonetic_map": {}, "typo_fixes": []}


def sanitize_indonesian_script(text: str) -> str:
    """Apply deterministic Indonesian proofreading & typo cleanup for subtitles/narration."""
    s = " ".join((text or "").strip().split())
    if not s:
        return ""
    pdict = load_pronunciation_dict()
    for rule in pdict.get("typo_fixes") or []:
        pat = rule.get("pattern")
        rep = rule.get("replacement", "")
        if pat:
            flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
            s = re.sub(pat, rep, s, flags=flags)
    # Remove accidental repeated spaces/punctuation left by deletions
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([!?.])\s+([!?.])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def to_tts_phonetic(text: str) -> str:
    """Convert clean baku Indonesian text into Edge-TTS id-ID-ArdiNeural phonetic pronunciation."""
    s = sanitize_indonesian_script(text)
    if not s:
        return ""
    pdict = load_pronunciation_dict()
    pmap: dict[str, str] = pdict.get("phonetic_map") or {}
    # Sort longest tokens first to match multi-char scientific terms accurately
    for term in sorted(pmap.keys(), key=len, reverse=True):
        phonetic = str(pmap[term]).strip()
        if not term or not phonetic:
            continue
        pattern = re.compile(rf"\b{re.escape(term)}\b", flags=re.IGNORECASE)
        s = pattern.sub(phonetic, s)
    return re.sub(r"\s{2,}", " ", s).strip()


def probe_duration(media_path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        text=True,
    ).strip()
    return float(out)


def _group_words_to_chunks(
    word_events: list[tuple[float, float, str]],
    offset_sec: float,
    max_words: int = 4,
    max_chars: int = 24,
) -> list[SubtitleChunk]:
    chunks: list[SubtitleChunk] = []
    buf: list[tuple[float, float, str]] = []
    cur_chars = 0

    def flush() -> None:
        nonlocal buf, cur_chars
        if not buf:
            return
        text = " ".join(w[2] for w in buf).strip()
        start = round(offset_sec + buf[0][0], 3)
        end = round(offset_sec + buf[-1][1], 3)
        if end <= start:
            end = round(start + 0.22, 3)
        chunks.append(SubtitleChunk(start=start, end=end, text=text))
        buf = []
        cur_chars = 0

    for w_start, w_end, word in word_events:
        pause_gap = (w_start - buf[-1][1]) if buf else 0.0
        if buf and (
            pause_gap > 0.18
            or len(buf) >= max_words
            or cur_chars + len(word) + 1 > max_chars
            or buf[-1][2].endswith((".", "!", "?", ","))
        ):
            flush()
        buf.append((w_start, w_end, word))
        cur_chars += len(word) + 1
    flush()
    return chunks


def _aligned_baku_subtitles(
    baku_text: str,
    offset_sec: float,
    speech_duration: float,
    max_words: int = 4,
    max_chars: int = 24,
) -> list[SubtitleChunk]:
    """Generate tight, synced subtitle chunks strictly from clean baku Indonesian text."""
    words = [w for w in baku_text.strip().split() if w]
    if not words:
        return []
    weights = [max(len(w), 2) + (2.5 if w.endswith((".", "!", "?", ",")) else 0.0) for w in words]
    total_w = sum(weights)
    usable_dur = max(0.25, speech_duration - 0.04)
    cursor = 0.02
    word_events: list[tuple[float, float, str]] = []
    for w, wt in zip(words, weights):
        dur = usable_dur * (wt / total_w)
        word_events.append((cursor, cursor + dur, w))
        cursor += dur
    return _group_words_to_chunks(word_events, offset_sec, max_words=max_words, max_chars=max_chars)


async def _synth_one_scene_edge_locked(
    tts_text: str,
    out_mp3: Path,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    max_attempts: int = 5,
) -> list[tuple[float, float, str]]:
    """Synthesize a single scene strictly with the locked voice + exponential backoff (never switches voices)."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            communicate = edge_tts.Communicate(
                text=tts_text,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
                boundary="WordBoundary",
            )
            word_events: list[tuple[float, float, str]] = []
            with open(out_mp3, "wb") as fh:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start_s = chunk["offset"] / 10_000_000.0
                        dur_s = chunk["duration"] / 10_000_000.0
                        word_events.append((start_s, start_s + dur_s, chunk["text"]))
            if out_mp3.exists() and out_mp3.stat().st_size > 512:
                return word_events
            raise RuntimeError(f"Empty Edge-TTS output file for {out_mp3.name}")
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                backoff = min(16.0, float(2 ** attempt))
                print(
                    f"[tts] Edge-TTS ({voice}) percobaan {attempt + 1}/{max_attempts} gagal ({exc}); "
                    f"retry voice yang sama dalam {backoff:.0f}s..."
                )
                await asyncio.sleep(backoff)
    raise RuntimeError(
        f"TTS gagal permanen pada locked voice '{voice}' setelah {max_attempts} percobaan: {last_exc}"
    )


async def _synth_all_scenes_concurrently(
    tts_texts: list[str],
    raw_mp3_paths: list[Path],
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
) -> list[list[tuple[float, float, str]]]:
    sem = asyncio.Semaphore(3)

    async def _worker(txt: str, path: Path) -> list[tuple[float, float, str]]:
        async with sem:
            return await _synth_one_scene_edge_locked(
                tts_text=txt,
                out_mp3=path,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
            )

    return await asyncio.gather(*(_worker(t, p) for t, p in zip(tts_texts, raw_mp3_paths)))


def _make_silence_wav(path: Path, duration_sec: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{duration_sec:.3f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def generate_episode_voiceover(
    scenes: list[SceneSpec],
    out_dir: Path,
    voice: str = LOCKED_DEFAULT_VOICE,
    rate: str = "+4%",
    pitch: str = "+4Hz",
    volume: str = "+0%",
    inter_scene_pause_sec: float = 0.15,
    tail_pause_sec: float = 0.15,
) -> tuple[str, list[SceneVoiceResult], list[SubtitleChunk], float]:
    """Synthesize locked `id-ID-ArdiNeural` voiceover, trim dead silence <=0.16s, and build clean baku subtitles."""
    locked_voice = (voice or LOCKED_DEFAULT_VOICE).strip()
    locked_rate = (rate or "+4%").strip()
    locked_pitch = (pitch or "+4Hz").strip()
    locked_volume = (volume or "+0%").strip()
    inter_pause = max(0.08, min(0.20, float(inter_scene_pause_sec)))
    end_tail = max(0.10, min(0.20, float(tail_pause_sec)))

    out_dir.mkdir(parents=True, exist_ok=True)
    voice_parts_dir = out_dir / "voice_parts"
    voice_parts_dir.mkdir(parents=True, exist_ok=True)

    baku_texts = [sanitize_indonesian_script(sc.narration) for sc in scenes]
    tts_texts = [to_tts_phonetic(baku) for baku in baku_texts]
    raw_mp3s = [voice_parts_dir / f"{sc.scene_id}.mp3" for sc in scenes]

    print(
        f"[tts] Locked Edge-TTS voice='{locked_voice}' rate='{locked_rate}' "
        f"pitch='{locked_pitch}' volume='{locked_volume}' ({len(scenes)} scenes)"
    )

    asyncio.run(
        _synth_all_scenes_concurrently(
            tts_texts=tts_texts,
            raw_mp3_paths=raw_mp3s,
            voice=locked_voice,
            rate=locked_rate,
            pitch=locked_pitch,
            volume=locked_volume,
        )
    )

    pause_wav = voice_parts_dir / "pause.wav"
    _make_silence_wav(pause_wav, inter_pause)
    tail_wav = voice_parts_dir / "tail_pause.wav"
    _make_silence_wav(tail_wav, end_tail)

    scene_results: list[SceneVoiceResult] = []
    all_subtitles: list[SubtitleChunk] = []
    concat_inputs: list[Path] = []
    cursor = 0.0

    for idx, (scene, baku_txt, raw_mp3) in enumerate(zip(scenes, baku_texts, raw_mp3s)):
        clean_wav = voice_parts_dir / f"{scene.scene_id}.wav"
        # Trim leading/trailing silence AND cap intra-sentence pauses to max 0.16s
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(raw_mp3),
                "-ar",
                "44100",
                "-ac",
                "1",
                "-af",
                (
                    "silenceremove=start_periods=1:start_threshold=-42dB:detection=peak,"
                    "areverse,silenceremove=start_periods=1:start_threshold=-42dB:detection=peak,areverse,"
                    "silenceremove=stop_periods=-1:stop_duration=0.16:stop_threshold=-42dB"
                ),
                "-c:a",
                "pcm_s16le",
                str(clean_wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        speech_dur = probe_duration(clean_wav)
        subs = _aligned_baku_subtitles(
            baku_text=baku_txt,
            offset_sec=cursor,
            speech_duration=speech_dur,
        )

        is_last = idx == len(scenes) - 1
        tail_sec = end_tail if is_last else inter_pause
        seg_dur = speech_dur + tail_sec
        scene_results.append(
            SceneVoiceResult(
                scene_id=scene.scene_id,
                audio_path=str(clean_wav),
                start_sec=round(cursor, 3),
                end_sec=round(cursor + seg_dur, 3),
                duration_sec=round(seg_dur, 3),
                subtitles=subs,
            )
        )
        all_subtitles.extend(subs)
        concat_inputs.append(clean_wav)
        concat_inputs.append(tail_wav if is_last else pause_wav)
        cursor += seg_dur

    concat_list = voice_parts_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in concat_inputs), encoding="utf-8"
    )
    full_voice = out_dir / "voiceover_full.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-af",
            "loudnorm=I=-15:TP=-1.5:LRA=9",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(full_voice),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    total_dur = probe_duration(full_voice)
    return str(full_voice), scene_results, all_subtitles, total_dur
