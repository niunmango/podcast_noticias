#!/usr/bin/env python3
"""
Podcast Pipeline Orchestrator (Linux & Open Source).
Integrates RSS Ingestion, Local LLM Script Generation, Remote TTS Synthesis (.248),
and FFmpeg EBU R128 Post-Processing.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.rss_collector import collect_news, NewsBatch
from src.script_generator import ScriptGenerator, ScriptResult
from src.remote_tts_client import RemoteTTSClient
from src.audio_processor import AudioProcessor

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcast.pipeline")
console = Console()

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config(config_path: Path) -> dict:
    """Carga y valida la configuración del pipeline desde config.json."""
    if not config_path.is_file():
        console.print(f"[bold red]Error:[/bold red] No se encontró el archivo de configuración en '{config_path}'.")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_pipeline(
    config: dict,
    dry_run: bool = False,
    voice_only: Optional[str] = None,
    date_offset: Optional[str] = None,
    days: int = 7,
    test_tts: bool = False,
):
    """Ejecuta el pipeline completo de podcast."""
    start_time = datetime.now(timezone.utc)
    timestamp_str = start_time.strftime("%Y%m%d_%H%M%S")

    # Rutas base
    base_dir = Path(__file__).resolve().parent
    scripts_out_dir = base_dir / config.get("paths", {}).get("output_scripts", "output/scripts")
    audio_out_dir = base_dir / config.get("paths", {}).get("output_audio", "output/audio")
    scripts_out_dir.mkdir(parents=True, exist_ok=True)
    audio_out_dir.mkdir(parents=True, exist_ok=True)

    console.print(
        Panel.fit(
            "[bold cyan]🎙️ PIPELINE AUTOMATIZADO DE PODCAST LINUX & OPEN SOURCE[/bold cyan]\n"
            f"[dim]Inicio: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')} | Modo: {'Dry-Run' if dry_run else ('Voice-Only' if voice_only else 'Completo')}[/dim]",
            border_style="cyan",
        )
    )

    # 0. Test rápido de TTS si fue solicitado
    tts_cfg = config.get("tts", {})
    tts_client = RemoteTTSClient(
        ssh_host=tts_cfg.get("ssh_host", "170.210.80.248"),
        ssh_port=tts_cfg.get("ssh_port", 9022),
        ssh_user=tts_cfg.get("ssh_user", "golem"),
        remote_dir=tts_cfg.get("remote_dir", "/home/golem/ramiro/clonvoz"),
        remote_script=tts_cfg.get("remote_script", "generar.sh"),
        remote_log=tts_cfg.get("remote_log", "salida.log"),
        remote_output_wav=tts_cfg.get("remote_output_wav", "podcast_completo.wav"),
        poll_interval=tts_cfg.get("poll_interval_seconds", 15),
        timeout=tts_cfg.get("timeout_seconds", 1800),
    )

    if test_tts:
        console.print("[bold yellow]🧪 Modo Prueba TTS: Verificando servidor .248...[/bold yellow]")
        if not tts_client.test_connection():
            console.print("[bold red]Fallo la verificación de conexión al servidor TTS .248[/bold red]")
            sys.exit(1)
        console.print("[bold green]Conexión TTS OK.[/bold green]")
        return

    # Preparar procesador de audio
    audio_cfg = config.get("audio", {})
    audio_proc = AudioProcessor(
        loudnorm_filter=audio_cfg.get("loudnorm", "loudnorm=I=-16:TP=-1.0:LRA=11"),
        sample_rate=audio_cfg.get("sample_rate", 44100),
        bitrate=audio_cfg.get("bitrate", "192k"),
        trim_silence=audio_cfg.get("trim_silence", False),
    )

    txt_file: Optional[Path] = None
    md_file: Optional[Path] = None

    # MODO VOICE-ONLY
    if voice_only:
        txt_file = Path(voice_only)
        if not txt_file.is_file():
            console.print(f"[bold red]Error:[/bold red] Archivo de guión no encontrado: '{voice_only}'")
            sys.exit(1)
        console.print(f"📖 [cyan]Utilizando guión existente:[/cyan] {txt_file}")
    else:
        # FASE 1: INGESTA RSS
        console.print("\n[bold cyan]📡 FASE 1: Ingesta y Filtrado RSS (Últimos 7 días)[/bold cyan]")
        ref_dt = None
        if date_offset:
            try:
                ref_dt = datetime.strptime(date_offset, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                console.print(f"🗓️ [yellow]Fecha de corte simulada:[/yellow] {ref_dt.strftime('%Y-%m-%d')}")
            except ValueError:
                console.print(f"[bold red]Error:[/bold red] Formato inválido de fecha '{date_offset}'. Use YYYY-MM-DD.")
                sys.exit(1)

        rss_cfg = config.get("rss", {})
        news_batch = collect_news(
            feeds=rss_cfg.get("feeds"),
            days_limit=days or rss_cfg.get("days_limit", 7),
            reference_date=ref_dt,
        )
        console.print(f"✅ [green]Se recopilaron {len(news_batch.items)} noticias relevantes.[/green]")

        if not news_batch.items:
            console.print("[bold red]No se encontraron noticias en el periodo seleccionado. Abortando.[/bold red]")
            sys.exit(1)

        news_prompt = news_batch.to_formatted_prompt_text(max_items=12)

        # FASE 2: GENERACIÓN DE GUIÓN CON LLM
        console.print("\n[bold cyan]🤖 FASE 2: Redacción de Guión con LLM Local (.200)[/bold cyan]")
        llm_cfg = config.get("llm", {})
        sys_prompt_rel = config.get("paths", {}).get("system_prompt", "prompts/system_prompt.txt")
        sys_prompt_file = base_dir / sys_prompt_rel

        generator = ScriptGenerator(
            base_url=llm_cfg.get("base_url", "http://192.168.1.200:20128/v1"),
            api_key=llm_cfg.get("api_key", "sk-625c35c6ebef3fea-bhqllm-9dad3943"),
            model=llm_cfg.get("model", "hermes-rotator"),
            temperature=llm_cfg.get("temperature", 0.7),
            system_prompt_path=sys_prompt_file,
            target_words_min=llm_cfg.get("target_words_min", 750),
            target_words_max=llm_cfg.get("target_words_max", 850),
        )

        script_res = generator.generate_script(news_prompt)
        txt_file, md_file = generator.save_script(
            script_res,
            scripts_out_dir,
            base_name=f"podcast_{timestamp_str}",
        )
        console.print(f"💾 [green]Guión guardado exitosamente en:[/green]\n  • TXT: {txt_file}\n  • MD:  {md_file}")

    if dry_run:
        console.print(
            Panel(
                f"[bold green]🏁 Modo Dry-Run completado exitosamente.[/bold green]\n"
                f"Guión listo en: [cyan]{txt_file}[/cyan]\n"
                f"No se invocó la síntesis de voz en el servidor .248.",
                border_style="green",
            )
        )
        return

    # FASE 3: SÍNTESIS DE VOZ REMOTA (.248)
    console.print("\n[bold cyan]🎙️ FASE 3: Síntesis de Voz Remota (clonvoz en .248)[/bold cyan]")
    raw_wav_path = audio_out_dir / f"raw_voice_{timestamp_str}.wav"

    try:
        tts_client.synthesize(txt_file, raw_wav_path)
    except Exception as exc:
        console.print(f"[bold red]❌ Error durante la síntesis remota en .248: {exc}[/bold red]")
        sys.exit(1)

    # FASE 4: POST-PROCESAMIENTO Y NORMALIZACIÓN FFMPEG
    console.print("\n[bold cyan]🎛️ FASE 4: Post-procesamiento y Normalización EBU R128 (-16 LUFS)[/bold cyan]")
    final_mp3_path = audio_out_dir / f"podcast_{timestamp_str}.mp3"

    metadata = {
        "title": f"Podcast Linux & Open Source - {start_time.strftime('%Y-%m-%d')}",
        "artist": "Pipeline Automatizado",
        "album": "Semanal Open Source",
        "date": start_time.strftime("%Y"),
        "genre": "Podcast",
        "comment": "Generado automáticamente con RSS, LLM local y síntesis de voz",
    }

    try:
        audio_proc.process_podcast(raw_wav_path, final_mp3_path, metadata=metadata)
    except Exception as exc:
        console.print(f"[bold red]❌ Error durante el procesamiento de audio: {exc}[/bold red]")
        sys.exit(1)

    # RESUMEN FINAL
    total_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    audio_dur = audio_proc.get_audio_duration(final_mp3_path)

    summary_table = Table(title="Resumen del Pipeline de Podcast", border_style="green")
    summary_table.add_column("Métrica / Artefacto", style="cyan")
    summary_table.add_column("Detalle", style="bold white")

    summary_table.add_row("Guión (TXT)", str(txt_file))
    if md_file:
        summary_table.add_row("Guión (Markdown)", str(md_file))
    summary_table.add_row("Audio Raw (WAV)", str(raw_wav_path))
    summary_table.add_row("Audio Final (MP3)", str(final_mp3_path))
    summary_table.add_row("Duración Audio", f"{audio_dur/60:.2f} minutos ({audio_dur:.1f}s)")
    summary_table.add_row("Estándar Loudness", "EBU R128 (-16 LUFS, -1.0 dB True Peak)")
    summary_table.add_row("Tiempo Total Pipeline", f"{total_duration:.1f} segundos")

    console.print()
    console.print(summary_table)
    console.print(
        Panel(
            f"[bold green]🎉 ¡Episodio de podcast producido y masterizado con éxito![/bold green]\n"
            f"Archivo final disponible en: [bold yellow]{final_mp3_path}[/bold yellow]",
            border_style="green",
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline automatizado de generación de podcasts de Linux & Open Source.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Ruta al archivo config.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo recopila noticias y genera el guión, sin invocar el servidor TTS .248",
    )
    parser.add_argument(
        "--voice-only",
        type=str,
        default=None,
        metavar="PATH_GUION",
        help="Toma un archivo de guión existente y lo envía directo a sintetizar y masterizar",
    )
    parser.add_argument(
        "--date-offset",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Simula una fecha de referencia específica para la recopilación de noticias",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Cantidad de días de antigüedad para las noticias (def: 7)",
    )
    parser.add_argument(
        "--test-tts",
        action="store_true",
        help="Prueba la conectividad y estado del servidor TTS en .248 sin generar podcast",
    )

    args = parser.parse_args()
    config = load_config(Path(args.config))

    try:
        run_pipeline(
            config=config,
            dry_run=args.dry_run,
            voice_only=args.voice_only,
            date_offset=args.date_offset,
            days=args.days,
            test_tts=args.test_tts,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Pipeline interrumpido por el usuario.[/yellow]")
        sys.exit(130)
    except Exception as exc:
        logger.exception("Error crítico durante la ejecución del pipeline: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
