"""
summarizer.py - Genere un post Medium et un post LinkedIn a partir d'un draft d'article.

Les deux formats sont produits en un seul appel Claude pour minimiser le cout.
Output sauvegarde dans articles_src/<slug>_social.json avec les cles `medium` et `linkedin`.

Usage:
    python -m agents.summarizer run                    # prend le dernier draft
    python -m agents.summarizer run path/to/draft.md  # draft specifique
    python -m agents.summarizer run --dry-run         # affiche sans sauvegarder
"""

import json
import logging
import re
from pathlib import Path

import anthropic
import click

import config
from data.db import init_db

logger = logging.getLogger(__name__)

# ── Prompt systeme summarizer (mis en cache) ───────────────────────────────────

SUMMARIZER_SYSTEM = """\
Tu es Matthieu Broquard, technicien cybersecurite en formation AIS chez Jedha. \
Tu adaptes tes articles de blog (0xmatthieu.dev) pour deux plateformes : Medium et LinkedIn.

CONVENTIONS ABSOLUES (s'appliquent aux deux formats) :
- Accents français obligatoires (é, è, ê, à, ù, etc.). Pas de tiret cadratin, pas de guillemets courbes, \
pas de flèches Unicode. Espace insécable avant ; : ? !
- Pas d'emojis.
- Ton : technicien qui s'adresse a des pairs, opinions tranchees, pas de sensationnalisme.
- Jamais de reproduction directe de passages de l'article source : reformule toujours.

---

FORMAT MEDIUM :
- Longueur : 2500 a 3500 caracteres (corps, sans frontmatter).
- Structure Markdown : un titre H2 "hook" different du titre du blog, \
sections H3 si necessaire, un bloc de code ou callout si pertinent.
- Premier paragraphe : accroche forte, question ou affirmation qui force a continuer.
- CTA en fin : une phrase invitant a lire l'article complet sur le blog, \
avec une phrase du type "Article complet sur 0xmatthieu.dev".
- Pas de frontmatter YAML.

FORMAT LINKEDIN :
- Longueur : 1300 a 2000 caracteres (texte brut, sans Markdown).
- Premiere ligne : hook percutant (max 180 caracteres), accroche avant le "voir plus".
- Structure aeree : paragraphes courts (2-4 lignes), espaces entre chaque bloc.
- Contenu : 2-3 insights techniques concrets, pas de paraphrase creuse.
- CTA final : une phrase vers l'article sur 0xmatthieu.dev.
- Hashtags : 5 a 8 hashtags pertinents en derniere ligne, separes par des espaces.
- Format texte pur : pas de Markdown, pas de titres avec #, pas de listes avec -.

---

FORMAT DE SORTIE :
Renvoie UNIQUEMENT un objet JSON valide avec exactement deux cles :
{
  "medium": "<contenu Medium en Markdown, chaines echappees>",
  "linkedin": "<contenu LinkedIn en texte brut, chaines echappees>"
}

Rien d'autre : pas d'explication, pas de balise, pas de texte avant ou apres le JSON.
"""


# ── Appel Claude summarizer ────────────────────────────────────────────────────

def summarize_article(markdown: str) -> dict[str, str]:
    """
    Genere les versions Medium et LinkedIn d'un article.
    Retourne un dict avec les cles 'medium' et 'linkedin'.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Extrait titre + lead pour contextualiser sans envoyer le MD complet
    title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', markdown, re.MULTILINE)
    lead_m = re.search(r'^lead:\s*["\']?(.+?)["\']?\s*$', markdown, re.MULTILINE)
    title = title_m.group(1) if title_m else "Article"
    lead = lead_m.group(1) if lead_m else ""

    # Corps sans frontmatter (pour estimer la longueur et donner le contenu)
    body = re.sub(r"^---.*?---\s*", "", markdown, flags=re.DOTALL).strip()

    user_msg = (
        f"Adapte cet article pour Medium et LinkedIn.\n\n"
        f"TITRE : {title}\n"
        f"LEAD : {lead}\n\n"
        f"CORPS DE L'ARTICLE :\n{body}"
    )

    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SUMMARIZER_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = message.content[0].text.strip()

    # Extrait le JSON meme si le modele ajoute du texte autour
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"Reponse non JSON : {raw[:200]}")

    result = json.loads(json_match.group())

    if "medium" not in result or "linkedin" not in result:
        raise ValueError(f"Cles manquantes dans la reponse : {list(result.keys())}")

    return result


# ── Sauvegarde ─────────────────────────────────────────────────────────────────

def save_social(draft_path: Path, social: dict[str, str]) -> Path:
    """Sauvegarde les posts social dans <slug>_social.json a cote du draft."""
    out_path = draft_path.with_name(draft_path.stem + "_social.json")
    out_path.write_text(json.dumps(social, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Social sauvegarde : %s", out_path)
    return out_path


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
@click.argument("draft_path", required=False)
@click.option("--dry-run", is_flag=True, help="Affiche sans sauvegarder")
def run(draft_path, dry_run):
    """Genere les posts Medium et LinkedIn depuis un draft. Sans argument, prend le plus recent."""
    if draft_path:
        path = Path(draft_path)
    else:
        drafts = sorted(
            [d for d in config.ARTICLES_SRC_PATH.glob("*.md") if "_social" not in d.name],
            key=lambda p: p.stat().st_mtime,
        )
        drafts = [d for d in drafts if "bienvenue" not in d.name]
        if not drafts:
            click.echo("Aucun draft trouve dans articles_src/")
            return
        path = drafts[-1]

    if not path.exists():
        click.echo(f"Fichier introuvable : {path}")
        return

    click.echo(f"Summarizer sur : {path.name}\n")
    click.echo("Generation Medium + LinkedIn...")

    social = summarize_article(path.read_text(encoding="utf-8"))

    click.echo("\n" + "=" * 80)
    click.echo("POST MEDIUM")
    click.echo("=" * 80)
    click.echo(social["medium"])

    click.echo("\n" + "=" * 80)
    click.echo("POST LINKEDIN")
    click.echo("=" * 80)
    click.echo(social["linkedin"])
    click.echo("=" * 80)

    chars_medium = len(social["medium"])
    chars_linkedin = len(social["linkedin"])
    click.echo(f"\nMedium : {chars_medium} caracteres | LinkedIn : {chars_linkedin} caracteres")

    if dry_run:
        click.echo("\n[DRY RUN] Rien sauvegarde.")
        return

    out = save_social(path, social)
    click.echo(f"\nSauvegarde : {out}")
    click.echo("Lance ensuite : python -m bot.main (B5) ou publication manuelle")


if __name__ == "__main__":
    cli()
