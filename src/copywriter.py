#!/usr/bin/env python3
"""
Copywriter module.
Generates engaging and SEO-optimized title, description, and hashtags
from the podcast script using the local LLM endpoint.
"""

import re
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from datetime import datetime, timezone

from openai import OpenAI
from pydantic import BaseModel
from rich.console import Console

logger = logging.getLogger("podcast.copywriter")
console = Console()


class EpisodeMetadata(BaseModel):
    title: str
    description: str
    hashtags: str
    tag: str
    created_at: datetime = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "hashtags": self.hashtags,
            "tag": self.tag,
            "created_at": self.created_at.isoformat(),
        }


class Copywriter:
    def __init__(
        self,
        base_url: str = "http://192.168.1.200:20128/v1",
        api_key: str = "sk-625c35c6ebef3fea-bhqllm-9dad3943",
        model: str = "hermes-rotator",
        temperature: float = 0.5,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def generate_metadata(self, script_text: str, tag: str = "EPISODIO") -> EpisodeMetadata:
        """
        Genera título, descripción y hashtags para el episodio a partir del guión.
        """
        system_prompt = (
            "Sos un especialista en comunicación técnica, podcasting y posicionamiento SEO de contenidos sobre Linux y Software Libre.\n"
            "Tu tarea es generar el título, descripción y hashtags para la edición semanal de noticias del 'Podcast de Linux al Sur'.\n"
            "La descripción debe resumir con claridad técnica y atractivo los temas principales tratados (Kernel, Desktop/Distro, Aplicaciones Libres y Gaming).\n"
            "Formato de respuesta OBLIGATORIO y EXACTO:\n"
            "TÍTULO: [Título atractivo y concreto, máx 75 caracteres]\n"
            "DESCRIPCIÓN: [Resumen de 120 a 180 palabras estructurado para Spotify/Apple Podcasts]\n"
            "HASHTAGS: [#Linux #OpenSource #Kernel #Ubuntu #GamingOnLinux]"
        )

        user_prompt = (
            f"A partir del siguiente guión del episodio, redactá el título, descripción y hashtags oficiales:\n\n"
            f"{script_text[:4000]}"
        )

        console.print(f"✍️  [cyan]Generando metadata editorial y SEO con LLM ({self.model})...[/cyan]")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=1500,
            )
            msg = response.choices[0].message
            raw = msg.content or ""
            if not raw and hasattr(msg, "reasoning") and msg.reasoning:
                raw = msg.reasoning

            # Quitar posibles wrappers markdown
            raw = re.sub(r"^```.*?\n", "", raw, flags=re.MULTILINE)
            raw = raw.replace("```", "").strip()

            title, desc, tags = self._parse_output(raw)
        except Exception as err:
            logger.warning("Error invocando LLM para metadata: %s. Usando fallback.", err)
            date_str = datetime.now().strftime("%Y-%m-%d")
            title = f"Novedades de Linux & Open Source - {date_str}"
            desc = "Edición semanal de noticias en Podcast de Linux al Sur: novedades destacadas del kernel Linux, distribuciones de escritorio, aplicaciones de código abierto y gaming en Linux."
            tags = "#Linux #OpenSource #Kernel #SoftwareLibre #GamingOnLinux"

        if not desc:
            desc = "Edición semanal de noticias en Podcast de Linux al Sur: novedades destacadas del kernel Linux, distribuciones de escritorio, aplicaciones de código abierto y gaming en Linux."

        console.print(f"📌 [green]Título generado:[/green] {title}")
        return EpisodeMetadata(
            title=title,
            description=desc,
            hashtags=tags,
            tag=tag,
        )

    def _parse_output(self, raw_text: str) -> Tuple[str, str, str]:
        """Extrae campos del formato estructurado."""
        title = ""
        desc = ""
        tags = ""

        m_title = re.search(r"TÍTULO:\s*(.*?)(?=\n\s*DESCRIPCIÓN:|\Z)", raw_text, re.DOTALL | re.IGNORECASE)
        if m_title:
            title = m_title.group(1).strip().strip("*\"'# ")

        m_desc = re.search(r"DESCRIPCIÓN:\s*(.*?)(?=\n\s*HASHTAGS:|\Z)", raw_text, re.DOTALL | re.IGNORECASE)
        if m_desc:
            desc = m_desc.group(1).strip()

        m_tags = re.search(r"HASHTAGS:\s*(.*)", raw_text, re.DOTALL | re.IGNORECASE)
        if m_tags:
            tags = m_tags.group(1).strip()

        # Fallbacks si el modelo varió el formato
        if not title:
            first_line = raw_text.strip().split("\n")[0] if raw_text.strip() else ""
            title = re.sub(r"^(?:TÍTULO:?|Title:?)\s*", "", first_line).strip() or "Novedades de Software Libre de la Semana"
        if not desc:
            desc = "Edición semanal de noticias en Podcast de Linux al Sur: novedades del kernel, distribuciones, herramientas libres y gaming."
        if not tags:
            tags = "#Linux #OpenSource #Kernel #Ubuntu #GamingOnLinux"

        return title, desc, tags

    def save_metadata(self, meta: EpisodeMetadata, dest_dir: Path, base_name: str) -> Path:
        """Guarda la metadata en formato de texto plano y JSON."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        txt_path = dest_dir / f"{base_name}_metadata.txt"
        json_path = dest_dir / f"{base_name}_metadata.json"

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"TÍTULO:\n{meta.title}\n\n")
            f.write(f"DESCRIPCIÓN:\n{meta.description}\n\n")
            f.write(f"HASHTAGS:\n{meta.hashtags}\n")

        with open(json_path, "w", encoding="utf-8") as f:
            import json
            json.dump(meta.to_dict(), f, indent=2, ensure_ascii=False)

        return txt_path
