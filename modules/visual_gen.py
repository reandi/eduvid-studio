"""Combined visual generator: Pollinations.ai (2D animation) & Python Pillow (charts & point cards)."""

from __future__ import annotations

import math
import os
import random
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

FONT_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


def get_resolution(rasio: str) -> tuple[int, int]:
    """Return (width, height) based on aspect ratio."""
    return (1920, 1080) if rasio == "16:9" else (1080, 1920)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = FONT_BOLD_CANDIDATES if bold else FONT_REGULAR_CANDIDATES
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    words = (text or "").strip().split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        w, _ = _measure_text(draw, candidate, font)
        if w <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _measure_text(draw, last + "...", font)[0] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = (last + "...") if last else "..."
    return lines


def _draw_vertical_gradient(width: int, height: int, top_rgb: tuple[int, int, int], bottom_rgb: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (1, height))
    pixels = []
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
        pixels.append((r, g, b))
    base.putdata(pixels)
    return base.resize((width, height), Image.Resampling.BILINEAR)


def _format_number(val: float, satuan: str = "") -> str:
    if abs(val - round(val)) < 1e-6:
        num_str = f"{int(round(val)):,}".replace(",", ".")
    else:
        num_str = f"{val:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
    satuan = (satuan or "").strip()
    if not satuan:
        return num_str
    if satuan in ("%", "°", "°C", "x", "X"):
        return f"{num_str}{satuan}"
    return f"{num_str} {satuan}"


def _render_kurzgesagt_fallback(width: int, height: int, seed: int) -> Image.Image:
    """Clean geometric vector-style fallback illustration when Pollinations.ai is unreachable."""
    rng = random.Random(seed)
    img = _draw_vertical_gradient(width, height, (12, 26, 48), (20, 48, 82))
    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle orbital rings
    cx, cy = int(width * 0.5), int(height * 0.44)
    base_r = int(min(width, height) * 0.34)
    for mult, alpha in [(1.35, 22), (1.05, 34), (0.78, 45)]:
        r = int(base_r * mult)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(45, 212, 191, alpha), width=4)

    # Stars / geometric dots
    for _ in range(36):
        sx = rng.randint(40, width - 40)
        sy = rng.randint(40, int(height * 0.85))
        sr = rng.randint(3, 8)
        col = rng.choice([(255, 255, 255, 160), (45, 212, 191, 170), (251, 146, 60, 170)])
        draw.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=col)

    # Central planet / stylized vector sphere
    pr = int(base_r * 0.56)
    draw.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=(20, 184, 166, 255))
    draw.pieslice((cx - pr, cy - pr, cx + pr, cy + pr), start=220, end=40, fill=(45, 212, 191, 255))
    # Floating accent moons
    m1_r = int(pr * 0.24)
    m1_x, m1_y = cx + int(base_r * 0.85), cy - int(base_r * 0.42)
    draw.ellipse((m1_x - m1_r, m1_y - m1_r, m1_x + m1_r, m1_y + m1_r), fill=(249, 115, 22, 255))

    m2_r = int(pr * 0.16)
    m2_x, m2_y = cx - int(base_r * 0.78), cy + int(base_r * 0.35)
    draw.ellipse((m2_x - m2_r, m2_y - m2_r, m2_x + m2_r, m2_y + m2_r), fill=(250, 204, 21, 255))

    # Stylized horizon hills at bottom
    hill_y = int(height * 0.78)
    draw.ellipse((-int(width * 0.2), hill_y, int(width * 0.85), height + int(height * 0.25)), fill=(15, 36, 66, 240))
    draw.ellipse((int(width * 0.25), hill_y + 30, int(width * 1.2), height + int(height * 0.3)), fill=(9, 23, 44, 255))
    return img


