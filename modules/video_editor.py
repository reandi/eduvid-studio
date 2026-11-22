"""FFmpeg video editor optimized for low-memory environments (512 MB RAM / Render Free Tier).

Features:
- Low-memory FFmpeg encoding (-preset ultrafast, -threads 1, -crf 28, single filter thread)
- Rolling pairwise xfade crossfades (never opens more than 2 video decoders simultaneously)
- Smooth Ken Burns zoom-in/zoom-out per scene
- Clean burned-in bottom subtitles (.srt)
- Optional local BGM mixing at 15% volume with end fade-out
- Automatic cleanup of all temporary scene images, audio, clips, and SRT files after render
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


BGM_DIR = Path(__file__).resolve().parent.parent / "assets" / "bgm"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# Low-memory FFmpeg flags safe for 512 MB RAM containers (e.g. Render Free Tier)
LOW_MEM_GLOBAL_FLAGS = [
    "-threads",
    "1",
    "-filter_threads",
    "1",
    "-filter_complex_threads",
    "1",
]

LOW_MEM_VIDEO_ENCODE_FLAGS = [
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-crf",
    "28",
    "-threads",
    "1",
    "-pix_fmt",
    "yuv420p",
    "-max_muxing_queue_size",
    "256",
]


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


def _safe_unlink(path: str | Path | None) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if p.is_file() or p.is_symlink():
            p.unlink(missing_ok=True)
    except OSError:
        pass


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


def _build_subtitle_style(rasio: str) -> str:
    if rasio == "9:16":
        # Portrait 1080x1920 (FFmpeg SRT virtual height = 288 -> 1 unit ≈ 6.67px)
        return (
            "FontName=DejaVu Sans,FontSize=8,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00091526,BackColour=&H96091526,"
            "BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginL=16,MarginR=16,MarginV=16"
        )
    # Landscape 1920x1080 (FFmpeg SRT virtual height = 288 -> 1 unit ≈ 3.75px)
    return (
        "FontName=DejaVu Sans,FontSize=12,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00091526,BackColour=&H96091526,"
        "BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginL=24,MarginR=24,MarginV=14"
    )


def _create_ken_burns_clip(
    image_path: str | Path,
    duration_sec: float,
    width: int,
    height: int,
    output_clip: str | Path,
    zoom_in: bool = True,
    fps: int = 24,
) -> None:
    """Render a smooth low-memory Ken Burns (zoom-in or zoom-out) clip from a still image."""
    total_frames = max(int(round(duration_sec * fps)), fps)
    # Moderate 1.15x upscale keeps zoompan smooth while keeping RAM well within 512 MB
    up_w = int(width * 1.15)
    up_h = int(height * 1.15)
    zoom_range = 0.08

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
        *LOW_MEM_GLOBAL_FLAGS,
        "-i",
        str(image_path),
        "-vf",
        vf,
        "-t",
        f"{duration_sec:.3f}",
        *LOW_MEM_VIDEO_ENCODE_FLAGS,
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


def _cleanup_render_artifacts(
    work_dir: Path,
    scene_images: list[str | Path],
    scene_audios: list[str | Path],
    srt_path: str | Path | None,
    extra_cleanup_paths: list[str | Path] | None,
    protected_files: set[Path],
    cleanup_scene_files: bool,
) -> None:
    """Remove temporary working files and (on success) per-scene images/audios/SRTs to keep disk lean."""
    shutil.rmtree(work_dir, ignore_errors=True)

    if not cleanup_scene_files:
        return

    candidates: list[Path] = []
    for p in list(scene_images) + list(scene_audios) + list(extra_cleanup_paths or []):
        candidates.append(Path(p))
    if srt_path:
        candidates.append(Path(srt_path))

    parent_dirs: set[Path] = set()
    output_root = OUTPUT_DIR.resolve()
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in protected_files:
            continue
        # Only delete files located inside OUTPUT_DIR (never touch assets/bgm or source files)
        if str(resolved).startswith(str(output_root)):
            _safe_unlink(resolved)
            # Also remove sibling per-scene .srt if present
            if resolved.suffix.lower() == ".mp3":
                _safe_unlink(resolved.with_suffix(".srt"))
            if resolved.parent != output_root:
                parent_dirs.add(resolved.parent)

    # Remove empty temporary project directories under OUTPUT_DIR
    for d in parent_dirs:
        try:
            if d.exists() and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass


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
    fps: int = 24,
    cleanup_temp: bool = True,
    extra_cleanup_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Render final MP4 video with low-memory FFmpeg settings and automatic temp cleanup."""
    if not scene_images or len(scene_images) != len(scene_audios):
        raise ValueError("Jumlah gambar dan audio adegan tidak cocok.")

    width, height = (1920, 1080) if rasio == "16:9" else (1080, 1920)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_mp4 = Path(final_output_path) if final_output_path else (OUTPUT_DIR / "video_final.mp4")
    target_mp4.parent.mkdir(parents=True, exist_ok=True)
    canonical_mp4 = OUTPUT_DIR / "video_final.mp4"
    protected_files = {target_mp4.resolve(), canonical_mp4.resolve()}

    timeline = compute_scene_timeline(audio_durations)
    slot_durations: list[float] = timeline["slot_durations"]
    start_offsets: list[float] = timeline["start_offsets"]
    clip_durations: list[float] = timeline["clip_durations"]
    xfade_dur: float = timeline["crossfade_dur"]
    total_duration: float = timeline["total_duration"]

    render_succeeded = False
    try:
        # 1. Build synchronized narration track (compressed AAC to minimize disk & RAM)
        narration_m4a = work / "narration_full.m4a"
        audio_cmd = ["ffmpeg", "-y", *LOW_MEM_GLOBAL_FLAGS]
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
                "aac",
                "-b:a",
                "128k",
                str(narration_m4a),
            ]
        )
        _run_ffmpeg(audio_cmd, "Penggabungan Audio Narasi")

        # 2. Mix optional BGM (volume 15% + fade-out at end)
        bgm_path = resolve_bgm_path(selected_bgm) if enable_bgm else None
        final_audio_path = narration_m4a
        bgm_used_name = ""

        if bgm_path and bgm_path.exists():
            mixed_m4a = work / "audio_mixed.m4a"
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
                *LOW_MEM_GLOBAL_FLAGS,
                "-i",
                str(narration_m4a),
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
                "aac",
                "-b:a",
                "128k",
                str(mixed_m4a),
            ]
            _run_ffmpeg(mix_cmd, "Mixing BGM")
            _safe_unlink(narration_m4a)
            final_audio_path = mixed_m4a
            bgm_used_name = bgm_path.name

        # 3. Generate Ken Burns clips & merge pairwise with xfade so FFmpeg never opens >2 video decoders
        has_subtitles = bool(
            enable_subtitle and srt_path and Path(srt_path).exists() and Path(srt_path).stat().st_size > 0
        )
        sub_filter_suffix = ""
        if has_subtitles:
            escaped_srt = _escape_ffmpeg_filter_path(srt_path)
            style = _build_subtitle_style(rasio)
            sub_filter_suffix = f",subtitles='{escaped_srt}':force_style='{style}'"

        n_scenes = len(scene_images)
        first_clip = work / "clip_00.mp4"
        _create_ken_burns_clip(
            image_path=scene_images[0],
            duration_sec=clip_durations[0],
            width=width,
            height=height,
            output_clip=first_clip,
            zoom_in=True,
            fps=fps,
        )

        if n_scenes == 1:
            final_cmd = [
                "ffmpeg",
                "-y",
                *LOW_MEM_GLOBAL_FLAGS,
                "-i",
                str(first_clip),
                "-i",
                str(final_audio_path),
            ]
            if has_subtitles:
                final_cmd.extend(
                    [
                        "-vf",
                        sub_filter_suffix.lstrip(","),
                        *LOW_MEM_VIDEO_ENCODE_FLAGS,
                    ]
                )
            else:
                final_cmd.extend(["-c:v", "copy"])
            final_cmd.extend(
                [
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(target_mp4),
                ]
            )
            _run_ffmpeg(final_cmd, "Render Video Final")
            _safe_unlink(first_clip)
        else:
            current_merged = first_clip
            for idx in range(1, n_scenes):
                next_clip = work / f"clip_{idx:02d}.mp4"
                _create_ken_burns_clip(
                    image_path=scene_images[idx],
                    duration_sec=clip_durations[idx],
                    width=width,
                    height=height,
                    output_clip=next_clip,
                    zoom_in=(idx % 2 == 0),
                    fps=fps,
                )

                is_last = idx == n_scenes - 1
                offset = start_offsets[idx]

                if is_last:
                    # Final pairwise xfade + optional burned-in subtitles + final audio mux in one pass
                    v_filter = (
                        f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}"
                        f"{sub_filter_suffix}[vout]"
                    )
                    final_cmd = [
                        "ffmpeg",
                        "-y",
                        *LOW_MEM_GLOBAL_FLAGS,
                        "-i",
                        str(current_merged),
                        "-i",
                        str(next_clip),
                        "-i",
                        str(final_audio_path),
                        "-filter_complex",
                        v_filter,
                        "-map",
                        "[vout]",
                        "-map",
                        "2:a:0",
                        *LOW_MEM_VIDEO_ENCODE_FLAGS,
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-movflags",
                        "+faststart",
                        "-shortest",
                        str(target_mp4),
                    ]
                    _run_ffmpeg(final_cmd, "Render Video Final")
                    _safe_unlink(current_merged)
                    _safe_unlink(next_clip)
                else:
                    # Intermediate pairwise crossfade merge (video-only, immediately discards old clips)
                    next_merged = work / f"merged_{idx:02d}.mp4"
                    v_filter = f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}[vout]"
                    merge_cmd = [
                        "ffmpeg",
                        "-y",
                        *LOW_MEM_GLOBAL_FLAGS,
                        "-i",
                        str(current_merged),
                        "-i",
                        str(next_clip),
                        "-filter_complex",
                        v_filter,
                        "-map",
                        "[vout]",
                        *LOW_MEM_VIDEO_ENCODE_FLAGS,
                        "-an",
                        str(next_merged),
                    ]
                    _run_ffmpeg(merge_cmd, f"Crossfade Adegan {idx + 1}")
                    _safe_unlink(current_merged)
                    _safe_unlink(next_clip)
                    current_merged = next_merged

        # Always keep canonical output/video_final.mp4 updated
        if target_mp4.resolve() != canonical_mp4.resolve():
            shutil.copy2(target_mp4, canonical_mp4)

        render_succeeded = True
        return {
            "video_path": str(canonical_mp4),
            "duration": round(total_duration, 2),
            "bgm_used": bgm_used_name,
            "subtitles_burned": has_subtitles,
            "resolution": f"{width}x{height}",
        }
    finally:
        _cleanup_render_artifacts(
            work_dir=work,
            scene_images=scene_images,
            scene_audios=scene_audios,
            srt_path=srt_path,
            extra_cleanup_paths=extra_cleanup_paths,
            protected_files=protected_files,
            cleanup_scene_files=bool(cleanup_temp and render_succeeded),
        )
