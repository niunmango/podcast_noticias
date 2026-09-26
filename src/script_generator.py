#!/usr/bin/env python3
"""
Script Generator module.
Modular podcast script generator in 6 distinct sections:
1. intro (greeting as AI bot + summary of topics, max 200 words)
2. kernel (Kernel Linux & low-level, ~220-250 words)
3. distros (Desktop & Distributions, ~220-250 words)
4. apps (Open Source apps & tools, ~220-250 words)
5. gaming (Gaming on Linux, ~160-190 words, max 200 words)
6. outro (farewell related to topics + fixed closing phrase, max 200 words)

Total target length: between 1000 and 1200 words (~6 to 7 minutes of speech).
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Union

from openai import OpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from src.rss_collector import NewsBatch, NewsItem

logger = logging.getLogger("podcast.script_generator")
console = Console()

FIXED_OUTRO_PHRASE = (
    "Gracias por acompañarnos en esta edición de noticias de Podcast de Linux al Sur. "
    "Los invito a sintonizar el episodio temático de mitad de semana y a reencontrarnos "
    "la próxima semana con más novedades del ecosistema libre. Hasta la próxima."
)


class ScriptResult(BaseModel):
    text: str
    word_count: int
    model: str
    created_at: datetime
    txt_path: Optional[str] = None
    md_path: Optional[str] = None
    sections: Dict[str, str] = Field(default_factory=dict)
    section_word_counts: Dict[str, int] = Field(default_factory=dict)


def count_words(text: str) -> int:
    """Cuenta palabras separadas por espacios en blanco."""
    if not text:
        return 0
    words = re.findall(r"\b\w+\b", text)
    return len(words)


def clean_section_prose(text: str) -> str:
    """
    Limpia y sanitiza el texto de una sección individual para síntesis de voz:
    - Remueve etiquetas de razonamiento <think>...</think>
    - Remueve encabezados markdown (#, ##) y formato (**negrita**, *cursiva*)
    - Remueve etiquetas entre corchetes tipo [MÚSICA], [INTRO], [PAUSA]
    - Remueve viñetas, guiones y asteriscos
    - Remueve comillas dobles y tipográficas
    - Remueve muletillas no permitidas como 'che'
    - Remueve encabezados de sección residuales tipo 'Bloque 1 - Kernel:'
    - Normaliza espacios en blanco
    """
    if not text:
        return ""

    # Quitar posibles bloques de pensamiento (ej. <think>...</think>)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Quitar marcas entre corchetes
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
    text = text.replace("*", "").replace("`", "")
    # Quitar comillas tipográficas y comillas dobles
    text = re.sub(r'["“”«»]', '', text)
    # Quitar números entre paréntesis tipo (1), (2)
    text = re.sub(r"\(\d+\)", "", text)
    # Quitar cualquier aparición residual de la muletilla 'che' o 'Che'
    text = re.sub(r"\b[Cc]he,?\s*", "", text)
    # Quitar etiquetas iniciales de sección tipo 'Bloque 1 - Kernel:', 'Intro:', etc.
    text = re.sub(
        r"^(?:intro|apertura|bloque \d+[\s:-]+[a-záéíóú\s]+|bloque \d+|kernel|distros|desktop|aplicaciones libres|apps|gaming|outro|cierre)[\s:-]+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Filtrar párrafos o líneas con notas de conteo o borrador en inglés
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    valid_lines = [
        l for l in lines
        if not re.match(r"^(?:block \d|draft|closing|opening|paragraph \d|segment \d|we\'ll write|need ~|\d+ words).*$", l, re.IGNORECASE)
    ]

    joined = " ".join(valid_lines)
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined


def enforce_word_limit(text: str, max_words: int, suffix: Optional[str] = None) -> str:
    """
    Asegura de forma estricta que el texto no supere max_words.
    Si se especifica suffix, garantiza que dicho sufijo quede al final intacto.
    """
    words = count_words(text)
    if words <= max_words:
        return text

    suffix_clean = suffix.strip() if suffix else ""
    suffix_words = count_words(suffix_clean) if suffix_clean else 0

    available_words = max_words - suffix_words
    if available_words <= 20:
        return suffix_clean or text

    # Si hay sufijo al final del texto actual, retirarlo para el recorte
    base_text = text
    if suffix_clean and base_text.endswith(suffix_clean):
        base_text = base_text[: -len(suffix_clean)].strip()

    sentences = re.split(r"(?<=[.!?])\s+", base_text)
    kept_sentences = []
    current_count = 0

    for s in sentences:
        s_words = count_words(s)
        if current_count + s_words <= available_words:
            kept_sentences.append(s)
            current_count += s_words
        else:
            break

    trimmed_base = " ".join(kept_sentences).strip()
    if not trimmed_base:
        # Si ni la primera oración entra, recortar a nivel de palabras
        word_list = re.findall(r"\S+", base_text)
        trimmed_base = " ".join(word_list[:available_words])
        if not trimmed_base.endswith((".", "!", "?")):
            trimmed_base += "..."

    if suffix_clean:
        return f"{trimmed_base} {suffix_clean}".strip()
    return trimmed_base


class ScriptGenerator:
    def __init__(
        self,
        base_url: str = "http://192.168.1.200:20128/v1",
        api_key: str = "sk-625c35c6ebef3fea-bhqllm-9dad3943",
        model: str = "hermes-rotator",
        temperature: float = 0.2,
        system_prompt_path: Optional[Path] = None,
        target_words_min: int = 1000,
        target_words_max: int = 1200,
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
            self.system_prompt = "Sos el bot de inteligencia artificial de Podcast de Linux al Sur."

        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 3500,
        temperature: Optional[float] = None,
    ) -> str:
        """Invoca al LLM local y limpia el texto para locución."""
        temp = self.temperature if temperature is None else temperature
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temp,
            max_tokens=max_tokens,
        )

        msg = response.choices[0].message
        raw_content = msg.content or ""
        if not raw_content and hasattr(msg, "reasoning") and msg.reasoning:
            raw_content = msg.reasoning

        return clean_section_prose(raw_content)

    def _generate_section_with_retry(
        self,
        section_name: str,
        system_prompt: str,
        user_prompt: str,
        min_words: int,
        max_words: int,
        hard_max: Optional[int] = None,
        max_retries: int = 1,
    ) -> str:
        """Genera una sección y aplica pase de ajuste si se desvía del conteo deseado."""
        prose = self._call_llm(system_prompt, user_prompt)
        words = count_words(prose)

        attempts = 0
        while (words < min_words or words > max_words) and attempts < max_retries:
            attempts += 1
            if words < min_words:
                adj_prompt = (
                    f"El texto actual para la sección '{section_name}' tiene {words} palabras y debe tener entre {min_words} y {max_words} palabras. "
                    "Por favor, expandí el análisis técnico, el impacto práctico y la arquitectura de las noticias ya mencionadas "
                    f"para alcanzar entre {min_words} y {max_words} palabras. "
                    "REGLAS: 100% castellano rioplatense (voseo técnico, sin 'che'). Prosa continua sin comandos ni títulos:\n\n"
                    f"{prose}"
                )
            else:
                adj_prompt = (
                    f"El texto actual para la sección '{section_name}' tiene {words} palabras y debe tener un máximo de {max_words} palabras"
                    f"{f' (LÍMITE ESTRICTO: {hard_max} palabras)' if hard_max else ''}. "
                    f"Por favor, condensalo para que quede entre {min_words} y {max_words} palabras manteniendo las explicaciones clave. "
                    "REGLAS: 100% castellano rioplatense (voseo técnico, sin 'che'). Prosa continua sin comandos ni títulos:\n\n"
                    f"{prose}"
                )

            adj_prose = self._call_llm(system_prompt, adj_prompt)
            adj_words = count_words(adj_prose)
            if adj_words >= min_words - 20:
                prose = adj_prose
                words = adj_words

        if hard_max and words > hard_max:
            prose = enforce_word_limit(prose, hard_max)

        return prose

    def generate_script(
        self,
        news_input: Union[NewsBatch, str, dict],
        max_retries: int = 1,
    ) -> ScriptResult:
        """
        Genera el guión dividiendo el proceso en 6 partes:
        1. kernel (~220-250 palabras)
        2. distros (~220-250 palabras)
        3. apps (~220-250 palabras)
        4. gaming (~160-190 palabras, máx 200)
        5. intro (con saludo y resumen de los 4 bloques, máx 200 palabras)
        6. outro (despedida + frase fija de cierre, máx 200 palabras)

        Total acumulado: entre 1000 y 1200 palabras.
        """
        console.print("[bold cyan]🔄 Generando guión modular en 6 secciones...[/bold cyan]")

        # 1. Obtener noticias clasificadas
        cats: Dict[str, list] = {}
        if isinstance(news_input, NewsBatch):
            cats = news_input.categorize_items()
        elif hasattr(news_input, "categorize_items"):
            cats = news_input.categorize_items()
        else:
            # Si se pasó un texto plano o dict, procesarlo genéricamente
            cats = {
                "kernel": [],
                "desktop_distro": [],
                "aplicaciones_libres": [],
                "gaming": [],
            }

        k_news = "\n\n".join(it.to_snippet() for it in cats.get("kernel", [])[:3])
        d_news = "\n\n".join(it.to_snippet() for it in cats.get("desktop_distro", [])[:3])
        a_news = "\n\n".join(it.to_snippet() for it in cats.get("aplicaciones_libres", [])[:3])
        g_news = "\n\n".join(it.to_snippet() for it in cats.get("gaming", [])[:2])

        common_rules = (
            "- Idioma: 100% castellano rioplatense profesional con voseo técnico (sin usar 'che').\n"
            "- Prosa continua para síntesis de voz (sin títulos #, sin viñetas, sin corchetes).\n"
            "- Prohibido dictar comandos de consola o flags con guiones.\n"
            "- Escribí los números en palabras habladas.\n"
            "- Basate EXCLUSIVAMENTE en las noticias provistas. PROHIBIDO inventar novedades o versiones pasadas."
        )

        sections: Dict[str, str] = {}
        section_words: Dict[str, int] = {}

        # PASO 1: Generar Bloque Kernel (~220-250 palabras)
        console.print("  [cyan]• 1/6 Redactando bloque Kernel y bajo nivel...[/cyan]")
        k_prompt = f"""Redactá el bloque temático de Kernel y bajo nivel para el podcast.
Objetivo de extensión: entre 220 y 250 palabras.

Noticias provistas:
{k_news}

REGLAS ESPECÍFICAS:
{common_rules}
- Redactá directamente el contenido técnico del bloque con desarrollo claro y conceptual.
- NO incluyas saludo de apertura ni despedida general del programa."""

        kernel_text = self._generate_section_with_retry(
            section_name="Kernel",
            system_prompt=self.system_prompt,
            user_prompt=k_prompt,
            min_words=210,
            max_words=260,
        )
        sections["kernel"] = kernel_text
        section_words["kernel"] = count_words(kernel_text)
        console.print(f"    [green]Kernel listo: {section_words['kernel']} palabras.[/green]")

        # PASO 2: Generar Bloque Distros (~220-250 palabras)
        console.print("  [cyan]• 2/6 Redactando bloque Distribuciones y Escritorio...[/cyan]")
        d_prompt = f"""Redactá el bloque temático de Distribuciones y Entornos de Escritorio.
Objetivo de extensión: entre 220 y 250 palabras.

Noticias provistas:
{d_news}

REGLAS ESPECÍFICAS:
{common_rules}
- Iniciá con una transición fluida desde el bloque anterior hacia el escritorio y distribuciones.
- Queda TERMINANTEMENTE PROHIBIDO inventar lanzamientos o versiones antiguas no presentes en las noticias (como Fedora 38, Ubuntu 22.04, GNOME 43, etc.).
- NO incluyas saludo inicial ni despedida."""

        distros_text = self._generate_section_with_retry(
            section_name="Distros",
            system_prompt=self.system_prompt,
            user_prompt=d_prompt,
            min_words=210,
            max_words=260,
        )
        sections["distros"] = distros_text
        section_words["distros"] = count_words(distros_text)
        console.print(f"    [green]Distros listo: {section_words['distros']} palabras.[/green]")

        # PASO 3: Generar Bloque Apps (~220-250 palabras)
        console.print("  [cyan]• 3/6 Redactando bloque Aplicaciones Libres y Herramientas...[/cyan]")
        a_prompt = f"""Redactá el bloque temático de Aplicaciones Libres y Herramientas del ecosistema open source.
Objetivo de extensión: entre 220 y 250 palabras.

Noticias provistas:
{a_news}

REGLAS ESPECÍFICAS:
{common_rules}
- Iniciá con una transición fluida hacia las aplicaciones y herramientas libres.
- Explicá la utilidad práctica y ventajas técnicas de cada desarrollo.
- NO incluyas saludo inicial ni despedida."""

        apps_text = self._generate_section_with_retry(
            section_name="Apps",
            system_prompt=self.system_prompt,
            user_prompt=a_prompt,
            min_words=210,
            max_words=260,
        )
        sections["apps"] = apps_text
        section_words["apps"] = count_words(apps_text)
        console.print(f"    [green]Apps listo: {section_words['apps']} palabras.[/green]")

        # PASO 4: Generar Bloque Gaming (máx 200 palabras, target 160-190)
        console.print("  [cyan]• 4/6 Redactando bloque Gaming en Linux (máx 200 palabras)...[/cyan]")
        g_prompt = f"""Redactá el bloque temático de Gaming en Linux (Steam, Proton, Wine, Vulkan).
Objetivo de extensión: entre 160 y 190 palabras.
LÍMITE ESTRICTO: NO SUPERAR LAS 200 PALABRAS BAJO NINGUNA CIRCUNSTANCIA.

Noticias provistas:
{g_news}

REGLAS ESPECÍFICAS:
{common_rules}
- Iniciá con una transición fluida al mundo de los videojuegos en Linux.
- NO incluyas la despedida final del programa."""

        gaming_text = self._generate_section_with_retry(
            section_name="Gaming",
            system_prompt=self.system_prompt,
            user_prompt=g_prompt,
            min_words=150,
            max_words=195,
            hard_max=200,
        )
        sections["gaming"] = gaming_text
        section_words["gaming"] = count_words(gaming_text)
        console.print(f"    [green]Gaming listo: {section_words['gaming']} palabras.[/green]")

        # PASO 5: Generar Intro (con saludo bot IA + resumen de los 4 bloques, máx 200 palabras)
        console.print("  [cyan]• 5/6 Redactando Intro con saludo y resumen de los 4 temas...[/cyan]")
        middle_summary = (
            f"1. Kernel: {kernel_text[:220]}...\n"
            f"2. Distros y Escritorio: {distros_text[:220]}...\n"
            f"3. Aplicaciones Libres: {apps_text[:220]}...\n"
            f"4. Gaming en Linux: {gaming_text[:220]}..."
        )

        intro_prompt = f"""Sos el bot de inteligencia artificial de Podcast de Linux al Sur.
Redactá la APERTURA (Intro) del episodio semanal de noticias.
Objetivo de extensión: entre 120 y 150 palabras.
LÍMITE ESTRICTO: NO SUPERAR LAS 200 PALABRAS.

En este episodio se desarrollarán exactamente los siguientes 4 bloques temáticos:
{middle_summary}

REGLAS OBLIGATORIAS:
- Presentate explícitamente como el bot de inteligencia artificial de Podcast de Linux al Sur (NO digas que sos humano ni uses el nombre Ramiro).
- Brindá un saludo cálido y una síntesis atractiva de los 4 temas principales que se escucharán en la entrega de hoy.
- Debe conectar de manera fluida y directa hacia el inicio del bloque de kernel.
{common_rules}"""

        intro_text = self._generate_section_with_retry(
            section_name="Intro",
            system_prompt=self.system_prompt,
            user_prompt=intro_prompt,
            min_words=110,
            max_words=160,
            hard_max=200,
        )
        sections["intro"] = intro_text
        section_words["intro"] = count_words(intro_text)
        console.print(f"    [green]Intro lista: {section_words['intro']} palabras.[/green]")

        # PASO 6: Generar Outro (despedida + frase fija de cierre, máx 200 palabras)
        console.print("  [cyan]• 6/6 Redactando Outro con despedida y frase fija de cierre...[/cyan]")
        outro_prompt = f"""Sos el bot de inteligencia artificial de Podcast de Linux al Sur.
Redactá el CIERRE (Outro) del episodio semanal de noticias.
Objetivo de extensión: entre 80 y 120 palabras.
LÍMITE ESTRICTO: NO SUPERAR LAS 200 PALABRAS.

ESTRUCTURA DEL CIERRE:
1. Breve despedida y reflexión integradora relacionada con los temas tratados hoy (~40 a 60 palabras):
{middle_summary}

2. Cierre OBLIGATORIO finalizando con la siguiente frase fija exacta:
"{FIXED_OUTRO_PHRASE}"

REGLAS:
{common_rules}"""

        outro_text = self._generate_section_with_retry(
            section_name="Outro",
            system_prompt=self.system_prompt,
            user_prompt=outro_prompt,
            min_words=80,
            max_words=140,
            hard_max=200,
        )

        # Garantizar que el outro termine exactamente con la frase fija
        if FIXED_OUTRO_PHRASE not in outro_text:
            outro_base = re.sub(
                r"(?:Gracias por acompañarnos.*|Hasta la próxima.*|Nos vemos.*)$",
                "",
                outro_text,
                flags=re.IGNORECASE,
            ).strip()
            outro_text = f"{outro_base} {FIXED_OUTRO_PHRASE}".strip()

        outro_text = enforce_word_limit(outro_text, max_words=200, suffix=FIXED_OUTRO_PHRASE)
        sections["outro"] = outro_text
        section_words["outro"] = count_words(outro_text)
        console.print(f"    [green]Outro listo: {section_words['outro']} palabras.[/green]")

        # BALANCEO FINAL DE PALABRAS (META TOTAL: 1000 A 1200 PALABRAS)
        total_words = sum(section_words.values())
        console.print(f"📊 [yellow]Conteo acumulado preliminar: {total_words} palabras (Rango: {self.target_words_min}-{self.target_words_max}).[/yellow]")

        # Si supera 1200 palabras, recortar suavemente en las secciones del medio sin tocar intro, gaming ni outro
        if total_words > self.target_words_max:
            excess = total_words - self.target_words_max
            for sec_key in ("apps", "distros", "kernel"):
                if excess <= 0:
                    break
                current_len = section_words[sec_key]
                if current_len > 230:
                    reduce_by = min(excess, current_len - 220)
                    new_target = current_len - reduce_by
                    sections[sec_key] = enforce_word_limit(sections[sec_key], max_words=new_target)
                    section_words[sec_key] = count_words(sections[sec_key])
                    excess -= reduce_by

        # Ensamblar guión final ordenado
        ordered_keys = ["intro", "kernel", "distros", "apps", "gaming", "outro"]
        full_prose = "\n\n".join(sections[k] for k in ordered_keys)
        final_word_count = count_words(full_prose)

        return ScriptResult(
            text=full_prose,
            word_count=final_word_count,
            model=self.model,
            created_at=datetime.now(timezone.utc),
            sections=sections,
            section_word_counts=section_words,
        )

    def save_script(
        self,
        script_res: ScriptResult,
        output_dir: Path,
        base_name: Optional[str] = None,
    ) -> Tuple[Path, Path]:
        """
        Guarda el guión en texto plano (.txt) para síntesis TTS y en markdown (.md)
        con desglose estructurado de las 6 secciones y métricas.
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

        # Guardar .md con metadatos y desglose de secciones
        swc = script_res.section_word_counts
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# Guión de Podcast - {script_res.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n")
            f.write(f"- **Palabras totales:** {script_res.word_count} (Rango meta: 1000 - 1200 palabras)\n")
            f.write(f"- **Modelo:** {script_res.model}\n")
            f.write(f"- **Estimación lectura:** ~{script_res.word_count / 135:.1f} minutos\n\n")
            f.write("### Desglose por Secciones (6 partes):\n")
            f.write(f"1. **Intro:** {swc.get('intro', 0)} palabras (límite máx: 200)\n")
            f.write(f"2. **Kernel:** {swc.get('kernel', 0)} palabras\n")
            f.write(f"3. **Distros:** {swc.get('distros', 0)} palabras\n")
            f.write(f"4. **Apps:** {swc.get('apps', 0)} palabras\n")
            f.write(f"5. **Gaming:** {swc.get('gaming', 0)} palabras (límite máx: 200)\n")
            f.write(f"6. **Outro:** {swc.get('outro', 0)} palabras (límite máx: 200)\n\n")
            f.write("## Contenido Completo del Guión (Texto Continuo TTS)\n\n")
            f.write(script_res.text)
            f.write("\n\n## Secciones Detalladas\n\n")
            f.write(f"### 1. Intro\n{script_res.sections.get('intro', '')}\n\n")
            f.write(f"### 2. Kernel y Bajo Nivel\n{script_res.sections.get('kernel', '')}\n\n")
            f.write(f"### 3. Distribuciones y Escritorio\n{script_res.sections.get('distros', '')}\n\n")
            f.write(f"### 4. Aplicaciones Libres y Herramientas\n{script_res.sections.get('apps', '')}\n\n")
            f.write(f"### 5. Gaming en Linux\n{script_res.sections.get('gaming', '')}\n\n")
            f.write(f"### 6. Outro\n{script_res.sections.get('outro', '')}\n")

        script_res.txt_path = str(txt_file)
        script_res.md_path = str(md_file)
        return txt_file, md_file


if __name__ == "__main__":
    import argparse
    from src.rss_collector import collect_news

    parser = argparse.ArgumentParser(description="Generador modular de guión en 6 secciones con LLM local.")
    parser.add_argument("--days", type=int, default=7, help="Días máximos de antigüedad de noticias (def: 7)")
    parser.add_argument("--output-dir", type=str, default="output/scripts", help="Directorio de guardado")
    args = parser.parse_args()

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
        target_words_min=llm_cfg.get("target_words_min", 1000),
        target_words_max=llm_cfg.get("target_words_max", 1200),
    )

    console.print("[cyan]1. Obteniendo noticias recientes...[/cyan]")
    news_batch = collect_news(days_limit=args.days)

    console.print("[cyan]2. Redactando guión modular en 6 secciones con LLM local...[/cyan]")
    res = gen.generate_script(news_batch)

    txt_p, md_p = gen.save_script(res, Path(args.output_dir))

    table = Table(title="Desglose del Guión Generado", border_style="cyan")
    table.add_column("Sección", style="cyan")
    table.add_column("Palabras", style="magenta")
    table.add_column("Límite / Meta", style="green")

    limits = {
        "intro": "Máx 200 (~120-150)",
        "kernel": "~220-250",
        "distros": "~220-250",
        "apps": "~220-250",
        "gaming": "Máx 200 (~160-190)",
        "outro": "Máx 200 (~80-120)",
    }
    for sec in ["intro", "kernel", "distros", "apps", "gaming", "outro"]:
        table.add_row(sec.capitalize(), str(res.section_word_counts.get(sec, 0)), limits[sec])
    table.add_row("TOTAL", str(res.word_count), "1000 - 1200", style="bold yellow")

    console.print(table)
    console.print(f"[bold green]✅ Guión guardado con éxito ({res.word_count} palabras)![/bold green]")
    console.print(f"📄 Texto plano (TTS): [blue]{txt_p}[/blue]")
    console.print(f"📑 Markdown: [blue]{md_p}[/blue]")
