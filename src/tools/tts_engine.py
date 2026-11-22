"""Cheerful Indonesian male voiceover engine (Edge-TTS id-ID-ArdiNeural)
with exact scene duration tracking and kinetic subtitle chunk timing.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from src.state import SceneSpec, SubtitleChunk


@dataclass
class SceneVoiceResult:
    scene_id: str
    audio_path: str
    start_sec: float
    end_sec: float
    duration_sec: float
    subtitles: list[SubtitleChunk]


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
            end = round(start + 0.25, 3)
        chunks.append(SubtitleChunk(start=start, end=end, text=text))
        buf = []
        cur_chars = 0

    for w_start, w_end, word in word_events:
        pause_gap = (w_start - buf[-1][1]) if buf else 0.0
        if buf and (
            pause_gap > 0.20
            or len(buf) >= max_words
            or cur_chars + len(word) + 1 > max_chars
            or buf[-1][2].endswith((".", "!", "?", ","))
        ):
            flush()
        buf.append((w_start, w_end, word))
        cur_chars += len(word) + 1
    flush()
    return chunks


def _fallback_proportional_chunks(
    text: str,
    offset_sec: float,
    speech_duration: float,
    max_words: int = 4,
    max_chars: int = 24,
) -> list[SubtitleChunk]:
    words = [w for w in text.strip().split() if w]
    if not words:
        return []
    weights = [max(len(w), 2) + (3 if w.endswith((".", "!", "?", ",")) else 0) for w in words]
    total_w = sum(weights)
    usable_dur = max(0.3, speech_duration - 0.08)
    cursor = 0.03
    word_events: list[tuple[float, float, str]] = []
    for w, wt in zip(words, weights):
        dur = usable_dur * (wt / total_w)
        word_events.append((cursor, cursor + dur, w))
        cursor += dur
    return _group_words_to_chunks(word_events, offset_sec, max_words=max_words, max_chars=max_chars)


async def _synth_one_scene(
    text: str,
    out_mp3: Path,
    voice: str,
    rate: str,
    pitch: str,
) -> list[tuple[float, float, str]]:
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch,
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
    return word_events


def generate_episode_voiceover(
    scenes: list[SceneSpec],
    out_dir: Path,
    voice: str = "id-ID-ArdiNeural",
    rate: str = "+2%",
    pitch: str = "+4Hz",
    inter_scene_pause_sec: float = 0.24,
) -> tuple[str, list[SceneVoiceResult], list[SubtitleChunk], float]:
    """Generate cheerful male voiceover per scene, stitch smoothly, and return exact timing."""
    out_dir.mkdir(parents=True, exist_ok=True)
    voice_parts_dir = out_dir / "voice_parts"
    voice_parts_dir.mkdir(parents=True, exist_ok=True)

    scene_results: list[SceneVoiceResult] = []
    all_subtitles: list[SubtitleChunk] = []
    concat_inputs: list[Path] = []

    # Generate tiny silence WAV for natural breath between scenes
    pause_wav = voice_parts_dir / "pause.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{inter_scene_pause_sec:.3f}",
            "-c:a",
            "pcm_s16le",
            str(pause_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cursor = 0.0
    for idx, scene in enumerate(scenes):
        raw_mp3 = voice_parts_dir / f"{scene.scene_id}.mp3"
        clean_wav = voice_parts_dir / f"{scene.scene_id}.wav"

        word_events = asyncio.run(
            _synth_one_scene(scene.narration, raw_mp3, voice=voice, rate=rate, pitch=pitch)
        )
        # Convert to clean 44.1kHz mono WAV and trim excessive trailing silence
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
                "silenceremove=start_periods=1:start_threshold=-45dB:detection=peak,"
                "areverse,silenceremove=start_periods=1:start_threshold=-45dB:detection=peak,areverse",
                "-c:a",
                "pcm_s16le",
                str(clean_wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        speech_dur = probe_duration(clean_wav)
        if word_events:
            # Clamp word timestamps to trimmed audio duration proportionally
            max_ev = max(e[1] for e in word_events)
            scale = speech_dur / max_ev if max_ev > 0 else 1.0
            scaled = [(s * scale, e * scale, w) for s, e, w in word_events]
            subs = _group_words_to_chunks(scaled, offset_sec=cursor)
        else:
            subs = _fallback_proportional_chunks(
                scene.narration, offset_sec=cursor, speech_duration=speech_dur
            )

        is_last = idx == len(scenes) - 1
        seg_dur = speech_dur + (0.45 if is_last else inter_scene_pause_sec)
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
        if not is_last:
            concat_inputs.append(pause_wav)
        else:
            tail_wav = voice_parts_dir / "tail_pause.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "0.45",
                    "-c:a",
                    "pcm_s16le",
                    str(tail_wav),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            concat_inputs.append(tail_wav)
        cursor += seg_dur

    # Stitch all voice parts cleanly into one normalized narration track
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
