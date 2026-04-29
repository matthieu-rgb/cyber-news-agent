"""
scorer.py - Note les items RSS selon le profil de Matthieu (0-10) via Claude.

Strategie : batch de 10 items par appel API pour limiter les couts.
Seuls les items des 72 dernières heures non encore scores sont traites.

Usage:
    python -m agents.scorer run            # score les nouveaux items
    python -m agents.scorer run --top 5    # affiche aussi le top 5
    python -m agents.scorer show-top --n 10
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import anthropic
import click

import config
from data.db import get_conn, init_db

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
WINDOW_HOURS = 72

SYSTEM_PROMPT = """\
Tu es un assistant de veille cyber pour Matthieu, profil :
- Formation AIS (Administrateur Infrastructure Securisee, RNCP37680 niveau 6), Jedha Academy, diplomation mai 2026.
- Stage confirme : Thales Luxembourg, pentest automobile, septembre 2026.
- Specialites techniques : pentest automobile (CAN bus, UDS, ECU, OBD-II, ICSim, RAMN), Active Directory (Responder, NTLM relay, Kerberoasting, BloodHound), NIS2/AIS, AWS (SAA-C03 en preparation), agents LLM en production, SecDevOps.
- Interets secondaires : hardening Linux, Ansible, Docker, SIEM Wazuh, CTF (HTB/THM).
- Niveau : technicien confirmé, lit l'anglais technique sans problème, aversion forte au sensationnalisme et aux articles marketing.
- Rejet explicite : articles de hype sans profondeur technique, rebrandings marketing, FUD generique, vulnérabilités triviales sans impact reel.

Pour chaque item RSS fourni, attribue un score de 0 a 10 selon ces criteres :
- 9-10 : directement dans sa zone de specialisation (pentest auto, NIS2 art. 21, UDS/CAN, HTB/CTF avance, AD attacks avances)
- 7-8 : pertinent pour son profil (cloud sec, DevSecOps, vulns critiques avec detail technique, outils red team)
- 5-6 : interet general cyber educatif (bonne vulgarisation technique, incidents notables, tools generiques)
- 3-4 : peripherique (IT generique, politique cyber sans technique, breaches sans detail)
- 0-2 : hors sujet ou sensationnalisme (marketing, titres clickbait sans fond, crypto/NFT/IA hype sans securite)

Reponds UNIQUEMENT avec un tableau JSON valide, sans markdown, sans commentaire :
[{"id": <int>, "score": <int 0-10>, "reason": "<1 phrase max, en francais>"}]
"""


def _score_batch(client: anthropic.Anthropic, items: list[dict]) -> list[dict]:
    """Envoie un batch d'items a Claude et retourne les scores."""
    payload = [
        {"id": it["id"], "title": it["title"] or "", "summary": (it["summary"] or "")[:300]}
        for it in items
    ]
    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Score ces {len(payload)} items RSS :\n{json.dumps(payload, ensure_ascii=False)}",
            }
        ],
    )
    raw = message.content[0].text.strip()
    return json.loads(raw)


def run_scorer(top_n: int = 5, window_hours: int = WINDOW_HOURS) -> list[dict]:
    """
    Score les items non encore notes des <window_hours> dernieres heures.
    Retourne les <top_n> meilleurs items tries par score desc.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    with get_conn(config.DB_PATH) as conn:
        rows = conn.execute(
            """SELECT id, title, summary FROM items
               WHERE score IS NULL AND fetched_at >= ?
               ORDER BY id""",
            (cutoff,),
        ).fetchall()

    if not rows:
        logger.info("Aucun nouvel item a scorer.")
        return []

    items = [dict(r) for r in rows]
    logger.info("%d items a scorer (fenetre %dh)", len(items), window_hours)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    results: list[dict] = []

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i : i + BATCH_SIZE]
        logger.info("Batch %d/%d (%d items)...", i // BATCH_SIZE + 1,
                    -(-len(items) // BATCH_SIZE), len(batch))
        try:
            scored = _score_batch(client, batch)
            results.extend(scored)
        except (json.JSONDecodeError, anthropic.APIError) as exc:
            logger.error("Erreur batch %d : %s", i // BATCH_SIZE + 1, exc)
            continue

    # Persistance des scores
    with get_conn(config.DB_PATH) as conn:
        for r in results:
            conn.execute(
                "UPDATE items SET score = ?, score_reason = ? WHERE id = ?",
                (r.get("score"), r.get("reason"), r["id"]),
            )

    logger.info("%d items scores.", len(results))

    # Top N
    with get_conn(config.DB_PATH) as conn:
        top = conn.execute(
            """SELECT i.id, f.name as source, i.title, i.url,
                      i.score, i.score_reason, i.published_at
               FROM items i JOIN feeds f ON f.id = i.feed_id
               WHERE i.score IS NOT NULL
               ORDER BY i.score DESC, i.id DESC
               LIMIT ?""",
            (top_n,),
        ).fetchall()

    return [dict(r) for r in top]


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
@click.option("--top", default=5, help="Affiche le top N apres scoring")
@click.option("--window", default=WINDOW_HOURS, help="Fenetre en heures")
def run(top, window):
    """Score les nouveaux items et affiche le top."""
    top_items = run_scorer(top_n=top, window_hours=window)
    if not top_items:
        click.echo("Aucun item score.")
        return
    click.echo(f"\nTop {top} items :")
    click.echo("-" * 80)
    for it in top_items:
        pub = (it.get("published_at") or "?")[:10]
        click.echo(f"[{it['score']:2.0f}/10]  {pub}  {it['source']:<20}  {it['title'][:60]}")
        click.echo(f"         {it['score_reason']}")
    click.echo("-" * 80)


@cli.command("show-top")
@click.option("--n", default=10)
def show_top(n):
    """Affiche le top N des items deja scores."""
    with get_conn(config.DB_PATH) as conn:
        rows = conn.execute(
            """SELECT i.id, f.name as source, i.title, i.score, i.score_reason
               FROM items i JOIN feeds f ON f.id = i.feed_id
               WHERE i.score IS NOT NULL
               ORDER BY i.score DESC, i.id DESC LIMIT ?""",
            (n,),
        ).fetchall()
    for r in rows:
        click.echo(f"[{r['score']:2.0f}/10]  {r['source']:<20}  {r['title'][:65]}")
        click.echo(f"         {r['score_reason']}")


if __name__ == "__main__":
    cli()
