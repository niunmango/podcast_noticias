# Pipeline Automatizado de Podcast: Linux & Open Source

Pipeline modular en Python para la generación automática de episodios semanales de podcast sobre el ecosistema Linux, kernel y software libre. Integra ingesta y filtrado de feeds RSS, generación de guiones mediante LLM local con estilo técnico rioplatense, síntesis de voz neuronal en GPU remota y post-procesamiento/masterización de audio según estándares profesionales de podcasting (EBU R128).

---

## 🏗️ Arquitectura del Sistema

```text
[ RSS Feeds (Últimos 7 días) ]
              │
              ▼ (feedparser + python-dateutil)
[ 1. Ingesta y Filtrado RSS ]
              │ (Texto normalizado de noticias)
              ▼
[ 2. Generador de Guión (LLM Local) ]
    • Host: http://192.168.1.200:20128/v1
    • Modelo: hermes-rotator (GPT-OSS 120B)
    • Estilo: Prosa corrida rioplatense (750-850 palabras, ~5-6 min)
    • Salida: Texto limpio sin marcas de producción ([MÚSICA], etc.)
              │
              ▼ (Guión .txt y .md)
[ 3. Síntesis de Voz Remota (.248) ]
    • Host: golem@170.210.80.248:9022
    • Motor: ClonVoz 2.0 (VoxCPM2 2B @ 48 kHz en NVIDIA RTX 5060 Ti)
    • Comunicación: SSH / SFTP (upload guión, spawn detached, poll log, download WAV)
              │
              ▼ (Audio WAV 48 kHz mono)
[ 4. Masterización y Normalización (FFmpeg) ]
    • Filtro: EBU R128 (-16 LUFS, True Peak -1.0 dB, LRA 11)
    • Salida: MP3 44.1 kHz, 192 kbps con metadatos ID3
              │
              ▼
[ Episodio Final: output/audio/podcast_YYYYMMDD_HHMMSS.mp3 ]
```

---

## 📁 Estructura del Repositorio

```text
podcast_noticias/
├── config.json                 # Configuración central (endpoints, feeds RSS, rutas, audio)
├── main.py                     # Orquestador principal y CLI
├── requirements.txt            # Dependencias Python del pipeline
├── prompts/
│   └── system_prompt.txt       # Prompt del sistema para el LLM (tono rioplatense, reglas TTS)
├── src/
│   ├── __init__.py
│   ├── rss_collector.py        # Ingesta, parseo y filtrado temporal de feeds RSS
│   ├── script_generator.py     # Cliente OpenAI/LLM, formateo y limpieza de guión
│   ├── remote_tts_client.py    # Cliente SSH/SFTP para orquestar ClonVoz en servidor .248
│   └── audio_processor.py      # Normalización EBU R128 y masterización con FFmpeg
├── output/
│   ├── scripts/                # Guiones generados (.txt y .md)
│   └── audio/                  # Archivos de audio temporales (WAV) y masterizados (MP3)
└── README.md
```

---

## 🚀 Requisitos e Instalación

### Requisitos del Sistema
- **Sistema Operativo**: Ubuntu Server 24.04 LTS (o compatible).
- **Python**: 3.10 o superior.
- **FFmpeg**: Instalado en el sistema local (`sudo apt install ffmpeg`).
- **Acceso SSH**: Clave SSH configurada para acceder a `golem@170.210.80.248:9022` sin contraseña interactiva.
- **Red LAN**: Acceso al endpoint LLM (`http://192.168.1.200:20128/v1`).

### Instalación de dependencias

```bash
# Clonar o entrar al directorio del proyecto
cd /home/ramiro/podcast_noticias

# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar librerías
pip install -r requirements.txt
```

---

## ⚙️ Configuración (`config.json`)

El archivo `config.json` centraliza todos los parámetros del pipeline:

- **`llm`**: Endpoint OpenAI compatible, modelo, temperatura y rango de palabras objetivo (750-850).
- **`tts`**: Parámetros de conexión SSH al servidor `.248`, ruta remota de `clonvoz`, script ejecutor (`generar.sh`) y timeouts.
- **`rss`**: Ventana de días (default: 7) y lista de portales monitoreados:
  - Phoronix
  - LWN.net
  - OMG! Ubuntu
  - GamingOnLinux
  - 9to5Linux
  - It's FOSS
  - Linux Today
  - MuyLinux
- **`audio`**: Filtro Loudnorm (`loudnorm=I=-16:TP=-1.0:LRA=11`), tasa de muestreo (`44100`) y bitrate (`192k`).

---

## 🎙️ Uso del CLI (`main.py`)

### 1. Ejecución Completa (End-to-End)
Descarga las noticias de los últimos 7 días, genera el guión en prosa rioplatense, lo sintetiza en el servidor `.248` y genera el MP3 normalizado:

```bash
python3 main.py
```

### 2. Modo Dry-Run (Solo Guión)
Recopila noticias y genera el guión en `output/scripts/`, sin invocar la GPU remota ni sintetizar audio:

```bash
python3 main.py --dry-run
```

### 3. Modo Voice-Only (Sintetizar Guión Existente)
Permite sintetizar y masterizar un guión ya generado o editado manualmente:

```bash
python3 main.py --voice-only output/scripts/podcast_20260920_183141.txt
```

### 4. Prueba Rápida de Conexión TTS
Verifica la conectividad SSH y la disponibilidad de la carpeta `clonvoz` en el servidor `.248`:

```bash
python3 main.py --test-tts
```

### 5. Parámetros de Ventana Temporal
Simular una fecha de corte o variar los días de recopilación:

```bash
python3 main.py --date-offset 2026-09-15 --days 5
```

---

## 🕒 Automatización (Programación Semanal)

Para ejecutar el podcast de manera automática todos los domingos a las 20:00:

### Opción A: Crontab
```bash
crontab -e
```
Añadir la siguiente línea:
```cron
0 20 * * 0 cd /home/ramiro/podcast_noticias && ./venv/bin/python3 main.py >> output/cron.log 2>&1
```

### Opción B: Systemd Timer
Crear servicio `/etc/systemd/system/podcast-pipeline.service`:
```ini
[Unit]
Description=Generador Semanal de Podcast Linux & Open Source
After=network.target

[Service]
Type=oneshot
User=ramiro
WorkingDirectory=/home/ramiro/podcast_noticias
ExecStart=/home/ramiro/podcast_noticias/venv/bin/python3 main.py
StandardOutput=journal
StandardError=journal
```

Crear timer `/etc/systemd/system/podcast-pipeline.timer`:
```ini
[Unit]
Description=Ejecutar pipeline de podcast semanalmente

[Timer]
OnCalendar=Sun *-*-* 20:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Habilitar el timer:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now podcast-pipeline.timer
```

---

## 📊 Estándares de Audio

- **Loudness Integrado**: `-16.0 LUFS` (estándar recomendado para podcasts).
- **True Peak Máximo**: `-1.0 dBTP` (evita distorsión y clipping inter-sample en codificadores con pérdida).
- **Loudness Range (LRA)**: `11 LU` (dinámica natural de voz hablada).
- **Formato Final**: MP3 Stereo/Joint-Stereo @ 44.1 kHz, 192 kbps CBR.
