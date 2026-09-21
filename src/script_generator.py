#!/usr/bin/env python3
"""
Script Generator module.
Interacts with the local LLM endpoint via the OpenAI API client to draft
a 750-850 word natural Rioplatense podcast script based on RSS news.
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from openai import OpenAI
from pydantic import BaseModel
from rich.console import Console

logger = logging.getLogger("podcast.script_generator")
console = Console()


class ScriptResult(BaseModel):
    text: str
    word_count: int
    model: str
    created_at: datetime
    txt_path: Optional[str] = None
    md_path: Optional[str] = None


def count_words(text: str) -> int:
    """Cuenta palabras separadas por espacios en blanco."""
    if not text:
        return 0
    words = re.findall(r"\b\w+\b", text)
    return len(words)


def is_english_scratchpad(p_text: str) -> bool:
    """Detecta si un párrafo contiene notas internas de conteo o borrador en inglés."""
    p_lower = p_text.lower()
    keywords = [
        "count words", "word count", "words intro", "draft", "need ~", "good enough",
        "still short", "we'll write", "we will write", "let's add", "let's write",
        "para maybe", "closing:", "opening:", "block 1", "block 2", "block 3", "block 4",
        "distributions and desktop", "now development"
    ]
    if any(k in p_lower for k in keywords):
        return True
    en_matches = len(re.findall(r"\b(the|and|we|will|lets|let|can|add|about|need|draft|block|closing|opening|words|still|short|total|thats|that|good|enough|write)\b", p_lower))
    es_matches = len(re.findall(r"\b(el|la|los|las|de|en|un|una|que|por|con|para|del|al|es|se|no|lo|como|su|más)\b", p_lower))
    if en_matches >= 3 and en_matches > es_matches:
        return True
    return False


def clean_tts_prose(text: str) -> str:
    """
    Limpia y sanitiza el texto para síntesis de voz óptima:
    - Remueve etiquetas de producción como [MÚSICA], [INTRO], etc.
    - Remueve encabezados markdown (#, ##) y formato (**negrita**, *cursiva*).
    - Remueve corchetes, asteriscos y comillas.
    - Filtra notas internas de conteo y borradores en inglés (Block, Draft, words count).
    - Asegura que el locutor se identifique correctamente como Ramiro.
    - Normaliza saltos de línea y espaciado.
    """
    # Quitar posibles bloques de pensamiento (ej. <think>...</think>)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Quitar marcas entre corchetes tipo [Música de inicio], [Pausa], [Efecto], [Intro]
    text = re.sub(r"\[.*?\]", "", text)
    # Quitar encabezados markdown al inicio de líneas (# Título, ## Sección)
    text = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE)
    # Quitar negritas y cursivas
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Quitar viñetas o guiones al inicio de línea
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    # Quitar asteriscos sueltos
    text = text.replace("*", "").replace("`", "")

    # Quitar posibles notas intermedias de planificación ("Now development...", "Transition to...", etc.)
    text = re.sub(r"^(?:Now|Transition to|Segment \d|Paragraph \d).*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Corregir si el modelo omitió el nombre del locutor ("Soy y en los próximos...")
    text = re.sub(r"\bSoy\s+y\s+en\s+los\s+próximos\b", "Soy Ramiro y en los próximos", text, flags=re.IGNORECASE)

    # Cortar desde el saludo inicial típico en español rioplatense
    saludo_match = re.search(r"\b(¡?Hola\b|¡?Bienvenidos\b|¡?Muy buenas\b|Arrancamos\b)", text, re.IGNORECASE)
    if saludo_match and saludo_match.start() > 0:
        text = text[saludo_match.start():]

    # Cortar cualquier texto de notas o conteo posterior a la despedida ("¡Hasta la próxima!", etc.)
    despedida_match = re.search(r"(?:¡?Hasta la próxima!?[^\n]*|Nos escuchamos[^\n]*|¡?Chau[^\n]*)", text, re.IGNORECASE)
    if despedida_match:
        text = text[:despedida_match.end()]

    # Quitar comillas tipográficas y comillas dobles sueltas
    text = re.sub(r'["“”«»]', '', text)

    # Eliminar cualquier aparición residual de la muletilla 'che' o 'Che'
    text = re.sub(r"\b[Cc]he,?\s*", "", text)

    # Filtrar párrafos de meta-razonamiento o conteo
    paragraphs = []
    for p in text.split("\n\n"):
        p_clean = p.strip()
        if not p_clean:
            continue
        # Limpiar líneas de encabezado de borrador dentro del párrafo (Draft:, Block 1:, etc.)
        lines = [l.strip() for l in p_clean.split("\n") if l.strip()]
        valid_lines = [
            l for l in lines
            if not re.match(r"^(?:block \d|draft|closing|opening|paragraph \d|segment \d|we\'ll write|need ~|\d+ words).*$", l, re.IGNORECASE)
        ]
        if not valid_lines:
            continue
        p_joined = " ".join(valid_lines)
        if is_english_scratchpad(p_joined):
            continue
        # Descartar si el párrafo tiene palabras numeradas (palabra1 palabra2)
        numbered = len(re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]+\d+\b", p_joined))
        if numbered > 3:
            continue
        paragraphs.append(re.sub(r"\s+", " ", p_joined))

    # Verificar que el último párrafo termine de forma limpia (no cortado a mitad de palabra)
    if paragraphs:
        last_p = paragraphs[-1]
        if not re.search(r"[.!?]$", last_p):
            sentences = re.split(r"(?<=[.!?])\s+", last_p)
            if len(sentences) > 1 and re.search(r"[.!?]$", sentences[-2]):
                paragraphs[-1] = " ".join(sentences[:-1])

    return "\n\n".join(paragraphs)


class ScriptGenerator:
    def __init__(
        self,
        base_url: str = "http://192.168.1.200:20128/v1",
        api_key: str = "sk-625c35c6ebef3fea-bhqllm-9dad3943",
        model: str = "hermes-rotator",
        temperature: float = 0.2,
        system_prompt_path: Optional[Path] = None,
        target_words_min: int = 1000,
        target_words_max: int = 1150,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.target_words_min = target_words_min
        self.target_words_max = target_words_max

        # Cargar prompt del sistema
        if system_prompt_path is None:
            system_prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.txt"

        if system_prompt_path.is_file():
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt = f.read().strip()
        else:
            raise FileNotFoundError(f"No se encontró el archivo de prompt: {system_prompt_path}")

        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def generate_script(self, news_prompt_text: str, max_retries: int = 2) -> ScriptResult:
        """
        Genera el guión a partir del texto estructurado de noticias candidatas.
        Asegura que se cubran las 4 líneas temáticas y que la extensión ronde
        entre 1000 y 1150 palabras para exactamente 6 minutos de locución.
        """
        user_prompt = (
            "Acá tenés las noticias de Linux y Software Libre recopiladas en los últimos 7 días estructuradas en 4 líneas:\n"
            "1. Kernel y bajo nivel\n"
            "2. Distribuciones y Escritorio (Desktop/Distro)\n"
            "3. Aplicaciones Libres y Herramientas\n"
            "4. Gaming en Linux (Steam, Proton, Wine, Vulkan)\n\n"
            "Redactá el guión completo del episodio cubriendo OBLIGATORIAMENTE cada una de las 4 líneas temáticas en el desarrollo.\n"
            "La extensión debe ser de aproximadamente 1050 palabras (entre 1000 y 1150 palabras) para una locución fluida de exactamente 6 minutos.\n"
            "REGLAS CRÍTICAS DE ESTILO:\n"
            "- Idioma: 100% castellano rioplatense. ESTRICTAMENTE PROHIBIDO pensar o redactar en inglés, hacer conteos o notas intermedias.\n"
            "- Presentate como Ramiro en la apertura dentro de Podcast de Linux al Sur.\n"
            "- ESTRICTAMENTE PROHIBIDO usar la palabra 'che' en cualquier parte del guión (saludo, cuerpo o cierre).\n"
            "- Escribí DIRECTAMENTE en texto plano continuo lo que va a leer el locutor, sin notas previas, sin títulos de sección ni marcas tipo [MÚSICA].\n\n"
            f"{news_prompt_text}"
        )

        console.print(f"🤖 [cyan]Conectando con LLM ({self.model} en {self.base_url})...[/cyan]")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=3500,
        )

        msg = response.choices[0].message
        raw_content = msg.content or ""
        if not raw_content and hasattr(msg, "reasoning") and msg.reasoning:
            raw_content = msg.reasoning
        prose = clean_tts_prose(raw_content)
        prose = re.sub(r"\(\d+\)", "", prose)
        prose = re.sub(r"^Paragraph \d+.*?:?", "", prose, flags=re.MULTILINE)
        prose = re.sub(r"\n{3,}", "\n\n", prose).strip()
        words = count_words(prose)
        console.print(f"📝 [yellow]Primer borrador generado: {words} palabras.[/yellow]")

        best_prose = prose
        best_words = words

        # Ajustar si se desvía drásticamente (< 900 o > 1250 palabras)
        attempts = 0
        while (best_words < 900 or best_words > 1250) and attempts < max_retries:
            attempts += 1
            import time
            time.sleep(7)

            if best_words < 900:
                adjustment_prompt = (
                    f"El guión actual tiene {best_words} palabras. Por favor, expandí el desarrollo de las 4 líneas temáticas "
                    "(Kernel, Desktop/Distro, Aplicaciones Libres y Gaming), agregando más contexto técnico y ejemplos prácticos "
                    "para alcanzar aproximadamente 1050 palabras (6 minutos de duración). "
                    "IMPORTANTE: 100% castellano rioplatense, sin notas ni palabras en inglés, sin usar 'che' y sin notas de conteo. Prosa continua exclusivamente:\n\n"
                    f"{best_prose}"
                )
            else:
                adjustment_prompt = (
                    f"El guión actual tiene {best_words} palabras. Por favor, condensalo ligeramente para que tenga "
                    "aproximadamente 1050 palabras manteniendo las 4 líneas temáticas (Kernel, Desktop/Distro, Apps y Gaming). "
                    "IMPORTANTE: 100% castellano rioplatense, sin notas ni palabras en inglés, sin usar 'che' y sin notas de conteo. Prosa continua exclusivamente:\n\n"
                    f"{best_prose}"
                )

            console.print(f"🔄 [magenta]Pase de ajuste {attempts}/{max_retries}...[/magenta]")
            try:
                adj_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": adjustment_prompt},
                    ],
                    temperature=0.2,
                    max_tokens=3500,
                )
                adj_msg = adj_response.choices[0].message
                raw_content = adj_msg.content or ""
                if not raw_content and hasattr(adj_msg, "reasoning") and adj_msg.reasoning:
                    raw_content = adj_msg.reasoning
                adj_prose = clean_tts_prose(raw_content)
                adj_prose = re.sub(r"\(\d+\)", "", adj_prose)
                adj_prose = re.sub(r"^Paragraph \d+.*?:?", "", adj_prose, flags=re.MULTILINE)
                adj_prose = re.sub(r"\n{3,}", "\n\n", adj_prose).strip()
                adj_words = count_words(adj_prose)
                console.print(f"📝 [yellow]Borrador ajustado ({attempts}): {adj_words} palabras.[/yellow]")
                if adj_words >= 800:
                    best_prose = adj_prose
                    best_words = adj_words
            except Exception as e:
                console.print(f"⚠️ [yellow]Error en pase de ajuste ({e}), manteniendo borrador previo.[/yellow]")
                break

        return ScriptResult(
            text=best_prose,
            word_count=best_words,
            model=self.model,
            created_at=datetime.now(timezone.utc),
        )

    def save_script(
        self,
        script_res: ScriptResult,
        output_dir: Path,
        base_name: Optional[str] = None,
    ) -> Tuple[Path, Path]:
        """
        Guarda el guión en texto plano (.txt) para el TTS y en markdown (.md) con metadatos.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        if base_name is None:
            timestamp = script_res.created_at.strftime("%Y%m%d_%H%M%S")
            base_name = f"guion_{timestamp}"

        txt_file = output_dir / f"{base_name}.txt"
        md_file = output_dir / f"{base_name}.md"

        # Guardar .txt limpio para síntesis
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write(script_res.text)

        # Guardar .md con metadatos y conteo
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# Guión de Podcast - {script_res.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n")
            f.write(f"- **Palabras:** {script_res.word_count}\n")
            f.write(f"- **Modelo:** {script_res.model}\n")
            f.write(f"- **Estimación lectura:** ~{script_res.word_count / 135:.1f} minutos\n\n")
            f.write("## Contenido del Guión\n\n")
            f.write(script_res.text)
            f.write("\n")

        script_res.txt_path = str(txt_file)
        script_res.md_path = str(md_file)
        return txt_file, md_file


if __name__ == "__main__":
    import argparse
    from src.rss_collector import collect_news

    parser = argparse.ArgumentParser(description="Generador de guión con LLM local.")
    parser.add_argument("--days", type=int, default=7, help="Días máximos de antigüedad de noticias (def: 7)")
    parser.add_argument("--output-dir", type=str, default="output/scripts", help="Directorio de guardado")
    args = parser.parse_args()

    # Cargar config si existe
    config_path = Path("config.json")
    cfg = {}
    if config_path.is_file():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    llm_cfg = cfg.get("llm", {})
    gen = ScriptGenerator(
        base_url=llm_cfg.get("base_url", "http://192.168.1.200:20128/v1"),
        api_key=llm_cfg.get("api_key", "sk-625c35c6ebef3fea-bhqllm-9dad3943"),
        model=llm_cfg.get("model", "hermes-rotator"),
    )

    console.print("[cyan]1. Obteniendo noticias recientes...[/cyan]")
    news_batch = collect_news(days_limit=args.days)
    prompt_text = news_batch.to_formatted_prompt_text(max_items=25)

    console.print("[cyan]2. Redactando guión con LLM local...[/cyan]")
    res = gen.generate_script(prompt_text)

    txt_p, md_p = gen.save_script(res, Path(args.output_dir))
    console.print(f"[bold green]✅ Guión generado con éxito ({res.word_count} palabras)![/bold green]")
    console.print(f"📄 Texto plano (TTS): [blue]{txt_p}[/blue]")
    console.print(f"📑 Markdown: [blue]{md_p}[/blue]")
