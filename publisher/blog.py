"""Blog generator : Markdown -> HTML pour 0xmatthieu.dev.

Pipeline :
  1. Lit un fichier `.md` avec frontmatter YAML (titre, date, tags, lead, sources, slug, status)
  2. Rend le Markdown en HTML via markdown-it-py + plugins (anchor, deflist, tasklist, footnote)
  3. Applique syntax highlighting via Pygments (wrappe chaque bloc dans .codehilite + bouton copy)
  4. Transforme les blockquotes Obsidian-style `> [!info] Titre` en `<aside class="callout">`
  5. Genere une TOC a partir des h2/h3
  6. Rend le tout via le template Jinja2 `templates/article.html.j2`
  7. Ecrit le HTML final dans le repo portfolio (`articles/<slug>.html`)
  8. Met a jour `articles/index.json` et `articles/feed.xml`

Usage :
  uv run python -m publisher.blog build articles_src/2026-04-28-test.md
  uv run python -m publisher.blog index
  uv run python -m publisher.blog publish articles_src/2026-04-28-test.md
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click
import frontmatter
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from slugify import slugify

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
ARTICLES_SRC_DIR = REPO_ROOT / "articles_src"

PORTFOLIO_PATH = Path(
    os.environ.get("PORTFOLIO_REPO_PATH", "/Users/matthieu/Documents/mon-portfolio")
).resolve()
ARTICLES_OUT_DIR = PORTFOLIO_PATH / "articles"

SITE_URL = "https://0xmatthieu.dev"
WORDS_PER_MINUTE = 220

CALLOUT_TYPES = {"info", "warning", "success", "danger", "note"}
CALLOUT_ALIASES = {
    "tip": "info",
    "abstract": "info",
    "important": "warning",
    "caution": "danger",
    "error": "danger",
    "fail": "danger",
    "todo": "note",
    "example": "note",
}
# `quote` est traite a part comme pull-quote (signature editoriale)


@dataclass
class Article:
    """Article publie (HTML + meta)."""

    slug: str
    title: str
    date: datetime
    tags: list[str]
    summary: str
    lead: str | None = None
    primary_source: str | None = None
    sources: list[dict[str, str]] = field(default_factory=list)
    medium_url: str | None = None
    status: str = "draft"
    src_path: Path | None = None

    @property
    def date_iso(self) -> str:
        return self.date.isoformat()

    @property
    def date_human(self) -> str:
        mois = [
            "janvier", "fevrier", "mars", "avril", "mai", "juin",
            "juillet", "aout", "septembre", "octobre", "novembre", "decembre",
        ]
        return f"{self.date.day} {mois[self.date.month - 1]} {self.date.year}"

    @property
    def date_rfc822(self) -> str:
        dt = self.date if self.date.tzinfo else self.date.replace(tzinfo=timezone.utc)
        return format_datetime(dt)


def load_article(path: Path) -> tuple[Article, str]:
    """Charge un article depuis un fichier Markdown avec frontmatter YAML."""
    post = frontmatter.load(path)
    meta = post.metadata
    body = post.content

    if "title" not in meta:
        raise click.ClickException(f"{path.name} : frontmatter `title` manquant")

    raw_date = meta.get("date")
    if isinstance(raw_date, str):
        date = datetime.fromisoformat(raw_date)
    elif isinstance(raw_date, datetime):
        date = raw_date
    else:
        date = datetime.now(timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    slug = meta.get("slug") or slugify(meta["title"])
    summary = meta.get("summary") or _extract_summary(body)

    article = Article(
        slug=slug,
        title=meta["title"],
        date=date,
        tags=list(meta.get("tags") or []),
        summary=summary,
        lead=meta.get("lead"),
        primary_source=meta.get("primary_source"),
        sources=list(meta.get("sources") or []),
        medium_url=meta.get("medium_url"),
        status=meta.get("status", "draft"),
        src_path=path,
    )
    return article, body


def _extract_summary(body: str, max_chars: int = 200) -> str:
    """Premiere phrase ou ~200 chars du body, sans markdown."""
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"[#>*_`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 0 else cut) + "..."


def _highlighter(code: str, lang: str, _attrs: str) -> str:
    """Highlighter Pygments avec wrapper custom (header langue + bouton copy)."""
    lang_clean = (lang or "").strip().split()[0] if lang else ""
    try:
        lexer = get_lexer_by_name(lang_clean or "text", stripall=False)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    formatter = HtmlFormatter(nowrap=True, noclasses=False, classprefix="")
    highlighted = highlight(code, lexer, formatter)
    lang_label = lang_clean.upper() if lang_clean else "TEXT"
    return (
        '<div class="codehilite">'
        '<div class="codehilite-header">'
        f'<span class="codehilite-lang">{lang_label}</span>'
        '<button type="button" class="codehilite-copy" aria-label="Copier le code">'
        '<i class="fa-regular fa-copy"></i> Copier</button>'
        '</div>'
        f'<pre><code>{highlighted}</code></pre>'
        '</div>'
    )


def _build_md() -> MarkdownIt:
    """Instance markdown-it-py configuree avec plugins."""
    md = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": True,
            "typographer": False,
            "breaks": False,
            "highlight": _highlighter,
        },
    )
    md.enable(["table", "strikethrough"])
    md.use(
        anchors_plugin,
        min_level=2,
        max_level=4,
        slug_func=lambda s: slugify(s),
        permalink=True,
        permalinkSymbol="#",
        permalinkSpace=False,
    )
    md.use(footnote_plugin)
    md.use(deflist_plugin)
    md.use(tasklists_plugin, enabled=True)
    return md


_CALLOUT_RE = re.compile(
    r'<blockquote>\s*<p>\[!(?P<type>\w+)\](?P<title>[^\n<]*)(?P<rest>.*?)</p>(?P<more>.*?)</blockquote>',
    re.DOTALL,
)


def _render_callouts(html: str) -> str:
    """Transforme les blockquotes Obsidian-style en composants editoriaux.

    - `> [!info] Titre` -> `<aside class="callout callout-info">`
    - `> [!quote] Attribution` -> `<blockquote class="pull-quote">` (pull quote editorial)
    """

    def repl(match: re.Match[str]) -> str:
        cb_type = match.group("type").lower()
        title = (match.group("title") or "").strip()
        rest = (match.group("rest") or "").strip()
        more = (match.group("more") or "").strip()

        # Pull quote (signature editoriale)
        if cb_type == "quote":
            cite_html = f'<cite>{title}</cite>' if title else ''
            body_html = f'<p>{rest}</p>' if rest else ''
            return (
                '<blockquote class="pull-quote">'
                f'{body_html}'
                f'{cite_html}'
                '</blockquote>'
            )

        # Callout standard
        cb_type = CALLOUT_ALIASES.get(cb_type, cb_type)
        if cb_type not in CALLOUT_TYPES:
            cb_type = "info"
        label = title if title else cb_type.capitalize()
        body_parts: list[str] = []
        if rest:
            body_parts.append(f"<p>{rest}</p>")
        if more:
            body_parts.append(more)
        body = "\n".join(body_parts)
        return (
            f'<aside class="callout callout-{cb_type}">'
            f'<div class="callout-label">{label}</div>'
            f'{body}'
            '</aside>'
        )

    return _CALLOUT_RE.sub(repl, html)


_HEADING_RE = re.compile(
    r'<h(?P<level>[23])[^>]*id="(?P<anchor>[^"]+)"[^>]*>(?P<inner>.*?)</h\1>',
    re.DOTALL,
)


def _extract_toc(content_html: str) -> str:
    """Genere une TOC HTML (h2/h3) a partir du HTML rendu."""
    items: list[tuple[int, str, str]] = []
    for match in _HEADING_RE.finditer(content_html):
        level = int(match.group("level"))
        anchor = match.group("anchor")
        inner = match.group("inner")
        title_text = re.sub(r'<a[^>]*class="header-anchor"[^>]*>.*?</a>', "", inner, flags=re.DOTALL)
        title_text = re.sub(r"<[^>]+>", "", title_text).strip()
        if title_text:
            items.append((level, anchor, title_text))
    if not items:
        return ""
    out = ["<ul>"]
    for level, anchor, title in items:
        cls = "toc-h2" if level == 2 else "toc-h3"
        out.append(f'<li class="{cls}"><a href="#{anchor}">{title}</a></li>')
    out.append("</ul>")
    return "\n".join(out)


def _reading_time(body: str) -> int:
    """Estime le temps de lecture en minutes (220 wpm)."""
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"[#>*_`]", "", text)
    words = len(text.split())
    return max(1, round(words / WORDS_PER_MINUTE))


def render_article_html(article: Article, body: str) -> str:
    """Rend l'article complet (HTML final) via le template Jinja2."""
    md = _build_md()
    raw_html = md.render(body)
    raw_html = _render_callouts(raw_html)
    toc_html = _extract_toc(raw_html)

    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("article.html.j2")
    return template.render(
        slug=article.slug,
        title=article.title,
        title_encoded=quote(article.title),
        lead=article.lead,
        summary=article.summary,
        primary_source=article.primary_source,
        tags=article.tags,
        sources=article.sources,
        medium_url=article.medium_url,
        date_iso=article.date_iso,
        date_human=article.date_human,
        reading_time=_reading_time(body),
        content_html=raw_html,
        toc_html=toc_html,
        prev_article=None,
        next_article=None,
    )


