"""
writer.py - Redige un article de blog a partir d'un item RSS top-score.

Pipeline :
  1. Charge l'item le mieux note (non encore selectionne)
  2. Fetch le contenu complet de la page source
  3. Appelle Claude pour rediger l'article en Markdown (frontmatter YAML inclus)
  4. Sauvegarde dans articles_src/<slug>.md
  5. Marque l'item comme selectionne en base

Usage:
    python -m agents.writer run
    python -m agents.writer run --item-id 42   # force un item specifique
    python -m agents.writer run --dry-run       # affiche sans sauvegarder
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import click
import httpx
import trafilatura
from slugify import slugify

import config
from data.db import get_conn, init_db

logger = logging.getLogger(__name__)

# ── Prompt systeme writer (long -> mis en cache) ──────────────────────────────

WRITER_SYSTEM = """\
Tu es Matthieu Broquard, technicien cybersecurite en formation AIS chez Jedha (diplomation mai 2026), \
stage Thales Luxembourg confirme (pentest automobile, sept. 2026). \
Tu rediges des articles pour ton blog technique 0xmatthieu.dev.

TON PROFIL EDITORIAL :
- Ton de voix : technicien qui explique a un pair, pas a un debutant. Opinions tranchees, pas de complaisance.
- Pas de sensationnalisme, pas de paraphrase proche de la source : tu commentes, tu analyses, tu contextualises.
- Tu cites obligatoirement >= 3 sources verifiables en fin d'article (frontmatter YAML sources[]).
- Tu reformules toujours : jamais de reproduction directe de passages.
- Tu te positionnes sur le sujet : si c'est important pour le pentest auto ou NIS2, tu le dis explicitement.

CONTRAINTES TECHNIQUES :
- Longueur : 800 a 1200 mots (corps, hors frontmatter).
- Langue : français avec tous les accents obligatoires (é, è, ê, à, ù, î, ô, etc.). Termes techniques en anglais quand c'est l'usage (CVE, payload, fuzzing, etc.).
- Conventions typographiques : pas de tiret cadratin (--), pas de guillemets courbes, pas de flèches Unicode. \
Les accents français sont des caractères standard et DOIVENT être présents. Espace insécable avant ; : ? !
- Structure : H2 pour les sections principales, H3 si necessaire. Pas de H1 (deja dans le frontmatter title).
- Callouts Obsidian autorises : > [!info], > [!warning], > [!danger], > [!success], > [!note]. Un seul bloc par callout.
- Pull quote : > [!quote] pour mettre en valeur une citation ou formulation cle.
- Code blocks : fenced avec langage explicite (```bash, ```python, etc.).

FORMAT DE SORTIE :
Renvoie UNIQUEMENT le contenu Markdown, sans balise, sans explication, en commencant par le frontmatter YAML :

---
title: "Titre accrocheur, 60-80 caracteres, pas clickbait"
lead: "Sous-titre/chapeau, 1-2 phrases, 100-150 caracteres"
summary: "Resume SEO, 150-160 caracteres"
date: "YYYY-MM-DD"
tags: [tag1, tag2, tag3]
primary_source: "Nom de la source principale"
reading_time: <int minutes>
sources:
  - url: "https://..."
    title: "Titre source 1"
  - url: "https://..."
    title: "Titre source 2"
  - url: "https://..."
    title: "Titre source 3"
status: draft
---

[Corps de l'article ici]
"""

# ── Fetch contenu source ───────────────────────────────────────────────────────

def fetch_article_content(url: str, timeout: int = 15) -> str:
    """Extrait le contenu textuel principal d'une page web."""
    try:
        resp = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; cyber-news-agent/0.1)"},
        )
        resp.raise_for_status()
        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if text:
            return text[:6000]  # limite pour ne pas exploser le contexte
        # Fallback : strip HTML basique
        stripped = re.sub(r"<[^>]+>", " ", resp.text)
        return re.sub(r"\s+", " ", stripped).strip()[:4000]
    except Exception as exc:
        logger.warning("Impossible de fetcher %s : %s", url, exc)
        return ""


