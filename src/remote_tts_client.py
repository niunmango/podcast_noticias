#!/usr/bin/env python3
"""
Remote TTS Client module.
Communicates via SSH/SFTP with the remote Golem server (170.210.80.248:9022)
to run VoxCPM2 2B synthesis through clonvoz and retrieve the generated WAV.
"""

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import paramiko
from rich.console import Console

logger = logging.getLogger("podcast.remote_tts")
console = Console()


class RemoteTTSClient:
    def __init__(
        self,
        ssh_host: str = "170.210.80.248",
        ssh_port: int = 9022,
        ssh_user: str = "golem",
        remote_dir: str = "/home/golem/ramiro/clonvoz",
        remote_script: str = "generar.sh",
        remote_log: str = "salida.log",
        remote_output_wav: str = "podcast_completo.wav",
        poll_interval: int = 15,
        timeout: int = 1800,
    ):
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.remote_dir = remote_dir.rstrip("/")
        self.remote_script = remote_script
        self.remote_log = remote_log
        self.remote_output_wav = remote_output_wav
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _get_ssh_client(self) -> paramiko.SSHClient:
        """Crea y conecta un cliente SSH usando las llaves locales del usuario."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=self.ssh_host,
            port=self.ssh_port,
            username=self.ssh_user,
            look_for_keys=True,
            timeout=20,
        )
        return ssh

    def test_connection(self) -> bool:
        """Verifica que el servidor remoto responde y que el directorio clonvoz existe."""
        try:
            console.print(f"🔌 [cyan]Probando conexión SSH a {self.ssh_user}@{self.ssh_host}:{self.ssh_port}...[/cyan]")
            ssh = self._get_ssh_client()
            stdin, stdout, stderr = ssh.exec_command(f"test -d '{self.remote_dir}' && echo 'OK' || echo 'DIR_NOT_FOUND'")
            out = stdout.read().decode().strip()
            ssh.close()
            if out == "OK":
                console.print(f"✅ [bold green]Conexión SSH exitosa y directorio {self.remote_dir} verificado.[/bold green]")
                return True
            else:
                console.print(f"❌ [bold red]Directorio remoto '{self.remote_dir}' no encontrado.[/bold red]")
                return False
        except Exception as err:
            console.print(f"❌ [bold red]Fallo de conexión SSH: {err}[/bold red]")
            return False

    def synthesize(
        self,
        local_guion_path: Path,
        local_wav_dest: Path,
        custom_output_name: Optional[str] = None,
    ) -> Path:
        """
        Sube el guión al servidor remoto, lanza la síntesis en background,
        monitorea el log remoto hasta completar y descarga el WAV resultante.
        """
        if not local_guion_path.is_file():
            raise FileNotFoundError(f"No se encontró el archivo de guión en '{local_guion_path}'")

        remote_out_file = custom_output_name or self.remote_output_wav
        remote_guion = f"{self.remote_dir}/guion.txt"
        remote_wav = f"{self.remote_dir}/{remote_out_file}"
        remote_log_path = f"{self.remote_dir}/{self.remote_log}"

        console.print(f"🌐 [cyan]Conectando a {self.ssh_user}@{self.ssh_host}:{self.ssh_port}...[/cyan]")
        ssh = self._get_ssh_client()
        sftp = ssh.open_sftp()

        try:
            # 1. Limpiar archivo de audio previo y log previo en el remoto
            try:
                sftp.remove(remote_wav)
            except IOError:
                pass
            ssh.exec_command(f"rm -f '{remote_wav}'")

            # 2. Subir el guión
            console.print(f"📤 [cyan]Subiendo guión '{local_guion_path.name}' a '{remote_guion}'...[/cyan]")
            sftp.put(str(local_guion_path), remote_guion)
            console.print("✅ [green]Guión subido exitosamente al servidor remoto.[/green]")

            # 3. Lanzar la ejecución del script remoto
            console.print(f"🚀 [cyan]Ejecutando script de síntesis '{self.remote_script}' en {self.remote_dir}...[/cyan]")
            cmd = f"cd {self.remote_dir} && OUTPUT='{remote_out_file}' bash {self.remote_script}"
            stdin, stdout, stderr = ssh.exec_command(cmd)

            # Esperar a que el proceso en background se lance y devuelva salida inicial con el PID
            init_out = stdout.read().decode(errors="replace").strip()
            if init_out:
                console.print(f"[dim]{init_out}[/dim]")

            pid_match = re.search(r"PID:\s*(\d+)", init_out)
            remote_pid = pid_match.group(1) if pid_match else None
            if remote_pid:
                console.print(f"🎯 [cyan]Proceso remoto detectado con PID:[/cyan] {remote_pid}")

            # 4. Monitorear progreso de síntesis
            console.print(f"⏳ [yellow]Sintetizando en GPU remota (VoxCPM2)... Monitoreando cada {self.poll_interval}s...[/yellow]")
            start_time = time.time()
            file_ready = False

            while not file_ready:
                elapsed = time.time() - start_time
                if elapsed > self.timeout:
                    raise TimeoutError(f"Tiempo de espera agotado ({self.timeout}s) esperando síntesis de voz.")

                time.sleep(self.poll_interval)

                # Drenar posibles buffers
                while stdout.channel.recv_ready():
                    stdout.channel.recv(1024)

                # Consultar últimas líneas del log remoto
                try:
                    _, log_out, _ = ssh.exec_command(f"tail -n 6 '{remote_log_path}'")
                    log_tail = log_out.read().decode("utf-8", errors="replace").strip()
                    if log_tail:
                        console.print(f"[dim]--- Estado remoto [{int(elapsed)}s] ---[/dim]")
                        for line in log_tail.split("\n"):
                            console.print(f"[dim]{line}[/dim]")
                except Exception:
                    pass

                # Verificar estado del proceso y del archivo
                pid_running = True
                if remote_pid:
                    _, pid_check, _ = ssh.exec_command(f"kill -0 {remote_pid} 2>/dev/null && echo 'RUNNING' || echo 'STOPPED'")
                    pid_status = pid_check.read().decode().strip()
                    pid_running = (pid_status == "RUNNING")

                try:
                    attr = sftp.stat(remote_wav)
                    # Si el proceso terminó o el log indica éxito y el archivo existe
                    _, check_finish, _ = ssh.exec_command(f"grep -q '¡Podcast generado con éxito!' '{remote_log_path}' && echo 'DONE' || echo 'WAIT'")
                    status_done = check_finish.read().decode().strip()

                    if (status_done == "DONE" or not pid_running) and attr.st_size > 1000:
                        file_ready = True
                        console.print(f"🎉 [bold green]¡Síntesis completada en {elapsed:.1f}s! Tamaño: {attr.st_size / (1024*1024):.2f} MB[/bold green]")
                        break
                except IOError:
                    if not pid_running and elapsed > 20:
                        # Si el proceso murió y no hay archivo, error
                        raise RuntimeError(f"El proceso remoto (PID {remote_pid}) terminó sin generar el archivo '{remote_wav}'. Revisar '{remote_log_path}'.")

            # 5. Descargar archivo a destino local
            local_wav_dest.parent.mkdir(parents=True, exist_ok=True)
            console.print(f"📥 [cyan]Descargando audio a '{local_wav_dest}'...[/cyan]")
            sftp.get(remote_wav, str(local_wav_dest))
            console.print(f"✅ [bold green]Audio descargado correctamente: {local_wav_dest} ({local_wav_dest.stat().st_size / (1024*1024):.2f} MB)[/bold green]")

            # 6. Limpieza remota
            console.print(f"🧹 [dim]Limpiando archivo remoto '{remote_wav}'...[/dim]")
            try:
                sftp.remove(remote_wav)
            except Exception:
                pass

            return local_wav_dest

        finally:
            sftp.close()
            ssh.close()


if __name__ == "__main__":
    import argparse
    import json
    import tempfile

    parser = argparse.ArgumentParser(description="Cliente de síntesis remota con clonvoz en .248.")
    parser.add_argument("--test-connection", action="store_true", help="Solo verifica conexión SSH")
    parser.add_argument("--test-phrase", type=str, default=None, help="Sintetiza una frase de prueba corta")
    parser.add_argument("--guion", type=str, default=None, help="Ruta de un archivo de guión a sintetizar")
    parser.add_argument("--output", type=str, default="output/audio/raw_voice.wav", help="Destino del audio WAV")
    args = parser.parse_args()

    cfg_path = Path("config.json")
    tts_cfg = {}
    if cfg_path.is_file():
        with open(cfg_path, "r", encoding="utf-8") as f:
            tts_cfg = json.load(f).get("tts", {})

    client = RemoteTTSClient(
        ssh_host=tts_cfg.get("ssh_host", "170.210.80.248"),
        ssh_port=tts_cfg.get("ssh_port", 9022),
        ssh_user=tts_cfg.get("ssh_user", "golem"),
        remote_dir=tts_cfg.get("remote_dir", "/home/golem/ramiro/clonvoz"),
    )

    if args.test_connection:
        client.test_connection()
    elif args.test_phrase:
        console.print(f"🧪 [cyan]Probando síntesis con frase corta: '{args.test_phrase}'[/cyan]")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(args.test_phrase)
            tmp_path = Path(tmp.name)
        try:
            dest = client.synthesize(tmp_path, Path(args.output), custom_output_name="test_phrase.wav")
            console.print(f"🏆 [bold green]Audio de prueba generado en:[/bold green] {dest}")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
    elif args.guion:
        dest = client.synthesize(Path(args.guion), Path(args.output))
        console.print(f"🏆 [bold green]Audio de guión generado en:[/bold green] {dest}")
    else:
        client.test_connection()
