"""
bot/main.py - Bot Telegram de validation des articles.

Inline keyboard : [Publier] [Regenerer] [Rejeter]

State machine (SQLite articles.status) :
  pending_review -> approved | rejected
  approved -> publisher.blog + Medium
  rejected -> writer reprend le prochain item

Usage :
  python -m bot.main run              # demarre le bot en mode polling (long-running)
  python -m bot.main send [path.md]   # envoie le dernier draft pour validation
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
from html import escape
from pathlib import Path

import click
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import config
from data.db import get_conn, init_db

logger = logging.getLogger(__name__)

PREVIEW_BODY_CHARS = 500
PREVIEW_LINKEDIN_CHARS = 900


# ── Helpers DB ─────────────────────────────────────────────────────────────────

def register_article(slug: str, title: str, lead: str, body_md: str) -> int:
    """Insere ou met a jour l'article en DB. Retourne l'article_id."""
    with get_conn(config.DB_PATH) as conn:
        row = conn.execute("SELECT id FROM articles WHERE slug = ?", (slug,)).fetchone()
        if row:
            conn.execute(
                "UPDATE articles SET title=?, lead=?, body_md=?, status='pending_review', "
                "updated_at=datetime('now') WHERE id=?",
                (title, lead, body_md, row["id"]),
            )
            return row["id"]
        cur = conn.execute(
            "INSERT INTO articles (slug, title, lead, body_md, status) VALUES (?,?,?,?,'pending_review')",
            (slug, title, lead, body_md),
        )
        return cur.lastrowid


def update_article_status(article_id: int, status: str) -> None:
    with get_conn(config.DB_PATH) as conn:
        conn.execute(
            "UPDATE articles SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, article_id),
        )


def record_publication(article_id: int, msg_id: int) -> None:
    with get_conn(config.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO publications (article_id, channel, status, payload) VALUES (?,?,?,?)",
            (article_id, "telegram", "pending", str(msg_id)),
        )


def get_article_slug(article_id: int) -> str:
    with get_conn(config.DB_PATH) as conn:
        row = conn.execute("SELECT slug FROM articles WHERE id=?", (article_id,)).fetchone()
        return row["slug"] if row else ""


# ── Construction du message preview ───────────────────────────────────────────

def _strip_md(text: str) -> str:
    """Retire Markdown basique pour du texte lisible en Telegram."""
    text = re.sub(r"^---.*?---\s*", "", text, flags=re.DOTALL)  # frontmatter
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)       # code blocks
    text = re.sub(r"[#*`>_]", "", text)                          # inline markers
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)         # links -> text
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_message(article_id: int, title: str, lead: str, body_md: str, linkedin: str) -> tuple[str, InlineKeyboardMarkup]:
    body_plain = _strip_md(body_md)
    extract = body_plain[:PREVIEW_BODY_CHARS]
    if len(body_plain) > PREVIEW_BODY_CHARS:
        extract += "..."

    linkedin_preview = linkedin[:PREVIEW_LINKEDIN_CHARS]
    if len(linkedin) > PREVIEW_LINKEDIN_CHARS:
        linkedin_preview += "..."

    # HTML parse mode : escape pour eviter les erreurs de parsing
    parts = [
        f"<b>{escape(title)}</b>",
        f"<i>{escape(lead)}</i>",
        "",
        "--- Extrait ---",
        escape(extract),
        "",
        "--- LinkedIn pret ---",
        escape(linkedin_preview) if linkedin_preview else "(summarizer non encore lance)",
        "",
        f"<code>#article_{article_id}</code>",
    ]

    text = "\n".join(parts)
    if len(text) > 4000:
        text = text[:4000] + "..."

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Publier", callback_data=f"approve_{article_id}"),
            InlineKeyboardButton("Regenerer", callback_data=f"regen_{article_id}"),
            InlineKeyboardButton("Rejeter", callback_data=f"reject_{article_id}"),
        ]
    ])
    return text, keyboard


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        await query.edit_message_text("Callback invalide.")
        return

    action, article_id = parts[0], int(parts[1])
    chat_id = query.message.chat_id
    slug = get_article_slug(article_id)

    if action == "approve":
        update_article_status(article_id, "approved")
        await query.edit_message_text(
            f"Article #{article_id} approuve. Publication en cours...",
            parse_mode="HTML",
        )
        # Deploy en sous-processus (bloquant mais court)
        draft_path = config.ARTICLES_SRC_PATH / f"{slug}.md"
        try:
            result = subprocess.run(
                [sys.executable, "-m", "publisher.blog", "deploy", str(draft_path)],
                capture_output=True, text=True, timeout=60,
                cwd=str(config.ARTICLES_SRC_PATH.parent),
            )
            if result.returncode == 0:
                update_article_status(article_id, "published")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Publie sur le blog.\n<code>{result.stdout.strip()}</code>",
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Erreur deploy :\n<code>{result.stderr.strip()}</code>",
                    parse_mode="HTML",
                )
        except subprocess.TimeoutExpired:
            await context.bot.send_message(chat_id=chat_id, text="Timeout deploy (> 60s).")

    elif action == "reject":
        update_article_status(article_id, "rejected")
        await query.edit_message_text(
            f"Article #{article_id} rejete.",
            parse_mode="HTML",
        )

    elif action == "regen":
        update_article_status(article_id, "rejected")
        await query.edit_message_text(
            f"Article #{article_id} rejete. Regeneration en attente.",
            parse_mode="HTML",
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Relance le pipeline complet :\n"
                "<code>python -m agents.writer run\n"
                "python -m agents.editor run\n"
                "python -m agents.summarizer run\n"
                "python -m bot.main send</code>"
            ),
            parse_mode="HTML",
        )


# ── Send async helper ──────────────────────────────────────────────────────────

async def _send_draft_async(path: Path) -> None:
    md = path.read_text(encoding="utf-8")

    title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', md, re.MULTILINE)
    lead_m = re.search(r'^lead:\s*["\']?(.+?)["\']?\s*$', md, re.MULTILINE)
    title = title_m.group(1) if title_m else path.stem
    lead = lead_m.group(1) if lead_m else ""
    slug = path.stem

    social_path = path.with_name(slug + "_social.json")
    linkedin = ""
    if social_path.exists():
        social = json.loads(social_path.read_text(encoding="utf-8"))
        linkedin = social.get("linkedin", "")
    else:
        logger.warning("Pas de %s — lance d'abord summarizer.py", social_path.name)

    article_id = register_article(slug, title, lead, md)
    logger.info("Article #%d enregistre (pending_review)", article_id)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    text, keyboard = build_message(article_id, title, lead, md, linkedin)

    async with app:
        msg = await app.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        record_publication(article_id, msg.message_id)
        logger.info("Message Telegram envoye (msg_id=%d)", msg.message_id)

        # Deuxieme message : post LinkedIn complet pour copy-paste
        if linkedin:
            await app.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=f"<b>POST LINKEDIN - copier-coller :</b>\n\n{escape(linkedin)}",
                parse_mode="HTML",
            )

    click.echo(f"Article #{article_id} envoye sur Telegram. En attente de ta validation.")


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
def run():
    """Demarre le bot en mode polling (long-running, Ctrl+C pour arreter)."""
    if not config.TELEGRAM_BOT_TOKEN:
        click.echo("TELEGRAM_BOT_TOKEN manquant dans .env")
        return
    click.echo("Bot Telegram demarre (polling). Ctrl+C pour arreter.")
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(allowed_updates=["callback_query"])


@cli.command()
@click.argument("draft_path", required=False)
def send(draft_path):
    """Envoie un draft pour validation sur Telegram. Sans argument, prend le plus recent."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        click.echo("TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID requis dans .env")
        return

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

    click.echo(f"Envoi de : {path.name}")
    asyncio.run(_send_draft_async(path))


if __name__ == "__main__":
    cli()
