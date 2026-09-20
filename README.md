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
    • Estilo: Prosa corrida rioplatense profesional con voseo técnico (sin "che")
    • Extensión: 1000-1150 palabras (~6 minutos de locución fluida)
    • 4 Líneas temáticas obligatorias:
        1. Kernel y bajo nivel (Linux, subsistemas, drivers)
        2. Desktop / Distros (Ubuntu, Fedora, GNOME, KDE, Wayland)
        3. Aplicaciones libres y herramientas (utilidades, open source)
        4. Gaming en Linux (Steam, Proton, Wine, Vulkan, DXVK)
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
[ Episodio Final: output/audio/podcast_YYYYMMDD_HHMMSS.mp3 (~6 min) ]
```

---

## 📁 Estructura del Repositorio

```text
podcast_noticias/
├── config.json                 # Configuración central (endpoints, feeds RSS, rutas, audio)
├── main.py                     # Orquestador principal y CLI
├── requirements.txt            # Dependencias Python del pipeline
├── assets/
│   └── background.mp3          # Pista de música de fondo para la mezcla final
├── prompts/
│   └── system_prompt.txt       # Prompt del sistema para el LLM (tono rioplatense, reglas TTS)
├── src/
│   ├── __init__.py
│   ├── rss_collector.py        # Ingesta, parseo y filtrado de noticias en 4 líneas
│   ├── script_generator.py     # Redactor de guión (~6 min, sin "che") con LLM local
│   ├── copywriter.py           # Generador de título, descripción y hashtags SEO
│   ├── cover_generator.py      # Generador de portadas oficiales (1400x1400px JPEG)
│   ├── remote_tts_client.py    # Cliente SSH/SFTP para orquestar ClonVoz en servidor .248
│   ├── audio_processor.py      # Mezcla de música y normalización EBU R128 (-16 LUFS)
│   └── publisher.py            # Publicador a GitHub Releases y generador de feed.xml
├── systemd/
│   ├── podcast-noticias.service# Servicio systemd de usuario para ejecución desatendida
│   └── podcast-noticias.timer  # Temporizador systemd (Sábados 03:00 ART)
├── output/
│   ├── scripts/                # Guiones generados (.txt y .md)
│   ├── audio/                  # Archivos de audio temporales (WAV) y masterizados (MP3)
│   ├── covers/                 # Portadas generadas para cada episodio (.jpg)
│   └── metadata/               # Título, descripción y hashtags por episodio (.txt/.json)
├── feed.xml                    # Feed RSS público para Spotify / Apple Podcasts
├── podcast_cover.jpg           # Portada principal del canal en GitHub Pages
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

- **`llm`**: Endpoint OpenAI compatible, modelo, temperatura y rango de palabras objetivo (1000-1150 palabras para ~6 minutos de locución).
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
- **`audio`**: Filtro Loudnorm (`loudnorm=I=-16:TP=-1.0:LRA=11`), tasa de muestreo (`44100`), bitrate (`192k`), ruta de música de fondo (`background_music: "assets/background.mp3"`) y volumen de fondo (`background_volume: 0.08`).

---

## 🎙️ Uso del CLI (`main.py`)

### 1. Ejecución Completa (End-to-End con Publicación)
Descarga las noticias de los últimos 7 días, redacta el guión en prosa rioplatense (4 líneas temáticas), genera portada y metadatos SEO, sintetiza la voz en el servidor `.248`, masteriza el audio con música de fondo, sube el release a GitHub y actualiza el feed RSS en GitHub Pages:

```bash
python3 main.py --publish
```

### 2. Ejecución Local (Sin Publicar)
Genera el guión, portada, metadatos y audio MP3 masterizado en la máquina local, sin subir a GitHub:

```bash
python3 main.py
```

### 3. Modo Dry-Run (Solo Guión, Portada y Metadatos)
Recopila noticias, redacta el guión, genera la portada 1400x1400px y la descripción SEO en `output/`, sin invocar la GPU remota ni sintetizar audio:

