# cyber-news-agent

Pipeline agentique de veille cyber pour `0xmatthieu.dev`.

Lit les flux RSS spécialisés (CERT-FR, ANSSI, BleepingComputer, Krebs, The Hacker News, Auto-ISAC, ENISA), score chaque item selon le profil (AIS, pentest auto, AD, NIS2, AWS), rédige un article original avec sources citées, génère un résumé pour Medium et LinkedIn, soumet à validation humaine via Telegram, puis publie.

## Phasage

- **Phase A** : socle blog (générateur Markdown -> HTML, template article, RSS, index)
- **Phase B** : agents LLM (fetcher, scorer, writer, editor, summarizer) + bot Telegram
- **Phase C** : déploiement VPS Hetzner (Ansible, Docker, systemd, Wazuh)

## Stack

- Python 3.11.9 (pyenv)
- uv (gestion projet)
- Jinja2 + markdown-it-py + Pygments (Phase A)
- Anthropic SDK Claude Sonnet 4.6 (Phase B)
- python-telegram-bot (Phase B)
- SQLite (state machine + dedup)
- Ansible + Docker Compose + sops/age (Phase C)

## Structure

```
agents/         (Phase B) fetcher, scorer, writer, editor, summarizer
publisher/      blog.py (Phase A), medium.py (Phase B)
templates/      Jinja2 templates (article.html.j2, feed.xml.j2)
bot/            (Phase B) Telegram bot + state machine
articles_src/   sources Markdown des articles
infra/          (Phase C) Ansible, docker-compose, systemd units
tests/
data/           SQLite, runtime (gitignored)
```

## Usage local (Phase A)

```bash
# Generer un article HTML a partir d'un fichier Markdown
uv run python -m publisher.blog build articles_src/2026-04-28-mon-article.md

# Generer index.json + feed.xml a partir de tous les articles publies
uv run python -m publisher.blog index

# Tout en une commande
uv run python -m publisher.blog publish articles_src/2026-04-28-mon-article.md
```

## Domaine cible

`0xmatthieu.dev` (le repo portfolio est `~/Documents/mon-portfolio`, déploiement GitHub Pages).