def generate_animasi_2d(
    prompt: str,
    rasio: str,
    output_path: str | Path,
    seed: int | None = None,
) -> dict[str, Any]:
    """Download a Kurzgesagt-style 2D flat vector illustration from Pollinations.ai."""
    width, height = get_resolution(rasio)
    used_seed = seed if seed is not None else random.randint(1000, 999999)

    clean_prompt = (prompt or "").strip()
    style_suffix = "flat vector illustration, Kurzgesagt style, clean geometric shapes, vibrant colors, no text, no letters, no watermark"
    if "kurzgesagt" not in clean_prompt.lower():
        full_prompt = f"{clean_prompt}, {style_suffix}" if clean_prompt else style_suffix
    elif "no text" not in clean_prompt.lower():
        full_prompt = f"{clean_prompt}, no text, no letters, no watermark"
    else:
        full_prompt = clean_prompt

    encoded = quote(full_prompt, safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true&seed={used_seed}"
    )

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    last_error = ""
    headers = {"User-Agent": "EduVidStudio/1.0 (+https://huggingface.co/spaces)"}
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers, timeout=55)
            if resp.status_code == 200 and resp.content:
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                img = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS)
                img.save(out_path, format="PNG", optimize=True)
                return {
                    "path": str(out_path),
                    "source": "pollinations",
                    "seed": used_seed,
                    "warning": "",
                }
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_error = str(exc)
        # Try a new seed on retry
        used_seed = random.randint(1000, 999999)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width={width}&height={height}&nologo=true&seed={used_seed}"
        )

    # Graceful offline / rate-limit fallback so rendering pipeline never stalls
    fallback_img = _render_kurzgesagt_fallback(width, height, used_seed)
    fallback_img.save(out_path, format="PNG", optimize=True)
    return {
        "path": str(out_path),
        "source": "fallback",
        "seed": used_seed,
        "warning": f"Pollinations.ai sedang tidak merespons ({last_error}). Menggunakan ilustrasi vektor cadangan — klik 'Ganti Gambar' untuk mencoba ulang.",
    }