```bash
python3 main.py --dry-run
```

### 4. Modo Voice-Only (Sintetizar Guión Existente)
Permite sintetizar y masterizar un guión ya generado o editado manualmente:

```bash
python3 main.py --voice-only output/scripts/podcast_20260920_183141.txt
```

### 5. Prueba Rápida de Conexión TTS
Verifica la conectividad SSH y la disponibilidad de la carpeta `clonvoz` en el servidor `.248`:

```bash
python3 main.py --test-tts
```

### 6. Parámetros de Ventana Temporal
Simular una fecha de corte o variar los días de recopilación:

```bash
python3 main.py --date-offset 2026-09-15 --days 5
```

### 7. Control de Música de Fondo
Por defecto se utiliza `assets/background.mp3` al 8% de volumen. Es posible personalizar la pista o desactivarla:

```bash
# Desactivar música de fondo (solo voz)
python3 main.py --no-music

# Usar pista personalizada de fondo
python3 main.py --bg-music /ruta/a/mi_musica.mp3
```

---

## 🚀 Publicación y Distribución Oficial

El módulo `src/publisher.py`, en conjunto con `src/copywriter.py` y `src/cover_generator.py`, automatiza la distribución del podcast:

1. **Copywriting con LLM (`src/copywriter.py`)**: Analiza el guión final y genera:
   - Título atractivo y conciso para el episodio.
   - Descripción completa (<200 palabras) cubriendo las 4 líneas temáticas.
   - Hashtags optimizados para redes y agregadores.
2. **Generación de Portada (`src/cover_generator.py`)**: Renderiza con Pillow una carátula oficial de 1400x1400 px a 300 DPI con gradiente tecnológico, isotipo, badge de edición semanal y título tipográfico con contraste optimizado.
3. **GitHub Releases (`src/publisher.py`)**: Sube el binario MP3 masterizado y la portada JPEG a una Release oficial de GitHub bajo el tag `EPYYYYMMDD`.
4. **Feed RSS 2.0 & GitHub Pages**:
   - Genera/actualiza `feed.xml` con soporte completo para estándares de Apple Podcasts y Spotify (`itunes:duration`, `itunes:image`, `enclosure` apuntando a GitHub Releases, `guid`, etc.).
   - Hace commit y push automático a la rama `main` del repositorio, sirviendo el feed a través de GitHub Pages.
   - **URL Pública del Feed RSS**: `https://niunmango.github.io/podcast_noticias/feed.xml`

---

## 🕒 Automatización (Systemd User Timer)

El pipeline está configurado para ejecutarse y publicarse desatendidamente todos los **sábados a las 03:00 AM (hora de Argentina = 06:00 UTC)** utilizando un temporizador de usuario de systemd.

Las unidades residen en `systemd/` del repositorio y se instalan en `~/.config/systemd/user/`:

- `podcast-noticias.service`: Invoca `main.py --publish` utilizando el entorno virtual de Python.
- `podcast-noticias.timer`: Programa la ejecución con `OnCalendar=Sat *-*-* 03:00:00 America/Argentina/Buenos_Aires` y `Persistent=true`.

### Gestión del Timer

```bash
# Ver estado del temporizador y próxima ejecución
systemctl --user status podcast-noticias.timer

# Listar temporizadores de usuario activos
systemctl --user list-timers podcast-noticias.timer

# Ejecutar manualmente una vez para probar el servicio desatendido
systemctl --user start podcast-noticias.service

# Inspeccionar logs de ejecución en tiempo real
journalctl --user -u podcast-noticias.service -f
```

---

## 📊 Estándares de Audio

- **Loudness Integrado**: `-16.0 LUFS` (estándar recomendado para podcasts).
- **True Peak Máximo**: `-1.0 dBTP` (evita distorsión y clipping inter-sample en codificadores con pérdida).
- **Loudness Range (LRA)**: `11 LU` (dinámica natural de voz hablada).
- **Formato Final**: MP3 Stereo/Joint-Stereo @ 44.1 kHz, 192 kbps CBR.
