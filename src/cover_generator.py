#!/usr/bin/env python3
"""
Cover Generator module.
Generates 1400x1400px high-resolution podcast cover art / thumbnail
compliant with Apple Podcasts and Spotify for Podcasters standards.
"""

import os
import re
import textwrap
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

logger = logging.getLogger("podcast.cover_generator")
console = Console()


class CoverGenerator:
    def __init__(self, size: int = 1400):
        self.size = (size, size)

    def _get_fonts(self):
        """Localiza fuentes TrueType en el sistema Ubuntu."""
        bold_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        ]
        reg_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        ]

        bold_path = next((p for p in bold_candidates if os.path.exists(p)), None)
        reg_path = next((p for p in reg_candidates if os.path.exists(p)), None)

        scale = self.size[0] / 1200.0

        if bold_path and reg_path:
            return {
                "header": ImageFont.truetype(bold_path, int(28 * scale)),
                "badge": ImageFont.truetype(bold_path, int(32 * scale)),
                "title": ImageFont.truetype(bold_path, int(52 * scale)),
                "title_small": ImageFont.truetype(bold_path, int(42 * scale)),
                "footer": ImageFont.truetype(reg_path, int(24 * scale)),
            }
        else:
            default = ImageFont.load_default()
            return {
                "header": default,
                "badge": default,
                "title": default,
                "title_small": default,
                "footer": default,
            }

    def generate(
        self,
        title: str,
        output_path: Path,
        edition_label: Optional[str] = None,
        custom_bg: Optional[Path] = None,
    ) -> Path:
        """
        Genera la portada cuadrada masterizada en 1400x1400px.
        """
        w, h = self.size
        scale = w / 1200.0

        # 1. Base / Fondo
        if custom_bg and custom_bg.is_file():
            try:
                base = Image.open(custom_bg).convert("RGBA")
                bw, bh = base.size
                min_dim = min(bw, bh)
                left = (bw - min_dim) // 2
                top = (bh - min_dim) // 2
                base = base.crop((left, top, left + min_dim, top + min_dim))
                base = base.resize((w, h), Image.Resampling.LANCZOS)
            except Exception as e:
                logger.warning("Error abriendo fondo personalizado: %s. Usando procedimental.", e)
                base = None
        else:
            base = None

        if base is None:
            # Gradiente de alta tecnología (Azul noche profundo a cian oscuro Linux)
            base = Image.new("RGBA", (w, h), (10, 14, 26, 255))
            draw_b = ImageDraw.Draw(base)
            for y in range(h):
                ratio = y / h
                r = int(8 + ratio * 14)
                g = int(14 + ratio * 28)
                b = int(28 + ratio * 56)
                draw_b.line([(0, y), (w, y)], fill=(r, g, b, 255))

            # Cuadrícula sutil decorativa estilo terminal / ciberseguridad
            grid_spacing = int(70 * scale)
            for x in range(0, w, grid_spacing):
                draw_b.line([(x, 0), (x, h)], fill=(20, 45, 80, 40), width=1)
            for y in range(0, h, grid_spacing):
                draw_b.line([(0, y), (w, y)], fill=(20, 45, 80, 40), width=1)

        # 2. Capas de viñeta y contraste para legibilidad
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)

        # Gradiente superior (cabecera)
        top_h = int(h * 0.20)
        for y in range(top_h):
            alpha = int((1 - (y / top_h)) * 200)
            draw_ov.line([(0, y), (w, y)], fill=(6, 9, 18, alpha))

        # Gradiente inferior para título y pie
        bottom_start = int(h * 0.40)
        for y in range(bottom_start, h):
            ratio = (y - bottom_start) / (h - bottom_start)
            alpha = int((ratio ** 1.2) * 240)
            draw_ov.line([(0, y), (w, y)], fill=(6, 10, 20, alpha))

        final_img = Image.alpha_composite(base, overlay)
        draw = ImageDraw.Draw(final_img)
        fonts = self._get_fonts()

        margin = int(70 * scale)

        # 3. Cabecera
        header_y = int(60 * scale)
        header_text = "PODCAST LINUX & OPEN SOURCE"
        draw.text((margin, header_y), header_text, font=fonts["header"], fill=(220, 238, 255, 240))
        # Línea cian de acento
        line_y = header_y + int(48 * scale)
        draw.line([(margin, line_y), (w - margin, line_y)], fill=(0, 190, 220, 220), width=int(4 * scale))

        # 4. Badge de Edición
        label = (edition_label or "EDICIÓN SEMANAL").upper()
        badge_y = int(h * 0.58)
        bbox = draw.textbbox((margin, badge_y), label, font=fonts["badge"])
        pad_x, pad_y = int(18 * scale), int(8 * scale)
        pill_box = (bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y)
        draw.rounded_rectangle(pill_box, radius=int(10 * scale), fill=(0, 140, 190, 230))
        draw.text((margin, badge_y), label, font=fonts["badge"], fill=(255, 255, 255, 255))

        # 5. Título del Episodio
        clean_title = re.sub(r"^(?:Podcast\s+)?Linux\s+(?:al\s+Día|Semanal):\s*", "", title, flags=re.IGNORECASE)
        # Ajustar longitud
        lines = textwrap.wrap(clean_title, width=32)
        font_to_use = fonts["title"] if len(lines) <= 3 else fonts["title_small"]
        line_height = int(68 * scale) if len(lines) <= 3 else int(56 * scale)

        title_y = badge_y + int(70 * scale)
        for line in lines[:4]:
            draw.text((margin, title_y), line, font=font_to_use, fill=(255, 255, 255, 255))
            title_y += line_height

        # 6. Footer con las 4 líneas
        footer_y = h - int(75 * scale)
        footer_text = "KERNEL  •  ESCRITORIO  •  APPS LIBRES  •  GAMING"
        draw.text((margin, footer_y), footer_text, font=fonts["footer"], fill=(135, 175, 215, 220))

        # Guardar archivo
        output_path.parent.mkdir(parents=True, exist_ok=True)
        final_img.convert("RGB").save(str(output_path), quality=95, optimize=True)
        console.print(f"🎨 [bold green]Portada guardada exitosamente en:[/bold green] {output_path} ({w}x{h}px)")

        return output_path
