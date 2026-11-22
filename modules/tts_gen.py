"""Indonesian TTS generator using edge-tts with style presets and SRT subtitle generation."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import edge_tts


ALLOWED_VOICES = {
    "id-ID-ArdiNeural": "Ardi (Pria)",
    "id-ID-GadisNeural": "Gadis (Wanita)",
}

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "Edukasi": {"rate": "+0%", "pitch": "+0Hz"},
    "Entertainment": {"rate": "+12%", "pitch": "+2Hz"},
    "Storytelling": {"rate": "-6%", "pitch": "-2Hz"},
}


def get_audio_duration(audio_path: str | Path) -> float:
    """Measure exact audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        val = float(res.stdout.strip())
        return max(val, 0.5)
    except Exception:
        return 3.0


def _format_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    minutes = rem // 60_000
    rem %= 60_000
    secs = rem // 1000
    ms = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _group_word_cues(word_events: list[dict[str, Any]], max_words: int = 5, max_chars: int = 34) -> list[dict[str, Any]]:
    """Group word-boundary timings into crisp, readable subtitle lines."""
    cues: list[dict[str, Any]] = []
    buf_words: list[str] = []
    start_s: float | None = None
    end_s: float = 0.0

    def flush() -> None:
        nonlocal buf_words, start_s, end_s
        if buf_words and start_s is not None:
            cues.append(
                {
                    "start": start_s,
                    "end": max(end_s, start_s + 0.35),
                    "text": " ".join(buf_words).strip(),
                }
            )
        buf_words = []
        start_s = None

    for ev in word_events:
        word = str(ev.get("text") or "").strip()
        if not word:
            continue
        w_start = ev["offset"] / 10_000_000.0
        w_end = (ev["offset"] + ev["duration"]) / 10_000_000.0

        projected = " ".join(buf_words + [word])
        if buf_words and (len(buf_words) >= max_words or len(projected) > max_chars):
            flush()

        if start_s is None:
            start_s = w_start
        buf_words.append(word)
        end_s = w_end

        # Prefer breaking cleanly at sentence punctuation
        if word.endswith((".", "!", "?", ",")) and len(buf_words) >= 3:
            flush()

    flush()
    return cues


def _fallback_cues_from_text(text: str, duration: float, max_words: int = 5) -> list[dict[str, Any]]:
    """Create evenly proportioned subtitle cues if edge-tts emits no boundary metadata."""
    words = (text or "").strip().split()
    if not words:
        return []
    chunks: list[str] = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i : i + max_words]))

    total_chars = sum(max(len(c), 1) for c in chunks)
    cursor = 0.0
    cues: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        portion = duration * (max(len(chunk), 1) / total_chars)
        end = duration if idx == len(chunks) - 1 else min(duration, cursor + portion)
        cues.append({"start": cursor, "end": max(end, cursor + 0.25), "text": chunk})
        cursor = end
    return cues


def write_srt_file(cues: list[dict[str, Any]], srt_path: str | Path) -> str:
    """Write a list of {'start', 'end', 'text'} cues to a UTF-8 .srt file."""
    out = Path(srt_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        start_str = _format_srt_time(float(cue["start"]))
        end_str = _format_srt_time(float(cue["end"]))
        blocks.append(f"{idx}\n{start_str} --> {end_str}\n{text}\n")
    out.write_text("\n".join(blocks), encoding="utf-8")
    return str(out)


async def generate_scene_audio(
    text: str,
    output_mp3: str | Path,
    voice: str = "id-ID-ArdiNeural",
    gaya_bahasa: str = "Edukasi",
    generate_srt: bool = True,
    output_srt: str | Path | None = None,
) -> dict[str, Any]:
    """Synthesize a single scene narration MP3 with edge-tts and collect subtitle cues."""
    if voice not in ALLOWED_VOICES:
        voice = "id-ID-ArdiNeural"
    preset = STYLE_PRESETS.get(gaya_bahasa, STYLE_PRESETS["Edukasi"])
    clean_text = (text or "").strip()
    if not clean_text:
        clean_text = "Mari kita simak penjelasan berikutnya."

    mp3_path = Path(output_mp3)
    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    communicate = edge_tts.Communicate(
        text=clean_text,
        voice=voice,
        rate=preset["rate"],
        pitch=preset["pitch"],
    )

    word_events: list[dict[str, Any]] = []
    sentence_events: list[dict[str, Any]] = []

    with mp3_path.open("wb") as audio_file:
        async for chunk in communicate.stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                audio_file.write(chunk["data"])
            elif ctype == "WordBoundary":
                word_events.append(chunk)
            elif ctype == "SentenceBoundary":
                sentence_events.append(chunk)

    duration = get_audio_duration(mp3_path)

    cues: list[dict[str, Any]] = []
    if word_events:
        cues = _group_word_cues(word_events)
    elif sentence_events:
        for ev in sentence_events:
            txt = str(ev.get("text") or "").strip()
            if not txt:
                continue
            s_start = ev["offset"] / 10_000_000.0
            s_end = (ev["offset"] + ev["duration"]) / 10_000_000.0
            # Split sentences cleanly into short 1-2 line captions
            sub_words = txt.split()
            if len(sub_words) > 5 or len(txt) > 34:
                sub_cues = _fallback_cues_from_text(txt, max(s_end - s_start, 0.5), max_words=5)
                for sc in sub_cues:
                    cues.append(
                        {
                            "start": s_start + sc["start"],
                            "end": s_start + sc["end"],
                            "text": sc["text"],
                        }
                    )
            else:
                cues.append({"start": s_start, "end": max(s_end, s_start + 0.35), "text": txt})

    if not cues:
        cues = _fallback_cues_from_text(clean_text, duration)

    srt_file_path = ""
    if generate_srt:
        target_srt = Path(output_srt) if output_srt else mp3_path.with_suffix(".srt")
        srt_file_path = write_srt_file(cues, target_srt)

    return {
        "audio_path": str(mp3_path),
        "srt_path": srt_file_path,
        "duration": duration,
        "cues": cues,
        "voice": voice,
        "preset": preset,
    }


def build_combined_srt(
    scene_results: list[dict[str, Any]],
    scene_start_offsets: list[float],
    output_srt: str | Path,
) -> str:
    """Combine per-scene cues into a single timeline-aligned .srt file."""
    combined_cues: list[dict[str, Any]] = []
    for scene_res, offset in zip(scene_results, scene_start_offsets):
        for cue in scene_res.get("cues") or []:
            combined_cues.append(
                {
                    "start": offset + float(cue["start"]),
                    "end": offset + float(cue["end"]),
                    "text": cue["text"],
                }
            )
    return write_srt_file(combined_cues, output_srt)
