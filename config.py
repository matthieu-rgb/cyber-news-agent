"""
config.py - Charge .env et expose les settings du projet.
Importé par tous les agents.
"""

from pathlib import Path
from dotenv import dotenv_values

_ROOT = Path(__file__).parent
_env = dotenv_values(_ROOT / ".env")


def _require(key: str) -> str:
    val = _env.get(key) or ""
    if not val:
        raise RuntimeError(
            f"Variable manquante dans .env : {key}\n"
            f"Copier .env.example en .env et renseigner la valeur."
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return _env.get(key) or default


# Anthropic
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

# Telegram (Phase B5)
TELEGRAM_BOT_TOKEN: str = _optional("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _optional("TELEGRAM_CHAT_ID")

# Medium (Phase B6)
MEDIUM_INTEGRATION_TOKEN: str = _optional("MEDIUM_INTEGRATION_TOKEN")
MEDIUM_USER_ID: str = _optional("MEDIUM_USER_ID")

# Paths
DB_PATH = _ROOT / "data" / "articles.db"
PORTFOLIO_PATH = Path("/Users/matthieu/Documents/mon-portfolio")
ARTICLES_SRC_PATH = _ROOT / "articles_src"
