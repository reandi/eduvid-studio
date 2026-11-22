"""EduVid Studio - FastAPI Backend Server."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from modules.script_gen import generate_script
from modules.tts_gen import (
    ALLOWED_VOICES,
    STYLE_PRESETS,
    build_combined_srt,
    generate_scene_audio,
)
from modules.video_editor import (
    OUTPUT_DIR,
    compute_scene_timeline,
    list_bgm_files,
    render_video,
)
from modules.visual_gen import generate_scene_visual

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eduvid_studio")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_expected_pin() -> str:
    pin = os.getenv("APP_PIN", "").strip()
    if not pin:
        return "123456"
    return pin


if not os.getenv("APP_PIN", "").strip():
    logger.warning("APP_PIN belum diatur di environment. Menggunakan PIN default sementara: 123456")


def verify_pin(x_app_pin: str | None = Header(default=None, alias="X-App-Pin")) -> None:
    """Validate X-App-Pin header on every API request."""
    expected = get_expected_pin()
    provided = (x_app_pin or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="PIN tidak valid atau header X-App-Pin tidak ditemukan.",
        )


def _safe_id(value: str | None) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (value or "").strip())
    return cleaned[:48] or uuid.uuid4().hex[:12]


def _safe_filename(name: str) -> str:
    cleaned = Path(name).name
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", cleaned):
        raise HTTPException(status_code=400, detail="Nama file tidak valid.")
    return cleaned


class ScriptRequest(BaseModel):
    tema: str = ""
    naskah_manual: str = ""
    rasio: str = "9:16"
    durasi: int = 60
    struktur: str = "Masalah-Data-Solusi"
    gaya_bahasa: str = "Edukasi"


class ScenePayload(BaseModel):
    narasi: str = ""
    tipe_visual: str = "animasi_2d"
    visual_prompt: str = ""
    data_grafik: dict[str, Any] | None = None
    poin_teks: dict[str, Any] | None = None


class GenerateVisualsRequest(BaseModel):
    project_id: str | None = None
    rasio: str = "9:16"
    scenes: list[ScenePayload] = Field(default_factory=list)


class RegenerateSceneRequest(BaseModel):
    project_id: str | None = None
    scene_index: int = Field(ge=0)
    rasio: str = "9:16"
    scene: ScenePayload


class RenderVideoRequest(BaseModel):
    project_id: str | None = None
    rasio: str = "9:16"
    gaya_bahasa: str = "Edukasi"
    voice: str = "id-ID-ArdiNeural"
    subtitle: bool = True
    enable_bgm: bool = True
    selected_bgm: str | None = None
    scenes: list[ScenePayload] = Field(default_factory=list)


app = FastAPI(title="EduVid Studio", version="1.0.0")
api = APIRouter(prefix="/api", dependencies=[Depends(verify_pin)])


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Lightweight health check endpoint for Docker / Hugging Face Spaces."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> FileResponse:
    """Serve the mobile-first 3-step EduVid Studio web interface."""
    return FileResponse(STATIC_DIR / "index.html")


@api.get("/verify-pin")
async def api_verify_pin() -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    has_key = bool(api_key and api_key != "your_gemini_api_key_here")
    return {
        "ok": True,
        "has_gemini_key": has_key,
        "bgm_files": list_bgm_files(),
        "voices": ALLOWED_VOICES,
        "presets": STYLE_PRESETS,
    }


@api.get("/bgm")
async def api_list_bgm() -> dict[str, Any]:
    return {"files": list_bgm_files()}


@api.post("/generate-script")
async def api_generate_script(req: ScriptRequest) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            generate_script,
            tema=req.tema,
            naskah_manual=req.naskah_manual,
            rasio=req.rasio,
            durasi=req.durasi,
            struktur=req.struktur,
            gaya_bahasa=req.gaya_bahasa,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Error generating script")
        raise HTTPException(status_code=500, detail=f"Gagal membuat naskah: {exc}") from exc

    project_id = uuid.uuid4().hex[:12]
    return {
        "project_id": project_id,
        **result,
    }


@api.post("/generate-visuals")
async def api_generate_visuals(req: GenerateVisualsRequest) -> dict[str, Any]:
    if not req.scenes:
        raise HTTPException(status_code=400, detail="Daftar adegan tidak boleh kosong.")

    project_id = _safe_id(req.project_id)
    project_dir = OUTPUT_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)

    results: list[dict[str, Any]] = []
    for idx, scene in enumerate(req.scenes):
        filename = f"scene_{idx:02d}.png"
        out_path = project_dir / filename
        try:
            gen_info = await asyncio.to_thread(
                generate_scene_visual,
                scene.model_dump(),
                req.rasio,
                out_path,
            )
            results.append(
                {
                    "scene_index": idx,
                    "image_endpoint": f"/api/media/{project_id}/{filename}?v={ts}",
                    "source": gen_info.get("source", ""),
                    "warning": gen_info.get("warning", ""),
                }
            )
        except Exception as exc:
            logger.exception("Failed generating visual for scene %s", idx)
            raise HTTPException(
                status_code=500,
                detail=f"Gagal menghasilkan visual untuk adegan {idx + 1}: {exc}",
            ) from exc

    return {"project_id": project_id, "visuals": results}


@api.post("/regenerate-scene")
async def api_regenerate_scene(req: RegenerateSceneRequest) -> dict[str, Any]:
    """Regenerate visual image for a single specific scene."""
    project_id = _safe_id(req.project_id)
    project_dir = OUTPUT_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    filename = f"scene_{req.scene_index:02d}.png"
    out_path = project_dir / filename
    ts = int(time.time() * 1000)

    try:
        gen_info = await asyncio.to_thread(
            generate_scene_visual,
            req.scene.model_dump(),
            req.rasio,
            out_path,
        )
    except Exception as exc:
        logger.exception("Failed regenerating scene %s", req.scene_index)
        raise HTTPException(
            status_code=500,
            detail=f"Gagal mengganti gambar adegan {req.scene_index + 1}: {exc}",
        ) from exc

    return {
        "project_id": project_id,
        "scene_index": req.scene_index,
        "image_endpoint": f"/api/media/{project_id}/{filename}?v={ts}",
        "source": gen_info.get("source", ""),
        "warning": gen_info.get("warning", ""),
    }


@api.post("/render-video")
async def api_render_video(req: RenderVideoRequest) -> dict[str, Any]:
    if not req.scenes:
        raise HTTPException(status_code=400, detail="Minimal 1 adegan diperlukan untuk render.")

    project_id = _safe_id(req.project_id)
    project_dir = OUTPUT_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    scene_images: list[Path] = []
    scene_audios: list[Path] = []
    audio_durations: list[float] = []
    tts_results: list[dict[str, Any]] = []

    try:
        # Step A: Ensure every scene visual exists; auto-generate if missing
        for idx, scene in enumerate(req.scenes):
            img_path = project_dir / f"scene_{idx:02d}.png"
            if not img_path.exists():
                await asyncio.to_thread(
                    generate_scene_visual,
                    scene.model_dump(),
                    req.rasio,
                    img_path,
                )
            scene_images.append(img_path)

        # Step B: Synthesize Indonesian voice-over per scene with edge-tts
        for idx, scene in enumerate(req.scenes):
            mp3_path = project_dir / f"scene_{idx:02d}.mp3"
            srt_scene_path = project_dir / f"scene_{idx:02d}.srt"
            tts_info = await generate_scene_audio(
                text=scene.narasi,
                output_mp3=mp3_path,
                voice=req.voice,
                gaya_bahasa=req.gaya_bahasa,
                generate_srt=req.subtitle,
                output_srt=srt_scene_path,
            )
            tts_results.append(tts_info)
            scene_audios.append(Path(tts_info["audio_path"]))
            audio_durations.append(float(tts_info["duration"]))

        # Step C: Build timeline-aligned combined SRT if subtitles are enabled
        combined_srt_path: Path | None = None
        if req.subtitle:
            timeline = compute_scene_timeline(audio_durations)
            combined_srt_path = project_dir / "subtitles_full.srt"
            build_combined_srt(tts_results, timeline["start_offsets"], combined_srt_path)

        # Step D: Render final MP4 using FFmpeg
        render_info = await asyncio.to_thread(
            render_video,
            scene_images=scene_images,
            scene_audios=scene_audios,
            audio_durations=audio_durations,
            rasio=req.rasio,
            work_dir=project_dir / "render_work",
            srt_path=combined_srt_path,
            enable_subtitle=req.subtitle,
            enable_bgm=req.enable_bgm,
            selected_bgm=req.selected_bgm,
            final_output_path=OUTPUT_DIR / "video_final.mp4",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Render video failed")
        raise HTTPException(status_code=500, detail=f"Gagal merender video: {exc}") from exc

    ts = int(time.time() * 1000)
    return {
        "ok": True,
        "project_id": project_id,
        "video_endpoint": f"/api/video/final?v={ts}",
        "download_endpoint": f"/api/video/final?download=1&v={ts}",
        **render_info,
    }


@api.get("/media/{project_id}/{filename}")
async def api_get_media(project_id: str, filename: str) -> FileResponse:
    safe_pid = _safe_id(project_id)
    safe_name = _safe_filename(filename)
    target = (OUTPUT_DIR / safe_pid / safe_name).resolve()
    if not str(target).startswith(str(OUTPUT_DIR.resolve())) or not target.exists():
        raise HTTPException(status_code=404, detail="File media tidak ditemukan.")

    media_type = "image/png" if safe_name.endswith(".png") else "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@api.get("/video/final")
async def api_get_final_video(download: int = Query(default=0)) -> FileResponse:
    target = OUTPUT_DIR / "video_final.mp4"
    if not target.exists():
        raise HTTPException(status_code=404, detail="Video final belum tersedia.")

    headers = {}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="EduVid_Studio_Final.mp4"'
    return FileResponse(target, media_type="video/mp4", headers=headers)


app.include_router(api)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
