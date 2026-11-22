"""Script and storytelling generator powered by Google Gemini (google-genai)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


ALLOWED_RATIOS = ("9:16", "16:9")
ALLOWED_DURATIONS = (30, 60, 150)
ALLOWED_STRUCTURES = (
    "Masalah-Data-Solusi",
    "Hook-Penjelasan-Kesimpulan",
    "Listicle/Poin",
    "Perbandingan",
)
ALLOWED_STYLES = ("Edukasi", "Entertainment", "Storytelling")
ALLOWED_VISUAL_TYPES = ("animasi_2d", "infografis_data", "kartu_poin")


class GrafikItem(BaseModel):
    label: str = Field(description="Label singkat kategori grafik (maks 3 kata)")
    nilai: float = Field(description="Nilai numerik positif untuk batang grafik")


class DataGrafik(BaseModel):
    judul_grafik: str = Field(default="", description="Judul singkat grafik batang")
    satuan: str = Field(default="", description="Satuan angka, misal '%', 'Juta', '°C', atau ''")
    items: list[GrafikItem] = Field(
        default_factory=list,
        description="3 hingga 5 data batang grafik dengan label dan nilai numerik",
    )


class PoinTeks(BaseModel):
    judul: str = Field(default="", description="Judul kartu poin (maks 6 kata)")
    poin: list[str] = Field(
        default_factory=list,
        description="2 hingga 3 poin singkat yang jelas dan mudah dibaca",
    )


class SceneItem(BaseModel):
    narasi: str = Field(description="Naskah narasi Bahasa Indonesia yang natural untuk adegan ini")
    tipe_visual: Literal["animasi_2d", "infografis_data", "kartu_poin"] = Field(
        description="Jenis visual terbaik untuk menyampaikan pesan adegan ini"
    )
    visual_prompt: str = Field(
        default="",
        description="Prompt Bahasa Inggris gaya flat vector Kurzgesagt tanpa teks/huruf jika animasi_2d",
    )
    data_grafik: DataGrafik | None = Field(
        default=None,
        description="Data grafik batang jika tipe_visual adalah infografis_data",
    )
    poin_teks: PoinTeks | None = Field(
        default=None,
        description="Judul dan 2-3 poin singkat jika tipe_visual adalah kartu_poin",
    )


class ScriptOutput(BaseModel):
    judul: str = Field(description="Judul video edukasi yang menarik dalam Bahasa Indonesia")
    deskripsi: str = Field(description="Deskripsi video singkat dan informatif dalam Bahasa Indonesia")
    hashtags: list[str] = Field(description="5 hingga 8 hashtag relevan diawali simbol #")
    scenes: list[SceneItem] = Field(description="Daftar adegan berurutan dari pembuka sampai penutup")


def _duration_guidance(durasi: int) -> tuple[str, str]:
    if durasi <= 35:
        return "4 hingga 5 adegan", "65 hingga 85 kata total (sekitar 14-18 kata per adegan)"
    if durasi <= 90:
        return "6 hingga 8 adegan", "130 hingga 165 kata total (sekitar 18-24 kata per adegan)"
    return "10 hingga 13 adegan", "320 hingga 380 kata total (sekitar 26-34 kata per adegan)"


def _style_guidance(gaya_bahasa: str) -> str:
    mapping = {
        "Edukasi": "Jelas, terstruktur, kredibel, mudah dipahami semua kalangan, informatif namun tidak kaku.",
        "Entertainment": "Enerjik, lincah, penuh rasa penasaran, memakai hook kuat dan kalimat pendek yang seru.",
        "Storytelling": "Mengalir, hangat, imajinatif, membangun alur cerita dengan transisi emosional yang halus.",
    }
    return mapping.get(gaya_bahasa, mapping["Edukasi"])


def _normalize_scene(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    tipe = raw.get("tipe_visual", "animasi_2d")
    if tipe not in ALLOWED_VISUAL_TYPES:
        tipe = "animasi_2d"

    narasi = str(raw.get("narasi") or "").strip()
    if not narasi:
        narasi = f"Penjelasan penting pada bagian ke-{idx + 1}."

    visual_prompt = str(raw.get("visual_prompt") or "").strip()
    if not visual_prompt:
        visual_prompt = (
            "flat vector illustration in Kurzgesagt style, clean geometric shapes, "
            "vibrant teal and warm orange accents on deep navy background, minimalist educational concept, "
            "no text, no letters, no numbers, no watermark"
        )

    raw_grafik = raw.get("data_grafik") or {}
    items_clean: list[dict[str, Any]] = []
    if isinstance(raw_grafik, dict) and isinstance(raw_grafik.get("items"), list):
        for item in raw_grafik["items"][:5]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            try:
                nilai = float(item.get("nilai", 0))
            except (TypeError, ValueError):
                nilai = 0.0
            if label:
                items_clean.append({"label": label, "nilai": max(nilai, 0.0)})

    if not items_clean:
        items_clean = [
            {"label": "Kategori A", "nilai": 45.0},
            {"label": "Kategori B", "nilai": 72.0},
            {"label": "Kategori C", "nilai": 90.0},
        ]

    data_grafik = {
        "judul_grafik": str(raw_grafik.get("judul_grafik") or "Perbandingan Data Utama").strip()
        if isinstance(raw_grafik, dict)
        else "Perbandingan Data Utama",
        "satuan": str(raw_grafik.get("satuan") or "%").strip()
        if isinstance(raw_grafik, dict)
        else "%",
        "items": items_clean,
    }

    raw_poin = raw.get("poin_teks") or {}
    poin_list: list[str] = []
    if isinstance(raw_poin, dict) and isinstance(raw_poin.get("poin"), list):
        poin_list = [str(p).strip() for p in raw_poin["poin"] if str(p).strip()][:3]

    if not poin_list:
        poin_list = [
            "Pahami konsep inti secara ringkas",
            "Terapkan langkah praktis secara konsisten",
        ]

    poin_teks = {
        "judul": str(raw_poin.get("judul") or "Poin Utama").strip()
        if isinstance(raw_poin, dict)
        else "Poin Utama",
        "poin": poin_list,
    }

    return {
        "narasi": narasi,
        "tipe_visual": tipe,
        "visual_prompt": visual_prompt,
        "data_grafik": data_grafik,
        "poin_teks": poin_teks,
    }


def _extract_json_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("Respons Gemini tidak berisi JSON yang valid.")
        return json.loads(match.group(0))


def generate_script(
    *,
    tema: str = "",
    naskah_manual: str = "",
    rasio: str = "9:16",
    durasi: int = 60,
    struktur: str = "Masalah-Data-Solusi",
    gaya_bahasa: str = "Edukasi",
) -> dict[str, Any]:
    """Generate a structured infographic video script using Gemini 2.5 Flash."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        raise RuntimeError(
            "GEMINI_API_KEY belum dikonfigurasi. Tambahkan API key Gemini di file .env atau Secrets."
        )

    tema = (tema or "").strip()
    naskah_manual = (naskah_manual or "").strip()
    if not tema and not naskah_manual:
        raise ValueError("Mohon isi tema video atau naskah manual terlebih dahulu.")

    if rasio not in ALLOWED_RATIOS:
        rasio = "9:16"
    if durasi not in ALLOWED_DURATIONS:
        durasi = 60
    if struktur not in ALLOWED_STRUCTURES:
        struktur = "Masalah-Data-Solusi"
    if gaya_bahasa not in ALLOWED_STYLES:
        gaya_bahasa = "Edukasi"

    scene_range, word_range = _duration_guidance(durasi)
    style_desc = _style_guidance(gaya_bahasa)
    orientasi = "vertikal 9:16 (TikTok/Reels/Shorts)" if rasio == "9:16" else "horizontal 16:9 (YouTube)"

    if naskah_manual:
        source_instruction = (
            "Pengguna memberikan NASKAH MANUAL berikut. Pecah dan adaptasikan naskah ini menjadi adegan-adegan "
            "video edukasi infografis yang tetap setia pada makna aslinya, namun dipoles agar enak didengar "
            "sebagai voice-over dan terbagi rapi ke tipe visual yang bervariasi:\n\n"
            f"\"\"\"\n{naskah_manual}\n\"\"\""
        )
    else:
        source_instruction = f"Topik utama video edukasi: \"{tema}\""

    prompt = f"""Kamu adalah sutradara konten & penulis naskah video edukasi infografis profesional berbahasa Indonesia (setara kualitas Kurzgesagt & Kok Bisa).

{source_instruction}

Spesifikasi Produksi:
- Format layar: {orientasi}
- Target durasi: {durasi} detik ({scene_range}, sekitar {word_range})
- Struktur penceritaan: {struktur}
- Gaya bahasa: {gaya_bahasa} — {style_desc}

Aturan Penting Tiap Adegan (`scenes`):
1. `narasi`: Bahasa Indonesia yang natural untuk dibacakan oleh voice-over (tanpa tanda kurung arahan panggung, tanpa emoji, tanpa singkatan aneh).
2. `tipe_visual`: Variasikan secara cerdas dan otomatis di antara:
   - `"animasi_2d"`: untuk hook, ilustrasi cerita, analogi, atau konsep visual.
   - `"infografis_data"`: minimal 1 adegan berisi angka/perbandingan data nyata atau estimasi edukatif yang masuk akal.
   - `"kartu_poin"`: minimal 1 adegan berisi ringkasan 2–3 poin kunci atau langkah praktis.
3. Jika `tipe_visual == "animasi_2d"`:
   - `visual_prompt` WAJIB dalam Bahasa Inggris, bergaya *flat vector illustration in Kurzgesagt style*, kaya warna harmonis, komposisi rapi untuk rasio {rasio}, dan secara eksplisit TANPA teks/huruf/angka/watermark (`no text, no letters, no watermark`).
4. Jika `tipe_visual == "infografis_data"`:
   - Isi `data_grafik` dengan `judul_grafik` singkat, `satuan` (contoh: "%", "Juta", "x", atau ""), dan 3–4 `items` berisi `label` singkat serta `nilai` numerik positif.
5. Jika `tipe_visual == "kartu_poin"`:
   - Isi `poin_teks` dengan `judul` singkat dan 2–3 `poin` padat (masing-masing maks 12 kata).
6. Untuk `hashtags`, berikan 5–7 hashtag Bahasa Indonesia/global relevan yang diawali `#`.

Kembalikan HANYA JSON sesuai skema yang ditentukan."""

    client = genai.Client(api_key=api_key)
    preferred_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    candidate_models = [preferred_model]
    for fb in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"):
        if fb not in candidate_models:
            candidate_models.append(fb)

    response = None
    last_exc: Exception | None = None
    for model_name in candidate_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ScriptOutput,
                    temperature=0.7,
                ),
            )
            break
        except Exception as exc:
            last_exc = exc
            err_msg = str(exc)
            if any(k in err_msg for k in ("404", "NOT_FOUND", "503", "UNAVAILABLE")) or "no longer available" in err_msg.lower():
                continue
            raise

    if response is None:
        raise RuntimeError(f"Tidak ada model Gemini Flash yang tersedia: {last_exc}")

    raw_text = response.text or ""
    if not raw_text.strip():
        raise RuntimeError("Gemini mengembalikan respons kosong. Silakan coba lagi.")

    parsed = _extract_json_payload(raw_text)
    raw_scenes = parsed.get("scenes") or []
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise RuntimeError("Naskah tidak memiliki daftar adegan. Silakan ulangi generate.")

    hashtags_raw = parsed.get("hashtags") or []
    hashtags: list[str] = []
    if isinstance(hashtags_raw, list):
        for tag in hashtags_raw:
            t = str(tag).strip()
            if not t:
                continue
            if not t.startswith("#"):
                t = f"#{t.lstrip('#')}"
            hashtags.append(t)
    if not hashtags:
        hashtags = ["#EduVidStudio", "#VideoEdukasi", "#Infografis", "#BelajarCepat"]

    normalized_scenes = [_normalize_scene(s, i) for i, s in enumerate(raw_scenes) if isinstance(s, dict)]

    return {
        "judul": str(parsed.get("judul") or tema or "Video Edukasi Infografis").strip(),
        "deskripsi": str(parsed.get("deskripsi") or "Video edukasi infografis dibuat dengan EduVid Studio.").strip(),
        "hashtags": hashtags,
        "scenes": normalized_scenes,
    }
