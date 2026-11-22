"""FFmpeg video editor: Ken Burns zoom, crossfades, burned-in subtitles, and optional BGM mixing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


BGM_DIR = Path(__file__).resolve().parent.parent / "assets" / "bgm"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def list_bgm_files(bgm_dir: Path | None = None) -> list[str]:
    """Return sorted list of .mp3 filenames available in assets/bgm/."""
    directory = bgm_dir or BGM_DIR
    if not directory.exists():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".mp3")


def resolve_bgm_path(selected_bgm: str | None = None, bgm_dir: Path | None = None) -> Path | None:
    """Resolve an .mp3 file from assets/bgm/, or return None if directory is empty."""
    directory = bgm_dir or BGM_DIR
    files = list_bgm_files(directory)
    if not files:
        return None
    if selected_bgm and selected_bgm in files:
        return directory / selected_bgm
    return directory / files[0]


def _run_ffmpeg(cmd: list[str], step_label: str) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"FFmpeg gagal pada tahap '{step_label}':\n{tail}")


def _escape_ffmpeg_filter_path(path: str | Path) -> str:
    """Escape file path characters for FFmpeg filter arguments (like subtitles=...)."""
    s = str(Path(path).resolve())
    s = s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return s


def _create_ken_burns_clip(
    image_path: str | Path,
    duration_sec: float,
    width: int,
    height: int,
    output_clip: str | Path,
    zoom_in: bool = True,
    fps: int = 30,
) -> None:
    """Render a smooth Ken Burns (subtle zoom-in or zoom-out) video clip from a still image."""
    total_frames = max(int(round(duration_sec * fps)), fps)
    # Upscale slightly (1.4x) before zoompan to keep subpixel motion smooth & fast on CPU
    up_w = int(width * 1.4)
    up_h = int(height * 1.4)
    zoom_range = 0.09  # Smooth subtle 9% zoom

    if zoom_in:
        z_expr = f"1+{zoom_range}*on/{max(total_frames - 1, 1)}"
    else:
        z_expr = f"{1 + zoom_range}-{zoom_range}*on/{max(total_frames - 1, 1)}"

    vf = (
        f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase,"
        f"crop={up_w}:{up_h},"
        f"zoompan=z='{z_expr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={width}x{height}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(image_path),
        "-vf",
        vf,
        "-t",
        f"{duration_sec:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(output_clip),
    ]
    _run_ffmpeg(cmd, f"Ken Burns clip ({Path(output_clip).name})")


def compute_scene_timeline(
    audio_durations: list[float],
    breath_pad: float = 0.30,
    crossfade_dur: float = 0.50,
) -> dict[str, Any]:
    """Compute exact scene start offsets, padded audio slots, and clip lengths for zero-drift xfade."""
    n = len(audio_durations)
    if n == 0:
        raise ValueError("Minimal 1 adegan diperlukan untuk merender video.")

    # Each scene owns an audio slot = audio_duration + breath_pad (slightly longer tail on last scene)
    slot_durations = [
        max(d + (0.45 if i == n - 1 else breath_pad), 1.2)
        for i, d in enumerate(audio_durations)
    ]

    # Scene i narration starts at cumulative sum of preceding slot durations
    start_offsets: list[float] = []
    acc = 0.0
    for slot in slot_durations:
        start_offsets.append(acc)
        acc += slot
    total_duration = acc

    # For smooth xfade without overlapping voices:
    # Clip i (i < n-1) runs slot_i + crossfade_dur, and transitions into Clip i+1 at offset = start_offsets[i+1]
    effective_xfade = min(crossfade_dur, min(slot_durations) * 0.45) if n > 1 else 0.0
    clip_durations = [
        slot_durations[i] + (effective_xfade if i < n - 1 else 0.0)
        for i in range(n)
    ]

    return {
        "slot_durations": slot_durations,
        "start_offsets": start_offsets,
        "clip_durations": clip_durations,
        "crossfade_dur": effective_xfade,
        "total_duration": total_duration,
    }


def render_video(
    *,
    scene_images: list[str | Path],
    scene_audios: list[str | Path],
    audio_durations: list[float],
    rasio: str,
    work_dir: str | Path,
    srt_path: str | Path | None = None,
    enable_subtitle: bool = True,
    enable_bgm: bool = True,
    selected_bgm: str | None = None,
    final_output_path: str | Path | None = None,
    fps: int = 30,
) -> dict[str, Any]:
    """Render final MP4 video with Ken Burns, crossfades, optional subtitles, and optional BGM."""
    if not scene_images or len(scene_images) != len(scene_audios):
        raise ValueError("Jumlah gambar dan audio adegan tidak cocok.")

    width, height = (1920, 1080) if rasio == "16:9" else (1080, 1920)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    timeline = compute_scene_timeline(audio_durations)
    slot_durations: list[float] = timeline["slot_durations"]
    start_offsets: list[float] = timeline["start_offsets"]
    clip_durations: list[float] = timeline["clip_durations"]
    xfade_dur: float = timeline["crossfade_dur"]
    total_duration: float = timeline["total_duration"]

    # 1. Generate per-scene Ken Burns clips
    clip_paths: list[Path] = []
    for idx, (img_path, clip_dur) in enumerate(zip(scene_images, clip_durations)):
        clip_path = work / f"clip_{idx:02d}.mp4"
        _create_ken_burns_clip(
            image_path=img_path,
            duration_sec=clip_dur,
            width=width,
            height=height,
            output_clip=clip_path,
            zoom_in=(idx % 2 == 0),
            fps=fps,
        )
        clip_paths.append(clip_path)

    # 2. Concatenate scene audio with exact slot padding so narration stays 100% synced
    narration_wav = work / "narration_full.wav"
    audio_cmd = ["ffmpeg", "-y"]
    audio_filters: list[str] = []
    concat_labels: list[str] = []
    for idx, (aud_path, slot_dur) in enumerate(zip(scene_audios, slot_durations)):
        audio_cmd.extend(["-i", str(aud_path)])
        audio_filters.append(
            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"apad,atrim=0:{slot_dur:.3f},asetpts=PTS-STARTPTS[a{idx}]"
        )
        concat_labels.append(f"[a{idx}]")

    audio_filters.append(f"{''.join(concat_labels)}concat=n={len(scene_audios)}:v=0:a=1[nar]")
    audio_cmd.extend(
        [
            "-filter_complex",
            ";".join(audio_filters),
            "-map",
            "[nar]",
            "-c:a",
            "pcm_s16le",
            str(narration_wav),
        ]
    )
    _run_ffmpeg(audio_cmd, "Penggabungan Audio Narasi")

    # 3. Mix optional BGM (volume 15% + fade-out at end)
    bgm_path = resolve_bgm_path(selected_bgm) if enable_bgm else None
    final_audio_path = narration_wav
    bgm_used_name = ""

    if bgm_path and bgm_path.exists():
        mixed_wav = work / "audio_mixed.wav"
        fade_len = min(2.5, total_duration * 0.25)
        fade_start = max(0.0, total_duration - fade_len)
        mix_filter = (
            f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[nar];"
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume=0.15,afade=t=in:st=0:d=0.6,afade=t=out:st={fade_start:.3f}:d={fade_len:.3f}[bgm];"
            f"[nar][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        mix_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(narration_wav),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            mix_filter,
            "-map",
            "[aout]",
            "-t",
            f"{total_duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(mixed_wav),
        ]
        _run_ffmpeg(mix_cmd, "Mixing BGM")
        final_audio_path = mixed_wav
        bgm_used_name = bgm_path.name

    # 4. Assemble final video with crossfades, optional subtitles, and audio
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_mp4 = Path(final_output_path) if final_output_path else (OUTPUT_DIR / "video_final.mp4")
    target_mp4.parent.mkdir(parents=True, exist_ok=True)

    final_cmd = ["ffmpeg", "-y"]
    for cp in clip_paths:
        final_cmd.extend(["-i", str(cp)])
    audio_input_idx = len(clip_paths)
    final_cmd.extend(["-i", str(final_audio_path)])

    filter_parts: list[str] = []
    if len(clip_paths) == 1:
        current_v = "[0:v]"
    else:
        prev_label = "[0:v]"
        for i in range(1, len(clip_paths)):
            out_label = f"[vx{i}]"
            offset = start_offsets[i]
            filter_parts.append(
                f"{prev_label}[{i}:v]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}{out_label}"
            )
            prev_label = out_label
        current_v = prev_label

    # Burn clean bottom subtitles if requested and SRT file exists
    has_subtitles = bool(enable_subtitle and srt_path and Path(srt_path).exists() and Path(srt_path).stat().st_size > 0)
    if has_subtitles:
        escaped_srt = _escape_ffmpeg_filter_path(srt_path)
        if rasio == "9:16":
            # Portrait 1080x1920 (FFmpeg SRT virtual height = 288 -> 1 unit ≈ 6.67px)
            style = (
                "FontName=DejaVu Sans,FontSize=8,Bold=1,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00091526,BackColour=&H96091526,"
                "BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginL=16,MarginR=16,MarginV=16"
            )
        else:
            # Landscape 1920x1080 (FFmpeg SRT virtual height = 288 -> 1 unit ≈ 3.75px)
            style = (
                "FontName=DejaVu Sans,FontSize=12,Bold=1,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00091526,BackColour=&H96091526,"
                "BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginL=24,MarginR=24,MarginV=14"
            )
        filter_parts.append(f"{current_v}subtitles='{escaped_srt}':force_style='{style}'[vsub]")
        current_v = "[vsub]"

    if filter_parts:
        final_cmd.extend(["-filter_complex", ";".join(filter_parts)])
        final_cmd.extend(["-map", current_v, "-map", f"{audio_input_idx}:a"])
    else:
        final_cmd.extend(["-map", "0:v", "-map", f"{audio_input_idx}:a"])

    final_cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(target_mp4),
        ]
    )
    _run_ffmpeg(final_cmd, "Render Video Final")

    # Always also keep canonical output/video_final.mp4 updated
    canonical_mp4 = OUTPUT_DIR / "video_final.mp4"
    if target_mp4.resolve() != canonical_mp4.resolve():
        shutil.copy2(target_mp4, canonical_mp4)

    return {
        "video_path": str(canonical_mp4),
        "duration": round(total_duration, 2),
        "bgm_used": bgm_used_name,
        "subtitles_burned": has_subtitles,
        "resolution": f"{width}x{height}",
    }
