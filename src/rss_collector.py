#!/usr/bin/env python3
"""
RSS Collector module.
Fetches and filters Linux & Open Source news from the last N days (default 7 days).
"""

import re
import html
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from pathlib import Path

import feedparser
import requests
from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

logger = logging.getLogger("podcast.rss_collector")
console = Console()

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 PodcastBot/1.0"
)

DEFAULT_FEEDS = [
    {"name": "Phoronix", "url": "https://www.phoronix.com/rss.php"},
    {"name": "LWN", "url": "https://lwn.net/headlines/newrss"},
    {"name": "OMG! Ubuntu", "url": "https://www.omgubuntu.co.uk/feed"},
    {"name": "GamingOnLinux", "url": "https://www.gamingonlinux.com/article_rss.php"},
    {"name": "9to5Linux", "url": "https://9to5linux.com/feed"},
    {"name": "It's FOSS", "url": "https://itsfoss.com/feed/"},
    {"name": "Linux Today", "url": "https://www.linuxtoday.com/feed/"},
    {"name": "MuyLinux", "url": "https://www.muylinux.com/feed/"},
]


class NewsItem(BaseModel):
    title: str
    source: str
    url: str
    published_at: datetime
    summary: str

    def to_snippet(self) -> str:
        date_str = self.published_at.strftime("%Y-%m-%d")
        return f"[{self.source} - {date_str}] {self.title}\nResumen: {self.summary}\nEnlace: {self.url}"


class NewsBatch(BaseModel):
    collection_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reference_date: datetime
    days_limit: int
    items: List[NewsItem]

    def to_formatted_prompt_text(self, max_items: int = 25) -> str:
        """Formatea las noticias candidatas para inyectarlas al LLM."""
        if not self.items:
            return "No se encontraron noticias recientes en los feeds consultados."

        selected = self.items[:max_items]
        lines = [
            f"Noticias recopiladas de los últimos {self.days_limit} días "
            f"(Total seleccionadas: {len(selected)}):",
            "---",
        ]
        for i, item in enumerate(selected, 1):
            lines.append(f"{i}. {item.to_snippet()}")
            lines.append("")
        return "\n".join(lines)


def clean_html_snippet(raw_html: str, max_chars: int = 350) -> str:
    """Remueve etiquetas HTML y recorta el texto para resumen limpio."""
    if not raw_html:
        return ""
    # Quitar scripts y estilos
    text = re.sub(r"<(script|style).*?>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    # Quitar cualquier etiqueta HTML
    text = re.sub(r"<[^>]+>", " ", text)
    # Desescapar entidades HTML (&amp;, &quot;, etc.)
    text = html.unescape(text)
    # Normalizar espacios
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def parse_entry_datetime(entry: Dict[str, Any]) -> Optional[datetime]:
    """Extrae la fecha de publicación normalizada a UTC."""
    # Intentar parsed tuple de feedparser
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed_time = entry.get(key)
        if parsed_time:
            try:
                dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
                return dt
            except Exception:
                pass

    # Intentar texto crudo con dateutil
    for key in ("published", "updated", "pubDate", "date"):
        raw_date = entry.get(key)
        if raw_date and isinstance(raw_date, str):
            try:
                dt = date_parser.parse(raw_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt
            except Exception:
                pass

    return None


def fetch_single_feed(source_name: str, feed_url: str, timeout: int = 15) -> List[Dict[str, Any]]:
    """Descarga y parsea un feed RSS con headers seguros y timeout."""
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    try:
        response = requests.get(feed_url, headers=headers, timeout=timeout)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        return feed.entries
    except Exception as exc:
        logger.warning("Fallo al descargar %s vía requests (%s). Intentando feedparser directo...", source_name, exc)
        try:
            feed = feedparser.parse(feed_url, agent=DEFAULT_USER_AGENT)
            return feed.entries
        except Exception as direct_exc:
            logger.error("Error definitivo al obtener feed de %s: %s", source_name, direct_exc)
            return []


def collect_news(
    feeds: Optional[List[Dict[str, str]]] = None,
    days_limit: int = 7,
    reference_date: Optional[datetime] = None,
    max_items_per_feed: int = 10,
) -> NewsBatch:
    """
    Recopila noticias de los feeds provistos dentro del límite de días especificado.
    """
    if feeds is None:
        feeds = DEFAULT_FEEDS

    if reference_date is None:
        reference_date = datetime.now(timezone.utc)
    elif reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=timezone.utc)

    cutoff_date = reference_date - timedelta(days=days_limit)
    collected_items: List[NewsItem] = []
    seen_urls = set()
    seen_titles = set()

    for feed_info in feeds:
        name = feed_info.get("name", "Unknown")
        url = feed_info.get("url", "")
        if not url:
            continue

        entries = fetch_single_feed(name, url)
        feed_items_count = 0

        for entry in entries:
            pub_date = parse_entry_datetime(entry)
            # Si no tiene fecha detectable, se asume reciente sólo si está en el tope
            if pub_date is None:
                pub_date = reference_date

            # Filtrar por antigüedad
            if pub_date < cutoff_date:
                continue

            title = clean_html_snippet(entry.get("title", "Sin título"), max_chars=180)
            entry_url = entry.get("link", "").strip()

            norm_title = re.sub(r"\W+", "", title.lower())
            if not entry_url or entry_url in seen_urls or norm_title in seen_titles:
                continue

            seen_urls.add(entry_url)
            seen_titles.add(norm_title)

            # Extraer resumen
            raw_summary = (
                entry.get("summary")
                or entry.get("description")
                or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
                or ""
            )
            summary = clean_html_snippet(raw_summary, max_chars=400)
            if not summary:
                summary = title

            collected_items.append(
                NewsItem(
                    title=title,
                    source=name,
                    url=entry_url,
                    published_at=pub_date,
                    summary=summary,
                )
            )
            feed_items_count += 1
            if feed_items_count >= max_items_per_feed:
                break

    # Ordenar por fecha descendente (más recientes primero)
    collected_items.sort(key=lambda x: x.published_at, reverse=True)

    return NewsBatch(
        reference_date=reference_date,
        days_limit=days_limit,
        items=collected_items,
    )


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Recopilador y filtro RSS de noticias de Linux y Open Source.")
    parser.add_argument("--days", type=int, default=7, help="Días máximos de antigüedad (por defecto: 7)")
    parser.add_argument("--date-offset", type=str, default=None, help="Fecha de corte simulada (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, default=None, help="Ruta de archivo JSON para guardar las noticias")
    args = parser.parse_args()

    ref_date = None
    if args.date_offset:
        try:
            ref_date = datetime.strptime(args.date_offset, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            console.print(f"[bold red]Error:[/bold red] Formato inválido de fecha '{args.date_offset}', usar YYYY-MM-DD.")
            exit(1)

    console.print(f"[bold cyan]🔍 Recolectando noticias de los últimos {args.days} días...[/bold cyan]")
    batch = collect_news(days_limit=args.days, reference_date=ref_date)

    table = Table(title=f"Noticias Recopiladas ({len(batch.items)} encontradas)")
    table.add_column("Fuente", style="cyan", width=16)
    table.add_column("Fecha (UTC)", style="magenta", width=12)
    table.add_column("Título", style="green")

    for it in batch.items[:15]:
        table.add_row(it.source, it.published_at.strftime("%Y-%m-%d"), it.title)

    console.print(table)
    if len(batch.items) > 15:
        console.print(f"[yellow]... y {len(batch.items) - 15} noticias más.[/yellow]")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(batch.model_dump_json(indent=2))
        console.print(f"[bold green]✅ Noticias guardadas en:[/bold green] {out_path}")