# ── Appel Claude writer ────────────────────────────────────────────────────────

def write_article(item: dict) -> str:
    """
    Appelle Claude avec le contenu de la source et retourne le Markdown brut.
    Utilise prompt caching sur le system prompt.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    source_content = fetch_article_content(item["url"])
    source_block = (
        f"CONTENU COMPLET DE LA SOURCE :\n\n{source_content}"
        if source_content
        else "Contenu source non disponible (utilise uniquement le titre et le resume RSS)."
    )

    user_msg = (
        f"Redige un article de blog sur ce sujet.\n\n"
        f"SOURCE PRINCIPALE : {item['source']}\n"
        f"TITRE RSS : {item['title']}\n"
        f"RESUME RSS : {item.get('summary', '')[:500]}\n"
        f"URL : {item['url']}\n\n"
        f"{source_block}"
    )

    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": WRITER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )

    return message.content[0].text.strip()


# ── Sauvegarde ─────────────────────────────────────────────────────────────────

def save_draft(markdown: str, item_id: int) -> Path:
    """Sauvegarde le draft dans articles_src/ et marque l'item en base."""
    # Extrait le titre pour le slug
    title_match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', markdown, re.MULTILINE)
    title_raw = title_match.group(1) if title_match else f"article-{item_id}"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{date_str}-{slugify(title_raw, max_length=60)}"
    path = config.ARTICLES_SRC_PATH / f"{slug}.md"

    # Force la vraie date (le modele peut halluciner une date)
    markdown = re.sub(r'^date:\s*"[^"]*"', f'date: "{date_str}"', markdown, flags=re.MULTILINE)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    logger.info("Draft sauvegarde : %s", path)

    with get_conn(config.DB_PATH) as conn:
        conn.execute("UPDATE items SET selected = 1 WHERE id = ?", (item_id,))

    return path


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    init_db(config.DB_PATH)


@cli.command()
@click.option("--item-id", default=None, type=int, help="Force un item specifique")
@click.option("--dry-run", is_flag=True, help="Affiche sans sauvegarder")
@click.option("--min-score", default=6, type=float, help="Score minimum requis")
def run(item_id, dry_run, min_score):
    """Redige un article a partir du meilleur item non encore selectionne."""
    with get_conn(config.DB_PATH) as conn:
        if item_id:
            row = conn.execute(
                """SELECT i.id, f.name as source, i.title, i.url, i.summary, i.score
                   FROM items i JOIN feeds f ON f.id = i.feed_id
                   WHERE i.id = ?""",
                (item_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT i.id, f.name as source, i.title, i.url, i.summary, i.score
                   FROM items i JOIN feeds f ON f.id = i.feed_id
                   WHERE i.score >= ? AND i.selected = 0 AND i.score IS NOT NULL
                   ORDER BY i.score DESC, i.id DESC
                   LIMIT 1""",
                (min_score,),
            ).fetchone()

    if not row:
        click.echo("Aucun item eligible. Lance d'abord `scorer run`.")
        return

    item = dict(row)
    click.echo(f"\nItem selectionne : [{item['score']}/10] {item['title'][:80]}")
    click.echo(f"Source : {item['source']} | URL : {item['url']}\n")

    click.echo("Redaction en cours...")
    markdown = write_article(item)

    if dry_run:
        click.echo("\n" + "=" * 80)
        click.echo(markdown[:3000])
        if len(markdown) > 3000:
            click.echo(f"\n... ({len(markdown) - 3000} caracteres supplementaires)")
        click.echo("=" * 80)
        click.echo("\n[DRY RUN] Rien sauvegarde.")
        return

    path = save_draft(markdown, item["id"])
    click.echo(f"\nDraft sauvegarde : {path}")
    click.echo("Lance ensuite : python -m agents.editor run")


if __name__ == "__main__":
    cli()
