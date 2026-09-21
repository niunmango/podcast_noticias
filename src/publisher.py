#!/usr/bin/env python3
"""
Publisher module.
Publishes podcast episodes to GitHub Releases and maintains the unified public
RSS feed (feed.xml) on GitHub Pages for Spotify and Apple Podcasts (Podcast de Linux al Sur).
"""

import os
import re
import json
import email.utils
import datetime
import subprocess
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any

from rich.console import Console

console = Console()

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("content", CONTENT_NS)


def load_env(env_path: Path) -> Dict[str, str]:
    """Carga variables desde archivo .env."""
    env = {}
    if not env_path.is_file():
        return env
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip("'\"")
    return env


class GitHubUploader:
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo
        self.api_base = f"https://api.github.com/repos/{repo}"

    def _headers(self, content_type: str = "application/json") -> Dict[str, str]:
        return {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": content_type,
            "User-Agent": "PodcastPipeline/1.0",
        }

    def get_or_create_release(self, tag: str, title: str, body: str) -> dict:
        """Obtiene una release existente por tag o crea una nueva."""
        url = f"{self.api_base}/releases/tags/{tag}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req) as resp:
                console.print(f"ℹ️  [cyan]Release ya existe para el tag '{tag}'.[/cyan]")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise RuntimeError(f"Error consultando release en GitHub: {e.read().decode('utf-8')}")

        # Crear nueva release
        console.print(f"🚀 [cyan]Creando Release '{tag}' en {self.repo}...[/cyan]")
        create_url = f"{self.api_base}/releases"
        payload = {
            "tag_name": tag,
            "name": title,
            "body": body,
            "draft": False,
            "prerelease": False,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(create_url, data=data, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req) as resp:
            release = json.loads(resp.read().decode("utf-8"))
            console.print(f"✅ [bold green]Release creada exitosamente:[/bold green] {release.get('html_url')}")
            return release

    def upload_asset(self, release: dict, file_path: Path, content_type: str) -> str:
        """Sube un archivo como asset al Release si no existe."""
        file_name = file_path.name
        existing_assets = release.get("assets", [])
        for asset in existing_assets:
            if asset.get("name") == file_name:
                url = asset.get("browser_download_url")
                console.print(f"ℹ️  [dim]Asset '{file_name}' ya existe en el release:[/dim] {url}")
                return url

        upload_url_template = release.get("upload_url", "")
        upload_url = upload_url_template.split("{")[0] + f"?name={file_name}"

        file_size = file_path.stat().st_size
        console.print(f"📤 [cyan]Subiendo '{file_name}' ({file_size / (1024*1024):.2f} MB)...[/cyan]")

        with open(file_path, "rb") as f:
            data = f.read()

        headers = self._headers(content_type=content_type)
        headers["Content-Length"] = str(file_size)

        req = urllib.request.Request(upload_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            asset_resp = json.loads(resp.read().decode("utf-8"))
            download_url = asset_resp.get("browser_download_url")
            console.print(f"✅ [bold green]Asset subido:[/bold green] {download_url}")
            return download_url


def generate_or_update_feed(
    feed_path: Path,
    channel_info: dict,
    new_episode: dict,
) -> Path:
    """
    Actualiza el archivo RSS feed.xml compatible con Spotify for Podcasters
    y Apple Podcasts para el feed unificado de Linux al Sur.
    Si ya existe feed.xml, preserva todos los metadatos del canal y los
    episodios existentes, anteponiendo el nuevo episodio al inicio.
    """
    if feed_path.is_file():
        try:
            tree = ET.parse(str(feed_path))
            root = tree.getroot()
            channel = root.find("channel")
            if channel is not None:
                new_guid = new_episode.get("guid", new_episode["audio_url"])

                # Eliminar versión previa del mismo episodio si se está re-publicando
                for old_item in channel.findall("item"):
                    guid_elem = old_item.find("guid")
                    if guid_elem is not None and guid_elem.text == new_guid:
                        channel.remove(old_item)

                # Construir el nuevo elemento item
                item = ET.Element("item")
                ET.SubElement(item, "title").text = new_episode["title"]
                ET.SubElement(item, f"{{{ITUNES_NS}}}title").text = new_episode.get("itunes_title", new_episode["title"])
                ET.SubElement(item, f"{{{ITUNES_NS}}}episodeType").text = "full"

                desc_text = new_episode.get("description", "")
                if new_episode.get("hashtags"):
                    desc_text = f"{desc_text}\n\n{new_episode['hashtags']}"
                ET.SubElement(item, "description").text = desc_text
                ET.SubElement(item, f"{{{ITUNES_NS}}}summary").text = new_episode.get("description", "")

                ET.SubElement(item, "enclosure", {
                    "url": new_episode["audio_url"],
                    "length": str(new_episode["audio_bytes"]),
                    "type": "audio/mpeg",
                })

                guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
                guid.text = new_guid

                pub_date = new_episode.get("pub_date") or email.utils.formatdate(usegmt=True)
                ET.SubElement(item, "pubDate").text = pub_date

                if new_episode.get("duration"):
                    ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = str(new_episode["duration"])

                if new_episode.get("image_url"):
                    ET.SubElement(item, f"{{{ITUNES_NS}}}image", {"href": new_episode["image_url"]})

                ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "false"

                # Insertar al inicio de la lista de episodios
                items = channel.findall("item")
                if items:
                    first_idx = list(channel).index(items[0])
                    channel.insert(first_idx, item)
                else:
                    channel.append(item)

                ET.indent(root, space="  ", level=0)
                tree.write(str(feed_path), encoding="utf-8", xml_declaration=True)
                total_eps = len(channel.findall("item"))
                console.print(f"✅ [bold green]Feed RSS unificado actualizado ({total_eps} episodios en total):[/bold green] {feed_path}")
                return feed_path
        except Exception as e:
            console.print(f"⚠️  [yellow]No se pudo actualizar directamente feed existente ({e}), creando nuevo.[/yellow]")

    # Fallback si no existía el feed
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = channel_info["title"]
    ET.SubElement(channel, "link").text = channel_info["link"]
    ET.SubElement(channel, "language").text = channel_info.get("language", "es")
    ET.SubElement(channel, "copyright").text = f"© {datetime.date.today().year} {channel_info['author']}"

    ET.SubElement(channel, f"{{{ITUNES_NS}}}author").text = channel_info["author"]
    ET.SubElement(channel, "description").text = channel_info["description"]
    ET.SubElement(channel, f"{{{ITUNES_NS}}}summary").text = channel_info["description"]
    ET.SubElement(channel, f"{{{ITUNES_NS}}}type").text = "episodic"

    owner = ET.SubElement(channel, f"{{{ITUNES_NS}}}owner")
    ET.SubElement(owner, f"{{{ITUNES_NS}}}name").text = channel_info["author"]
    ET.SubElement(owner, f"{{{ITUNES_NS}}}email").text = channel_info["email"]

    if channel_info.get("image"):
        ET.SubElement(channel, f"{{{ITUNES_NS}}}image", {"href": channel_info["image"]})
        img_elem = ET.SubElement(channel, "image")
        ET.SubElement(img_elem, "url").text = channel_info["image"]
        ET.SubElement(img_elem, "title").text = channel_info["title"]
        ET.SubElement(img_elem, "link").text = channel_info["link"]

    category = ET.SubElement(channel, f"{{{ITUNES_NS}}}category", {"text": "Technology"})
    ET.SubElement(category, f"{{{ITUNES_NS}}}category", {"text": "Tech News"})
    ET.SubElement(channel, f"{{{ITUNES_NS}}}explicit").text = "false"

    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = new_episode["title"]
    ET.SubElement(item, f"{{{ITUNES_NS}}}title").text = new_episode.get("itunes_title", new_episode["title"])
    ET.SubElement(item, f"{{{ITUNES_NS}}}episodeType").text = "full"

    desc_text = new_episode.get("description", "")
    if new_episode.get("hashtags"):
        desc_text = f"{desc_text}\n\n{new_episode['hashtags']}"
    ET.SubElement(item, "description").text = desc_text
    ET.SubElement(item, f"{{{ITUNES_NS}}}summary").text = new_episode.get("description", "")

    ET.SubElement(item, "enclosure", {
        "url": new_episode["audio_url"],
        "length": str(new_episode["audio_bytes"]),
        "type": "audio/mpeg",
    })

    guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid.text = new_episode.get("guid", new_episode["audio_url"])

    pub_date = new_episode.get("pub_date") or email.utils.formatdate(usegmt=True)
    ET.SubElement(item, "pubDate").text = pub_date

    if new_episode.get("duration"):
        ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = str(new_episode["duration"])

    if new_episode.get("image_url"):
        ET.SubElement(item, f"{{{ITUNES_NS}}}image", {"href": new_episode["image_url"]})

    ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "false"

    ET.indent(rss, space="  ", level=0)
    tree = ET.ElementTree(rss)
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(feed_path), encoding="utf-8", xml_declaration=True)
    console.print(f"✅ [bold green]Feed RSS creado:[/bold green] {feed_path}")
    return feed_path


def git_commit_and_push(repo_dir: Path, commit_msg: str):
    """Realiza git add feed.xml, commit y push en el repositorio de GitHub Pages."""
    console.print(f"🔄 [cyan]Sincronizando feed en GitHub Pages ({repo_dir.name})...[/cyan]")
    # Agregar únicamente feed.xml (la portada general del podcast no se sobreescribe)
    subprocess.run(["git", "-C", str(repo_dir), "add", "feed.xml"], check=True)

    diff_check = subprocess.run(["git", "-C", str(repo_dir), "diff", "--staged", "--quiet"])
    if diff_check.returncode == 0:
        console.print("ℹ️  [dim]No hay cambios pendientes en feed.xml para commitear.[/dim]")
        return

    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", commit_msg], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "push", "origin", "main"], check=True)
    console.print(f"🚀 [bold green]Feed RSS unificado subido a GitHub Pages exitosamente.[/bold green]")


class Publisher:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.env = load_env(base_dir / ".env")
        self.token = self.env.get("GITHUB_TOKEN", "")
        self.repo = self.env.get("GITHUB_REPO", "niunmango/podcast_linux_al_sur")
        self.pages_dir = Path(self.env.get("PAGES_DIR", "/home/ramiro/.hermes/scripts/podcast/podcast_linux_al_sur"))
        self.feed_url = self.env.get("FEED_URL", f"https://{self.repo.split('/')[0]}.github.io/{self.repo.split('/')[1]}/feed.xml")
        self.author = self.env.get("AUTHOR_NAME", "Ramiro")
        self.email_addr = self.env.get("AUTHOR_EMAIL", "ramiro.gp@gmail.com")
        self.podcast_title = self.env.get("PODCAST_TITLE", "Podcast de Linux al Sur")
        self.podcast_desc = self.env.get(
            "PODCAST_DESC",
            "Podcast sobre Linux, software libre y desarrollo web",
        )

    def publish_episode(
        self,
        mp3_path: Path,
        cover_path: Path,
        title: str,
        description: str,
        hashtags: str,
        duration_seconds: int,
        tag: Optional[str] = None,
        pub_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Publica el episodio de noticias dentro del feed unificado de Podcast de Linux al Sur:
        1. Crea Release en GitHub (niunmango/podcast_linux_al_sur).
        2. Sube el .mp3 y la portada como assets.
        3. Actualiza feed.xml en el repositorio local de Pages (podcast_linux_al_sur).
        4. Hace git commit y push a GitHub Pages.
        """
        if not self.token:
            raise ValueError("No se encontró GITHUB_TOKEN configurado en .env.")

        if not mp3_path.is_file():
            raise FileNotFoundError(f"Archivo de audio MP3 no encontrado: {mp3_path}")

        date_tag = tag or datetime.datetime.now(datetime.timezone.utc).strftime("EP%Y%m%d")
        console.print(f"\n🚀 [bold cyan]Iniciando publicación oficial para [{date_tag}] en {self.repo}...[/bold cyan]")

        uploader = GitHubUploader(self.token, self.repo)

        # 1. Crear Release
        release_body = f"{description}\n\n{hashtags}\n\n🎙️ Edición semanal de noticias de Podcast de Linux al Sur."
        release = uploader.get_or_create_release(
            tag=date_tag,
            title=f"Noticias: {title}",
            body=release_body,
        )

        # 2. Subir Assets
        mp3_url = uploader.upload_asset(release, mp3_path, "audio/mpeg")
        image_url = None
        if cover_path and cover_path.is_file():
            image_url = uploader.upload_asset(release, cover_path, "image/jpeg")

        channel_image_url = f"https://raw.githubusercontent.com/{self.repo}/main/podcast_cover.jpg"

        # 3. Datos del episodio para el Feed unificado
        episode_data = {
            "title": f"Noticias: {title}",
            "itunes_title": f"Noticias: {title}",
            "description": description,
            "hashtags": hashtags,
            "audio_url": mp3_url,
            "audio_bytes": mp3_path.stat().st_size,
            "duration": duration_seconds,
            "guid": f"https://github.com/{self.repo}/releases/tag/{date_tag}",
            "pub_date": pub_date or email.utils.formatdate(usegmt=True),
            "image_url": image_url or channel_image_url,
        }

        channel_info = {
            "title": self.podcast_title,
            "link": f"https://github.com/{self.repo}",
            "language": "es",
            "author": self.author,
            "email": self.email_addr,
            "description": self.podcast_desc,
            "image": channel_image_url,
        }

        # 3. Traer cambios upstream antes de modificar feed.xml
        subprocess.run(["git", "-C", str(self.pages_dir), "pull", "--rebase", "origin", "main"], check=False)

        feed_file = self.pages_dir / "feed.xml"
        generate_or_update_feed(feed_file, channel_info, episode_data)

        # 4. Commit y push del feed
        git_commit_and_push(self.pages_dir, f"Publicar Noticias {date_tag}: {title}")

        console.print(
            f"\n🎉 [bold green]¡Episodio de noticias publicado exitosamente en el feed unificado![/bold green]\n"
            f"📡 [cyan]Feed RSS Oficial (Linux al Sur):[/cyan] {self.feed_url}\n"
            f"📦 [cyan]Release en GitHub:[/cyan] https://github.com/{self.repo}/releases/tag/{date_tag}\n"
        )

        return {
            "tag": date_tag,
            "mp3_url": mp3_url,
            "image_url": image_url,
            "feed_url": self.feed_url,
        }
