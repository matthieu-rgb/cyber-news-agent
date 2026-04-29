"""
orchestrator.py - Enchaîne le pipeline complet en un seul appel.

Ordre : fetcher -> scorer -> writer -> editor -> summarizer -> bot send

Usage :
    python orchestrator.py run              # run complet
    python orchestrator.py run --dry-run    # sans sauvegarder ni envoyer sur Telegram
    python orchestrator.py run --skip-fetch # skip fetcher (debug)
"""

import logging
import subprocess
import sys
from pathlib import Path

import click

ROOT = Path(__file__).parent

logger = logging.getLogger(__name__)


def _run(step: str, *args: str, dry_run: bool = False) -> bool:
    """Lance un sous-module Python. Retourne True si succès."""
    cmd = [sys.executable, "-m", step, "run", *args]
    if dry_run:
        cmd.append("--dry-run")
    label = step.split(".")[-1]
    click.echo(f"\n{'='*60}")
    click.echo(f"  {label.upper()}")
    click.echo(f"{'='*60}")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        click.echo(f"[ERREUR] {label} a échoué (code {result.returncode})", err=True)
        return False
    return True


@click.group()
def cli():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )


@cli.command()
@click.option("--dry-run", is_flag=True, help="Affiche sans sauvegarder ni envoyer Telegram")
@click.option("--skip-fetch", is_flag=True, help="Skip fetcher (utilise les items déjà en DB)")
@click.option("--min-score", default=6, type=float, help="Score minimum pour writer (default: 6)")
def run(dry_run: bool, skip_fetch: bool, min_score: float) -> None:
    """Lance le pipeline complet : fetch -> score -> write -> edit -> summarize -> send."""
    click.echo("Pipeline cyber-news-agent démarré.")

    steps_ok = True

    # 1. Fetch
    if not skip_fetch:
        steps_ok = _run("agents.fetcher") and steps_ok

    # 2. Score
    if steps_ok:
        steps_ok = _run("agents.scorer") and steps_ok

    # 3. Write (avec score minimum)
    if steps_ok:
        cmd = [sys.executable, "-m", "agents.writer", "run", f"--min-score={min_score}"]
        if dry_run:
            cmd.append("--dry-run")
        click.echo(f"\n{'='*60}\n  WRITER\n{'='*60}")
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            click.echo("[ERREUR] writer a échoué", err=True)
            steps_ok = False

    # 4. Edit (prend le dernier draft automatiquement)
    if steps_ok and not dry_run:
        steps_ok = _run("agents.editor") and steps_ok

    # 5. Summarize
    if steps_ok and not dry_run:
        steps_ok = _run("agents.summarizer") and steps_ok

    # 6. Bot send
    if steps_ok and not dry_run:
        click.echo(f"\n{'='*60}\n  BOT SEND\n{'='*60}")
        result = subprocess.run(
            [sys.executable, "-m", "bot.main", "send"],
            cwd=ROOT,
        )
        if result.returncode != 0:
            click.echo("[ERREUR] bot send a échoué", err=True)
            steps_ok = False

    click.echo("\n" + "="*60)
    if steps_ok:
        if dry_run:
            click.echo("Pipeline terminé (dry-run). Aucune sauvegarde.")
        else:
            click.echo("Pipeline terminé. Article en attente de validation sur Telegram.")
    else:
        click.echo("Pipeline interrompu. Vérifie les erreurs ci-dessus.")
    click.echo("="*60)


if __name__ == "__main__":
    cli()
