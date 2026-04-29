"""
fetcher.py - Agregation RSS + dedup SQLite.

Usage:
    python -m agents.fetcher run          # fetch toutes les sources
    python -m agents.fetcher run --limit 3
    python -m agents.fetcher list-feeds
    python -m agents.fetcher add-feed --name "Mon feed" --url https://...
"""

import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import click
import feedparser
import httpx

from data.db import DB_PATH, get_conn, init_db

logger = logging.getLogger(__name__)

# Sources par defaut - ajustables via `add-feed`
DEFAULT_FEEDS: list[dict] = [
    {"name": "CERT-FR",          "url": "https://www.cert.ssi.gouv.fr/feed/"},
    {"name": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/"},
    {"name": "The Hacker News",  "url": "https://feeds.feedburner.com/TheHackersNews"},
    {"name": "Krebs on Security","url": "https://krebsonsecurity.com/feed/"},
    {"name": "Dark Reading",     "url": "https://www.darkreading.com/rss.xml"},
    {"name": "SecurityWeek",     "url": "https://feeds.feedburner.com/securityweek"},
    {"name": "Schneier",         "url": "https://www.schneier.com/feed/atom/"},
    {"name": "The Record",       "url": "https://therecord.media/feed"},
    {"name": "Security Affairs", "url": "https://securityaffairs.com/feed"},
]

TIMEOUT = 15  # secondes


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def _parse_date(entry) -> str | None:
    """Retourne une date ISO 8601 ou None."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).isoformat()
            except Exception:
                pass
    return None


def _summary(entry) -> str:
    raw = getattr(entry, "summary", "") or ""
    # Nettoie les tags HTML basiques
    import re
    return re.sub(r"<[^>]+>", "", raw).strip()[:800]


def seed_feeds(path=DB_PATH) -> None:
    """Insere les feeds par defaut si absents."""
    with get_conn(path) as conn:
        for feed in DEFAULT_FEEDS:
            conn.execute(
                "INSERT OR IGNORE INTO feeds (name, url) VALUES (?, ?)",
                (feed["name"], feed["url"]),
            )


def fetch_feed(feed_id: int, name: str, url: str, path=DB_PATH) -> int:
    """
    Fetch un feed RSS, insere les nouveaux items.
    Retourne le nombre de nouveaux items inseres.
    """
    logger.info("Fetching %s (%s)", name, url)
    try:
        resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "cyber-news-agent/0.1 (0xmatthieu.dev)"})
        resp.raise_for_status()
        raw = resp.text
    except Exception as exc:
        logger.warning("Erreur fetch %s : %s", name, exc)
        return 0

    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        logger.warning("Feed malformed %s : %s", name, parsed.bozo_exception)
        return 0

    new_count = 0
    now = datetime.now(timezone.utc).isoformat()

    with get_conn(path) as conn:
        for entry in parsed.entries:
            url_item = entry.get("link", "").strip()
            if not url_item:
                continue
            h = _url_hash(url_item)
            exists = conn.execute(
                "SELECT id FROM items WHERE url_hash = ?", (h,)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO items
                   (feed_id, url_hash, url, title, summary, published_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    feed_id,
                    h,
                    url_item,
                    (entry.get("title") or "").strip()[:500],
                    _summary(entry),
                    _parse_date(entry),
                ),
            )
            new_count += 1

        conn.execute(
            "UPDATE feeds SET last_fetch = ? WHERE id = ?", (now, feed_id)
        )

    logger.info("%s : %d nouveaux items", name, new_count)
    return new_count


def run_fetch(limit: int | None = None, path=DB_PATH) -> int:
    """Fetch tous les feeds enregistres. Retourne le total de nouveaux items."""
    seed_feeds(path)
    with get_conn(path) as conn:
        feeds = conn.execute("SELECT id, name, url FROM feeds").fetchall()

    total = 0
    for i, row in enumerate(feeds):
        if limit and i >= limit:
            break
        total += fetch_feed(row["id"], row["name"], row["url"], path)

    logger.info("Fetch termine : %d nouveaux items au total", total)
    return total


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.group()
@click.option("--db", default=str(DB_PATH), help="Chemin vers la base SQLite")
@click.pass_context
def cli(ctx, db):
    ctx.ensure_object(dict)
    ctx.obj["db"] = Path(db)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    init_db(ctx.obj["db"])


@cli.command()
@click.option("--limit", default=None, type=int, help="Nombre max de feeds a fetcher")
@click.pass_context
def run(ctx, limit):
    """Fetch tous les feeds et affiche le bilan."""
    total = run_fetch(limit=limit, path=ctx.obj["db"])
    click.echo(f"\nTotal nouveaux items : {total}")


@cli.command("list-feeds")
@click.pass_context
def list_feeds(ctx):
    """Liste les feeds enregistres."""
    seed_feeds(ctx.obj["db"])
    with get_conn(ctx.obj["db"]) as conn:
        rows = conn.execute(
            "SELECT id, name, url, last_fetch FROM feeds ORDER BY id"
        ).fetchall()
    for r in rows:
        last = r["last_fetch"] or "jamais"
        click.echo(f"[{r['id']:2d}] {r['name']:<22} {r['url']}")
        click.echo(f"       dernier fetch : {last}")


@cli.command("list-items")
@click.option("--n", default=20, help="Nombre d'items a afficher")
@click.pass_context
def list_items(ctx, n):
    """Affiche les derniers items recus."""
    with get_conn(ctx.obj["db"]) as conn:
        rows = conn.execute(
            """SELECT i.id, f.name, i.title, i.published_at
               FROM items i JOIN feeds f ON f.id = i.feed_id
               ORDER BY i.id DESC LIMIT ?""",
            (n,),
        ).fetchall()
    for r in rows:
        pub = (r["published_at"] or "?")[:10]
        click.echo(f"[{r['id']:4d}] {pub}  {r['name']:<22}  {r['title'][:80]}")


@cli.command("add-feed")
@click.option("--name", required=True)
@click.option("--url", required=True)
@click.pass_context
def add_feed(ctx, name, url):
    """Ajoute un feed RSS a la base."""
    with get_conn(ctx.obj["db"]) as conn:
        conn.execute("INSERT OR IGNORE INTO feeds (name, url) VALUES (?, ?)", (name, url))
    click.echo(f"Feed ajoute : {name} -> {url}")


if __name__ == "__main__":
    cli()