def write_article(article: Article, html: str) -> Path:
    """Ecrit le fichier HTML dans `articles/<slug>.html` du repo portfolio."""
    ARTICLES_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTICLES_OUT_DIR / f"{article.slug}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def collect_published_articles() -> list[Article]:
    """Retourne tous les articles dont `status: published`, tries par date desc."""
    articles: list[Article] = []
    for md_path in sorted(ARTICLES_SRC_DIR.glob("*.md")):
        try:
            article, _body = load_article(md_path)
        except Exception as exc:
            click.echo(f"warn: skip {md_path.name} ({exc})", err=True)
            continue
        if article.status == "published":
            articles.append(article)
    articles.sort(key=lambda a: a.date, reverse=True)
    return articles


def write_index_json(articles: list[Article]) -> Path:
    """Genere `articles/index.json` (liste consommee par articles.html)."""
    ARTICLES_OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload: list[dict[str, Any]] = []
    for article in articles:
        payload.append(
            {
                "slug": article.slug,
                "title": article.title,
                "date": article.date_iso,
                "date_human": article.date_human,
                "summary": article.summary,
                "lead": article.lead,
                "tags": article.tags,
                "primary_source": article.primary_source,
                "url": f"articles/{article.slug}.html",
                "reading_time": _reading_time((article.src_path.read_text() if article.src_path else "")),
            }
        )
    out = ARTICLES_OUT_DIR / "index.json"
    out.write_text(
        json.dumps({"articles": payload, "count": len(payload), "updated": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def write_feed_xml(articles: list[Article]) -> Path:
    """Genere `articles/feed.xml` (RSS 2.0)."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("feed.xml.j2")
    feed_articles = [
        {
            "slug": a.slug,
            "title": a.title,
            "summary": a.summary,
            "tags": a.tags,
            "date_rfc822": a.date_rfc822,
        }
        for a in articles
    ]
    xml = template.render(
        articles=feed_articles,
        site_url=SITE_URL,
        build_date_rfc822=format_datetime(datetime.now(timezone.utc)),
    )
    out = ARTICLES_OUT_DIR / "feed.xml"
    out.write_text(xml, encoding="utf-8")
    return out


# ============================================================
# CLI
# ============================================================

@click.group()
def cli() -> None:
    """Blog generator pour 0xmatthieu.dev."""


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def build(source: Path) -> None:
    """Convertit un fichier Markdown en HTML, ecrit dans le repo portfolio."""
    article, body = load_article(source)
    html = render_article_html(article, body)
    out = write_article(article, html)
    click.echo(f"OK {out}")
    click.echo(f"     status={article.status} reading_time={_reading_time(body)} min")


@cli.command()
def index() -> None:
    """(Re)genere index.json et feed.xml a partir des articles publies."""
    articles = collect_published_articles()
    idx = write_index_json(articles)
    feed = write_feed_xml(articles)
    click.echo(f"OK {idx}  ({len(articles)} articles)")
    click.echo(f"OK {feed}")


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def publish(source: Path) -> None:
    """Build l'article + regenere l'index + feed (mais ne push pas git)."""
    article, body = load_article(source)
    html = render_article_html(article, body)
    out = write_article(article, html)
    click.echo(f"OK article {out}")
    if article.status != "published":
        click.echo(
            f"NOTE status={article.status}, l'article ne sera pas dans index.json. "
            "Passe `status: published` dans le frontmatter pour le publier."
        )
    articles = collect_published_articles()
    idx = write_index_json(articles)
    feed = write_feed_xml(articles)
    click.echo(f"OK index   {idx}  ({len(articles)} articles)")
    click.echo(f"OK feed    {feed}")


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--message", "-m", default="", help="Message de commit custom")
def deploy(source: Path, message: str) -> None:
    """Build, marque published, regenere index+feed, git commit+push sur le repo portfolio."""
    # 1. Marque le draft comme published dans le frontmatter
    raw = source.read_text(encoding="utf-8")
    if "status: draft" in raw:
        raw = raw.replace("status: draft", "status: published", 1)
        source.write_text(raw, encoding="utf-8")
        click.echo("status: draft -> published")

    # 2. Build HTML
    article, body = load_article(source)
    html = render_article_html(article, body)
    out = write_article(article, html)
    click.echo(f"OK article {out}")

    # 3. Index + feed
    articles = collect_published_articles()
    idx = write_index_json(articles)
    feed = write_feed_xml(articles)
    click.echo(f"OK index   {idx}  ({len(articles)} articles)")
    click.echo(f"OK feed    {feed}")

    # 4. Git commit + push dans le repo portfolio
    commit_msg = message or f"article: {article.title[:72]}"
    try:
        subprocess.run(
            ["git", "add",
             str(out),
             str(idx),
             str(feed)],
            cwd=PORTFOLIO_PATH, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=PORTFOLIO_PATH, check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=PORTFOLIO_PATH, check=True, capture_output=True, text=True,
        )
        click.echo(f"OK git push  {result.stdout.strip() or 'done'}")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise click.ClickException(f"git echoue : {stderr.strip()}")

    click.echo(f"\nArticle publie : {SITE_URL}/articles/{article.slug}.html")


if __name__ == "__main__":
    cli()
