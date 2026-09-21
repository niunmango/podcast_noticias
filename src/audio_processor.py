#!/usr/bin/env python3
"""
Audio Processor module.
Handles post-processing, silence trimming, and EBU R128 (-16 LUFS) normalization via FFmpeg.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from rich.console import Console

logger = logging.getLogger("podcast.audio_processor")
console = Console()


class AudioProcessor:
    def __init__(
        self,
        loudnorm_filter: str = "loudnorm=I=-16:TP=-1.0:LRA=11",
        sample_rate: int = 44100,
        bitrate: str = "192k",
        trim_silence: bool = False,
        denoise: bool = True,
        denoise_filter: str = "highpass=f=80,lowpass=f=12000,adeclick,afftdn=nr=12:nf=-40:tn=1",
    ):
        self.loudnorm_filter = loudnorm_filter
        self.sample_rate = sample_rate
        self.bitrate = bitrate
        self.trim_silence = trim_silence
        self.denoise = denoise
        self.denoise_filter = denoise_filter

    def check_ffmpeg(self) -> bool:
        """Verifica que ffmpeg y ffprobe estén instalados."""
        try:
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except Exception:
            return False

    def get_audio_duration(self, audio_path: Path) -> float:
        """Obtiene la duración en segundos de un archivo de audio usando ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return float(res.stdout.strip())
        except Exception as e:
            logger.warning("No se pudo obtener duración con ffprobe: %s", e)
            return 0.0

    def process_podcast(
        self,
        raw_voice_wav: Path,
        final_mp3_path: Path,
        metadata: Optional[Dict[str, str]] = None,
        bg_music_path: Optional[Path] = None,
        bg_volume: float = 0.08,
    ) -> Path:
        """
        Aplica filtro reductor de ruido (afftdn + adeclick + paso banda) a la voz,
        normalización EBU R128 (-16 LUFS), opcional recorte de silencios,
        mezcla con música de fondo y conversión a MP3 con metadatos ID3.
        """
        if not raw_voice_wav.is_file():
            raise FileNotFoundError(f"Archivo de voz no encontrado: '{raw_voice_wav}'")

        final_mp3_path.parent.mkdir(parents=True, exist_ok=True)

        audio_filters = []

        # 1. Recorte de silencios si está habilitado
        if self.trim_silence:
            audio_filters.append("silenceremove=stop_periods=-1:stop_duration=1.2:stop_threshold=-40dB")

        # 2. Normalización LUFS estándar podcast (-16 LUFS, TP -1.0 dB)
        audio_filters.append(self.loudnorm_filter)

        filter_str = ",".join(audio_filters)
        voice_clean_filter = f"{self.denoise_filter}," if self.denoise else ""

        cmd = ["ffmpeg", "-y", "-i", str(raw_voice_wav)]

        # Mezcla opcional con música de fondo sutil
        if bg_music_path and bg_music_path.is_file():
            console.print("🎛️  [cyan]Aplicando filtro denoiser (afftdn + adeclick) a la voz y mezclando fondo...[/cyan]")
            cmd.extend([
                "-stream_loop", "-1", "-i", str(bg_music_path),
                "-filter_complex",
                f"[0:a]{voice_clean_filter}aformat=sample_rates={self.sample_rate}:channel_layouts=stereo[v];"
                f"[1:a]aformat=sample_rates={self.sample_rate}:channel_layouts=stereo,volume={bg_volume}[bg];"
                f"[v][bg]amix=inputs=2:duration=first:weights=1 1[mixed];"
                f"[mixed]{filter_str}[out]",
                "-map", "[out]",
            ])
        else:
            console.print("🎛️  [cyan]Aplicando filtro denoiser (afftdn + adeclick) y normalizando voz...[/cyan]")
            all_filters = []
            if self.denoise:
                all_filters.append(self.denoise_filter)
            if self.trim_silence:
                all_filters.append("silenceremove=stop_periods=-1:stop_duration=1.2:stop_threshold=-40dB")
            all_filters.append(self.loudnorm_filter)
            cmd.extend(["-af", ",".join(all_filters)])

        cmd.extend([
            "-c:a", "libmp3lame",
            "-b:a", self.bitrate,
            "-ar", str(self.sample_rate),
        ])

        # Metadatos ID3
        if metadata:
            for k, v in metadata.items():
                if v:
                    cmd.extend(["-metadata", f"{k}={v}"])

        cmd.append(str(final_mp3_path))

        console.print(f"🎛️  [cyan]Procesando y normalizando audio a estándar podcast (-16 LUFS)...[/cyan]")
        logger.debug("Comando FFmpeg: %s", " ".join(cmd))

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            console.print(f"❌ [bold red]Error en FFmpeg:\n{res.stderr}[/bold red]")
            raise RuntimeError(f"Error procesando audio con FFmpeg: {res.stderr}")

        duration = self.get_audio_duration(final_mp3_path)
        size_mb = final_mp3_path.stat().st_size / (1024 * 1024)
        console.print(f"🏆 [bold green]Audio final generado:[/bold green] {final_mp3_path}")
        console.print(f"📊 [cyan]Duración: {duration/60:.2f} min ({duration:.1f}s) | Tamaño: {size_mb:.2f} MB[/cyan]")

        return final_mp3_path


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Procesador y normalizador de audio para podcasts.")
    parser.add_argument("--input", type=str, required=True, help="Archivo WAV de entrada")
    parser.add_argument("--output", type=str, required=True, help="Archivo MP3 de salida")
    parser.add_argument("--trim-silence", action="store_true", help="Habilita recorte de silencios prolongados")
    args = parser.parse_args()

    processor = AudioProcessor(trim_silence=args.trim_silence)
    meta = {
        "title": "Noticias de Software Libre de la Semana",
        "artist": "Pipeline Automatizado",
        "album": "Noticias de Software Libre de la Semana",
        "genre": "Podcast",
    }
    processor.process_podcast(Path(args.input), Path(args.output), metadata=meta)