def generate_infografis_data(
    data_grafik: dict[str, Any],
    rasio: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Render a crisp dark-navy data infographic bar chart using Python Pillow."""
    width, height = get_resolution(rasio)
    is_portrait = height > width

    img = _draw_vertical_gradient(width, height, (9, 21, 38), (16, 38, 66))
    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle decorative geometric rings in background
    draw.ellipse(
        (width - int(width * 0.42), -int(width * 0.22), width + int(width * 0.24), int(width * 0.44)),
        outline=(20, 184, 166, 26),
        width=6,
    )
    draw.ellipse(
        (-int(width * 0.28), height - int(width * 0.42), int(width * 0.36), height + int(width * 0.22)),
        outline=(249, 115, 22, 22),
        width=6,
    )

    judul = str((data_grafik or {}).get("judul_grafik") or "Ringkasan Data Utama").strip()
    satuan = str((data_grafik or {}).get("satuan") or "").strip()
    raw_items = (data_grafik or {}).get("items") or []
    items: list[tuple[str, float]] = []
    for item in raw_items[:5]:
        if isinstance(item, dict):
            lbl = str(item.get("label") or "").strip()
            try:
                val = max(float(item.get("nilai", 0)), 0.0)
            except (TypeError, ValueError):
                val = 0.0
            if lbl:
                items.append((lbl, val))
    if not items:
        items = [("Data A", 45.0), ("Data B", 78.0), ("Data C", 92.0)]

    max_val = max((v for _, v in items), default=1.0)
    if max_val <= 0:
        max_val = 1.0

    # Palette: alternating modern teal and warm orange
    bar_palette = [
        (20, 184, 166, 255),   # Teal 500
        (249, 115, 22, 255),   # Orange 500
        (45, 212, 191, 255),   # Teal 400
        (251, 146, 60, 255),   # Orange 400
        (56, 189, 248, 255),   # Sky 400
    ]

    pad_x = int(width * (0.075 if is_portrait else 0.065))
    top_y = int(height * (0.085 if is_portrait else 0.08))
    bottom_safe = int(height * (0.17 if is_portrait else 0.14))

    # Top badge pill
    badge_font = _load_font(26 if is_portrait else 24, bold=True)
    badge_text = "DATA & INFOGRAFIS"
    bw, bh = _measure_text(draw, badge_text, badge_font)
    pill_pad_x, pill_pad_y = 24, 12
    draw.rounded_rectangle(
        (pad_x, top_y, pad_x + bw + pill_pad_x * 2, top_y + bh + pill_pad_y * 2),
        radius=999,
        fill=(20, 184, 166, 38),
        outline=(45, 212, 191, 120),
        width=2,
    )
    draw.text((pad_x + pill_pad_x, top_y + pill_pad_y - 2), badge_text, font=badge_font, fill=(94, 234, 212, 255))

    # Chart title
    title_y = top_y + bh + pill_pad_y * 2 + (36 if is_portrait else 24)
    title_font = _load_font(60 if is_portrait else 56, bold=True)
    title_lines = _wrap_text(draw, judul, title_font, width - pad_x * 2, max_lines=3 if is_portrait else 2)
    cur_y = title_y
    for line in title_lines:
        draw.text((pad_x, cur_y), line, font=title_font, fill=(248, 250, 252, 255))
        _, lh = _measure_text(draw, line, title_font)
        cur_y += lh + 16

    # Short teal-orange accent divider
    cur_y += 14
    draw.rounded_rectangle((pad_x, cur_y, pad_x + 110, cur_y + 8), radius=4, fill=(20, 184, 166, 255))
    draw.rounded_rectangle((pad_x + 122, cur_y, pad_x + 170, cur_y + 8), radius=4, fill=(249, 115, 22, 255))

    # Chart card container
    card_top = cur_y + (48 if is_portrait else 34)
    card_bottom = height - bottom_safe
    card_rect = (pad_x, card_top, width - pad_x, card_bottom)
    draw.rounded_rectangle(
        card_rect,
        radius=34,
        fill=(15, 34, 59, 225),
        outline=(42, 74, 112, 175),
        width=2,
    )

    inner_pad_x = 56 if is_portrait else 70
    inner_pad_y = 60 if is_portrait else 52
    plot_left = card_rect[0] + inner_pad_x
    plot_right = card_rect[2] - inner_pad_x
    plot_top = card_rect[1] + inner_pad_y
    plot_bottom = card_rect[3] - inner_pad_y
    plot_w = plot_right - plot_left
    plot_h = plot_bottom - plot_top

    if is_portrait:
        # Clean horizontal bar layout — super readable on 9:16 mobile screens
        count = len(items)
        row_step = plot_h / max(count, 1)
        label_font = _load_font(38, bold=True)
        val_font = _load_font(40, bold=True)
        bar_h = min(68, max(40, int(row_step * 0.34)))

        # Subtle vertical guide lines (25%, 50%, 75%, 100%)
        for frac in (0.25, 0.5, 0.75, 1.0):
            gx = int(plot_left + plot_w * frac)
            draw.line((gx, plot_top, gx, plot_bottom), fill=(51, 85, 128, 70), width=2)

        for idx, (label, val) in enumerate(items):
            row_center = plot_top + row_step * idx + row_step * 0.45
            val_str = _format_number(val, satuan)
            vw, vh = _measure_text(draw, val_str, val_font)

            label_lines = _wrap_text(draw, label, label_font, plot_w - vw - 30, max_lines=1)
            label_txt = label_lines[0] if label_lines else label
            _, lh = _measure_text(draw, label_txt, label_font)

            text_y = int(row_center - bar_h / 2 - lh - 18)
            bar_y = int(row_center - bar_h / 2 + 8)

            # Row label (left) and formatted value (right)
            draw.text((plot_left, text_y), label_txt, font=label_font, fill=(226, 232, 240, 255))
            color = bar_palette[idx % len(bar_palette)]
            draw.text((plot_right - vw, text_y - 2), val_str, font=val_font, fill=color)

            # Bar track
            draw.rounded_rectangle(
                (plot_left, bar_y, plot_right, bar_y + bar_h),
                radius=bar_h // 2,
                fill=(23, 49, 82, 255),
            )
            # Active bar
            ratio = max(0.06, min(1.0, val / max_val))
            fill_w = max(bar_h, int(plot_w * ratio))
            draw.rounded_rectangle(
                (plot_left, bar_y, plot_left + fill_w, bar_y + bar_h),
                radius=bar_h // 2,
                fill=color,
            )
    else:
        # Modern vertical bar chart for 16:9 widescreen
        count = len(items)
        label_area_h = 104
        value_area_h = 64
        chart_top = plot_top + value_area_h
        chart_bottom = plot_bottom - label_area_h
        chart_h = max(chart_bottom - chart_top, 120)

        # Horizontal gridlines
        for i in range(5):
            gy = int(chart_bottom - (chart_h * i / 4))
            draw.line((plot_left, gy, plot_right, gy), fill=(51, 85, 128, 95 if i == 0 else 55), width=3 if i == 0 else 2)

        slot_w = plot_w / max(count, 1)
        bar_w = min(190, int(slot_w * 0.56))
        val_font = _load_font(38, bold=True)
        lbl_font = _load_font(30, bold=True)

        for idx, (label, val) in enumerate(items):
            cx = int(plot_left + slot_w * idx + slot_w / 2)
            x0 = cx - bar_w // 2
            x1 = cx + bar_w // 2
            ratio = max(0.08, min(1.0, val / max_val))
            bh_px = int(chart_h * ratio)
            y0 = chart_bottom - bh_px
            color = bar_palette[idx % len(bar_palette)]

            # Subtle bar track column
            draw.rounded_rectangle((x0, chart_top, x1, chart_bottom), radius=20, fill=(22, 46, 78, 130))
            # Colored bar
            draw.rounded_rectangle((x0, y0, x1, chart_bottom), radius=20, fill=color)

            # Value above bar
            val_str = _format_number(val, satuan)
            vw, vh = _measure_text(draw, val_str, val_font)
            draw.text((cx - vw // 2, y0 - vh - 16), val_str, font=val_font, fill=(248, 250, 252, 255))

            # Wrapped category label below axis
            lbl_lines = _wrap_text(draw, label, lbl_font, int(slot_w * 0.88), max_lines=2)
            ly = chart_bottom + 20
            for line in lbl_lines:
                lw, lh = _measure_text(draw, line, lbl_font)
                draw.text((cx - lw // 2, ly), line, font=lbl_font, fill=(203, 213, 225, 255))
                ly += lh + 8

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG", optimize=True)
    return {"path": str(out_path), "source": "pillow_infografis", "warning": ""}


def generate_kartu_poin(
    poin_teks: dict[str, Any],
    rasio: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Render a clean minimalist point card on soft cream background using Python Pillow."""
    width, height = get_resolution(rasio)
    is_portrait = height > width

    # Soft warm cream background
    img = _draw_vertical_gradient(width, height, (250, 247, 240), (242, 237, 226))
    draw = ImageDraw.Draw(img, "RGBA")

    # Subtle decorative geometric blobs
    draw.ellipse(
        (-int(width * 0.16), -int(width * 0.12), int(width * 0.38), int(width * 0.42)),
        fill=(20, 184, 166, 22),
    )
    draw.ellipse(
        (width - int(width * 0.34), height - int(width * 0.36), width + int(width * 0.16), height + int(width * 0.14)),
        fill=(249, 115, 22, 20),
    )

    judul = str((poin_teks or {}).get("judul") or "Poin Penting").strip()
    raw_poin = (poin_teks or {}).get("poin") or []
    poin_list = [str(p).strip() for p in raw_poin if str(p).strip()][:3]
    if not poin_list:
        poin_list = [
            "Pahami inti masalah dengan data yang tepat",
            "Terapkan langkah sederhana secara konsisten",
        ]

    pad_x = int(width * (0.075 if is_portrait else 0.085))
    top_margin = int(height * (0.10 if is_portrait else 0.09))
    bottom_safe = int(height * (0.17 if is_portrait else 0.14))

    card_rect = (pad_x, top_margin, width - pad_x, height - bottom_safe)

    # Soft blurred drop shadow under the white card
    shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_offset = 18
    shadow_draw.rounded_rectangle(
        (
            card_rect[0] + 4,
            card_rect[1] + shadow_offset,
            card_rect[2] + 4,
            card_rect[3] + shadow_offset,
        ),
        radius=42,
        fill=(15, 23, 42, 28),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=24))
    img = Image.alpha_composite(img.convert("RGBA"), shadow_layer)
    draw = ImageDraw.Draw(img, "RGBA")

    # Crisp elevated white card
    draw.rounded_rectangle(
        card_rect,
        radius=42,
        fill=(255, 255, 255, 255),
        outline=(226, 220, 208, 255),
        width=3,
    )

    inner_x = card_rect[0] + (64 if is_portrait else 84)
    inner_right = card_rect[2] - (64 if is_portrait else 84)
    inner_w = inner_right - inner_x
    cur_y = card_rect[1] + (68 if is_portrait else 56)

    # Top accent label pill
    tag_font = _load_font(24 if is_portrait else 22, bold=True)
    tag_txt = "POIN UTAMA"
    tw, th = _measure_text(draw, tag_txt, tag_font)
    draw.rounded_rectangle(
        (inner_x, cur_y, inner_x + tw + 40, cur_y + th + 20),
        radius=999,
        fill=(240, 253, 250, 255),
        outline=(153, 246, 228, 255),
        width=2,
    )
    draw.text((inner_x + 20, cur_y + 8), tag_txt, font=tag_font, fill=(13, 148, 136, 255))
    cur_y += th + 54

    # Card title
    title_font = _load_font(58 if is_portrait else 54, bold=True)
    title_lines = _wrap_text(draw, judul, title_font, inner_w, max_lines=3 if is_portrait else 2)
    for line in title_lines:
        draw.text((inner_x, cur_y), line, font=title_font, fill=(15, 23, 42, 255))
        _, lh = _measure_text(draw, line, title_font)
        cur_y += lh + 14

    # Clean divider line
    cur_y += 22
    draw.line((inner_x, cur_y, inner_right, cur_y), fill=(232, 228, 220, 255), width=3)
    cur_y += 42 if is_portrait else 34

    # Numbered point items
    badge_colors = [
        (13, 148, 136, 255),  # Teal 600
        (234, 88, 12, 255),   # Orange 600
        (15, 39, 71, 255),    # Deep Navy
    ]

    avail_h = (card_rect[3] - (64 if is_portrait else 52)) - cur_y
    slot_h = avail_h / max(len(poin_list), 1)

    point_font = _load_font(42 if is_portrait else 38, bold=False)
    num_font = _load_font(40 if is_portrait else 36, bold=True)
    badge_d = 84 if is_portrait else 76
    gap_after_badge = 34
    text_max_w = inner_w - badge_d - gap_after_badge - 48

    for idx, point in enumerate(poin_list):
        row_top = int(cur_y + idx * slot_h)
        row_bottom = int(cur_y + (idx + 1) * slot_h - (24 if idx < len(poin_list) - 1 else 0))
        row_mid = (row_top + row_bottom) // 2

        # Soft tinted item panel
        draw.rounded_rectangle(
            (inner_x, row_top, inner_right, row_bottom),
            radius=28,
            fill=(249, 248, 245, 255),
            outline=(236, 232, 224, 255),
            width=2,
        )

        # Circular numbered badge
        bx0 = inner_x + 28
        by0 = row_mid - badge_d // 2
        b_color = badge_colors[idx % len(badge_colors)]
        draw.ellipse((bx0, by0, bx0 + badge_d, by0 + badge_d), fill=b_color)
        num_str = str(idx + 1)
        nw, nh = _measure_text(draw, num_str, num_font)
        draw.text(
            (bx0 + (badge_d - nw) // 2, by0 + (badge_d - nh) // 2 - 4),
            num_str,
            font=num_font,
            fill=(255, 255, 255, 255),
        )

        # Wrapped point text vertically centered in row
        lines = _wrap_text(draw, point, point_font, text_max_w, max_lines=3 if is_portrait else 2)
        line_heights = [_measure_text(draw, ln, point_font)[1] for ln in lines]
        line_gap = 12
        total_txt_h = sum(line_heights) + line_gap * max(len(lines) - 1, 0)
        ty = row_mid - total_txt_h // 2 - 2
        tx = bx0 + badge_d + gap_after_badge
        for ln, lh in zip(lines, line_heights):
            draw.text((tx, ty), ln, font=point_font, fill=(30, 41, 59, 255))
            ty += lh + line_gap

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, format="PNG", optimize=True)
    return {"path": str(out_path), "source": "pillow_kartu_poin", "warning": ""}


def generate_scene_visual(
    scene: dict[str, Any],
    rasio: str,
    output_path: str | Path,
    seed: int | None = None,
) -> dict[str, Any]:
    """Dispatch visual generation for a single scene based on `tipe_visual`."""
    tipe = scene.get("tipe_visual", "animasi_2d")
    if tipe == "infografis_data":
        return generate_infografis_data(scene.get("data_grafik") or {}, rasio, output_path)
    if tipe == "kartu_poin":
        return generate_kartu_poin(scene.get("poin_teks") or {}, rasio, output_path)
    return generate_animasi_2d(scene.get("visual_prompt") or "", rasio, output_path, seed=seed)
