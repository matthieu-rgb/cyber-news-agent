"""
editor.py - Revise un draft d'article (max 2 iterations).

L'editeur joue le role d'un redacteur senior : il verifie la structure,
les sources, la factualite apparente, le ton, et renvoie soit une version
revisee soit un verdict "OK".

Usage:
    python -m agents.editor run <path/to/draft.md>
    python -m agents.editor run   # prend le dernier draft en articles_src/
"""

import logging
import re
from pathlib import Path

import anthropic
import click

import config
from data.db import init_db

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 2

EDITOR_SYSTEM = """\
Tu es un redacteur en chef senior specialise en cybersecurite. \
Tu relis les articles de Matthieu Broquard avant publication sur 0xmatthieu.dev.

TES CRITERES DE REVISION :
1. Exactitude factuelle apparente : les affirmations sont-elles plausibles et sourcees ?
2. Sources : >= 3 sources citees dans le frontmatter YAML ? Les URLs semblent-elles valides ?
3. Structure : titre accrocheur ? lead percutant ? sections H2 logiques ?
4. Ton : technique sans etre jargonneux, opinions claires, pas de complaisance ?
5. Longueur : entre 700 et 1300 mots (corps hors frontmatter) ?
6. Conventions : accents français obligatoires (é, è, à, etc.), pas de tiret cadratin, espace insécable avant ; : ? ! ?
7. Pertinence editoriale : l'article apporte-t-il une valeur ajoutee par rapport a la source brute ?

COMPORTEMENT :
- Si l'article est publiable avec des corrections mineures : renvoie "VERDICT: OK" suivi de tes remarques en une liste courte.
- Si l'article necessite des corrections substantielles : renvoie "VERDICT: REVISE" suivi de l'article COMPLET revise \
  (frontmatter YAML inclus, meme format que l'input).
- Si l'article est clairement hors sujet ou irreparable : renvoie "VERDICT: REJETE" avec justification en 2 phrases.

Important : ne modifie jamais le champ `status` du frontmatter. Ne change pas les URLs des sources.
"""


def _count_words(text: str) -> int:
    # Ignore le frontmatter
    body = re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL)
    return len(body.split())


def edit_article(markdown: str, iteration: int = 1) -> tuple[str, str]:
    """
    Soumet l'article a l'editeur Claude.
    Retourne (verdict: 'OK'|'REVISE'|'REJETE', contenu: str).
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    words = _count_words(markdown)
    user_msg = (
        f"Relis cet article ({words} mots, iteration {iteration}/{MAX_ITERATIONS}) "
        f"et applique tes criteres :\n\n{markdown}"
    )

    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": EDITOR_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )

    response = message.content[0].text.strip()

    if response.startswith("VERDICT: REVISE"):
        body = re.sub(r"^VERDICT: REVISE\s*\n?", "", response, count=1).strip()
        # Extrait l'article du bloc ```markdown si present
        md_block = re.search(r"```markdown\s*\n(.*?)\n```", body, re.DOTALL)
        if md_block:
            revised = md_block.group(1).strip()
        else:
            # Cherche le debut du frontmatter --- (ignore notes de critique avant)
            fm_match = re.search(r"^---\s*$", body, re.MULTILINE)
            revised = body[fm_match.start():].strip() if fm_match else body
        # Supprime les notes de relecture internes
        revised = re.sub(r"> \*\*Note de relecture[^\n]*\n[^\n]*\n\n?", "", revised)
        return "REVISE", revised

    if response.startswith("VERDICT: REJETE"):
        reason = re.sub(r"^VERDICT: REJETE\s*\n?", "", response, count=1).strip()
        return "REJETE", reason

    # OK ou format inattendu -> on considere OK
    remarks = re.sub(r"^VERDICT: OK\s*\n?", "", response, count=1).strip()
    return "OK", remarks


def run_editor(path: Path) -> tuple[str, Path]:
    """
    Lance le cycle d'edition (max MAX_ITERATIONS) sur un fichier draft.
    Retourne (verdict final, chemin du fichier final).
    """
    markdown = path.read_text(encoding="utf-8")
    current = markdown
    final_path = path

    for i in range(1, MAX_ITERATIONS + 1):
        logger.info("Edition iteration %d/%d...", i, MAX_ITERATIONS)
        verdict, content = edit_article(current, iteration=i)
        logger.info("Verdict : %s", verdict)

        if verdict == "REJETE":
            logger.warning("Article rejete : %s", content)
            return "REJETE", path

        if verdict == "REVISE":
            current = content
            # Sauvegarde la version revisee en ecrasant le fichier
            path.write_text(current, encoding="utf-8")
            logger.info("Version revisee sauvegardee.")
            continue

        # OK
        if verdict == "OK":
            logger.info("Remarques editeur :\n%s", content)
            return "OK", path

    # Apres MAX_ITERATIONS, on accepte l'etat actuel
    return "OK", path


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
def run(draft_path):
    """Relit et revise un draft. Sans argument, prend le plus recent dans articles_src/."""
    if draft_path:
        path = Path(draft_path)
    else:
        drafts = sorted(config.ARTICLES_SRC_PATH.glob("*.md"), key=lambda p: p.stat().st_mtime)
        drafts = [d for d in drafts if "bienvenue" not in d.name]
        if not drafts:
            click.echo("Aucun draft trouve dans articles_src/")
            return
        path = drafts[-1]

    if not path.exists():
        click.echo(f"Fichier introuvable : {path}")
        return

    click.echo(f"Edition de : {path.name}")
    click.echo(f"Mots : {_count_words(path.read_text())}\n")

    verdict, final_path = run_editor(path)

    if verdict == "REJETE":
        click.echo("Article rejete. Relancer writer avec un autre item.")
        return

    click.echo(f"\nArticle valide ({verdict}). Fichier : {final_path}")
    click.echo("Lance ensuite : python -m publisher.blog build " + str(final_path))


if __name__ == "__main__":
    cli()
