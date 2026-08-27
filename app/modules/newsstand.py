import calendar as _calendar
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import requests

from app import newsstand_qr
from app.config import format_print_datetime
from app.module_registry import register_module
from app.modules.news import clean_url
from app.modules.rss import MAX_FEED_SIZE, clean_text
from app.newsstand_fetch import ARCHIVE_URL_PREFIX
from app.newsstand_recipes import (
    IndexRecipeSpec,
    RecipeError,
    RecipeSpec,
    classify_source,
    get_index,
    resolve_source,
    source_display_name,
)

SOURCE_TIMEOUT_SECONDS = 10
# Index recipes legitimately take minutes on a cache miss (wsj_free sleeps 3s
# per section); only plain feed URLs keep the short wait.
INDEX_SOURCE_TIMEOUT_SECONDS = 300
ARCHIVE_SOURCE_PREFIX = "archive:"
_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


def split_archive_prefix(line: str) -> Tuple[bool, str]:
    """Split an "archive:<source>" line into (is_archive, bare source)."""
    if line.startswith(ARCHIVE_SOURCE_PREFIX):
        return True, line[len(ARCHIVE_SOURCE_PREFIX):].strip()
    return False, line


def _entry_datetime(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime.fromtimestamp(_calendar.timegm(parsed), tz=timezone.utc)
            except (ValueError, OverflowError, TypeError):
                continue
    return None


def _fetch_feed(url: str):
    response = requests.get(
        url,
        headers={"User-Agent": "Paper Console/1"},
        timeout=SOURCE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return feedparser.parse(response.content[:MAX_FEED_SIZE])


def fetch_source_articles(
    spec: RecipeSpec,
    articles_per_source: int,
    now: Optional[datetime] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Fetch one recipe's feeds and return (source name, top articles)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=spec.oldest_article)
    name = spec.title
    articles: List[Dict[str, Any]] = []
    failures = 0
    last_error: Optional[Exception] = None
    seen_keys = set()

    for feed_url in spec.feeds:
        # Builtin recipes often carry stale feed lists; skip dead feeds and
        # only fail the source when every feed is unreachable.
        try:
            feed = _fetch_feed(feed_url)
        except Exception as exc:
            failures += 1
            last_error = exc
            continue
        if not name:
            name = clean_text(feed.feed.get("title", "")) or feed_url
        taken = 0
        for entry in feed.entries:
            if taken >= spec.max_articles_per_feed:
                break
            published = _entry_datetime(entry)
            if published is not None and published < cutoff:
                continue
            # Section feeds in the same recipe often repeat the index feed's
            # entries; keep the first occurrence only.
            key = entry.get("link") or entry.get("title")
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            articles.append(
                {
                    "source": name,
                    "title": clean_text(entry.get("title", "No Title")),
                    "summary": clean_text(
                        entry.get("summary", entry.get("description", ""))
                    ),
                    "url": entry.get("link", ""),
                    "published": published,
                }
            )
            taken += 1

    if spec.feeds and failures == len(spec.feeds):
        raise last_error

    articles.sort(key=lambda a: a["published"] or _EPOCH, reverse=True)
    return name or "Feed", articles[:articles_per_source]


def _fetch_one_source(
    line: str, articles_per_source: int, sections_limit: int = 3
) -> Dict[str, Any]:
    archive, bare = split_archive_prefix(line)
    spec = resolve_source(bare)
    if isinstance(spec, IndexRecipeSpec):
        sections = get_index(spec, cache_key=bare)
        out_sections = [
            {
                "name": clean_text(section),
                "articles": [
                    {
                        "title": clean_text(article["title"]),
                        "summary": clean_text(article["description"]),
                        "url": article["url"],
                    }
                    for article in articles[:articles_per_source]
                ],
            }
            for section, articles in sections[:sections_limit]
        ]
        return {
            "name": spec.title or spec.name,
            "sections": out_sections,
            "archive": archive,
            "source_line": line,
        }
    name, articles = fetch_source_articles(spec, articles_per_source)
    return {"name": name, "articles": articles, "archive": archive, "source_line": line}


def _error_result(line: str, exc: Exception) -> Dict[str, Any]:
    name = source_display_name(line)
    if isinstance(exc, RecipeError):
        name = exc.display_name or name
    if isinstance(exc, RecipeError):
        message = str(exc)
    elif isinstance(exc, concurrent.futures.TimeoutError):
        message = "timed out"
    else:
        message = "unavailable"
    return {"name": name, "error": message}


def get_newsstand_results(config: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Resolve and fetch every configured source, concurrently.

    Returns one dict per source line, either {"name", "articles"} or
    {"name", "error"}.
    """
    if config is None:
        config = {}

    lines = [
        line.strip()
        for line in str(config.get("sources", "")).splitlines()
        if line.strip()
    ]
    if not lines:
        return []

    try:
        articles_per_source = int(config.get("articles_per_source", 3))
    except (TypeError, ValueError):
        articles_per_source = 3
    articles_per_source = max(1, min(articles_per_source, 10))

    try:
        sections_limit = int(config.get("sections_limit", 3))
    except (TypeError, ValueError):
        sections_limit = 3
    sections_limit = max(1, min(sections_limit, 10))

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(lines), 8)) as pool:
        futures = [
            pool.submit(_fetch_one_source, line, articles_per_source, sections_limit)
            for line in lines
        ]
        for line, future in zip(lines, futures):
            kind = classify_source(split_archive_prefix(line)[1])
            wait = (
                SOURCE_TIMEOUT_SECONDS + 5
                if kind == "url"
                else INDEX_SOURCE_TIMEOUT_SECONDS
            )
            try:
                results.append(future.result(timeout=wait))
            except Exception as exc:
                results.append(_error_result(line, exc))
    return results


def _article_qr_url(
    article_url: str, result: Dict[str, Any], qr_action: str, section: str = ""
) -> str:
    if qr_action == "print":
        return newsstand_qr.article_token_url(
            article_url,
            result.get("source_line", ""),
            source_name=result.get("name", ""),
            section=section,
        )
    if result.get("archive"):
        return ARCHIVE_URL_PREFIX + article_url
    return clean_url(article_url)


def print_full_article(
    printer,
    article: Dict[str, Any],
    source_name: str = "",
    section: str = "",
    max_print_lines: int = 200,
):
    """Print one fetched article in full (QR print flow)."""
    printer.print_header(source_name or "NEWSSTAND", icon="newspaper")
    caption = format_print_datetime()
    if section:
        caption = f"{section} - {caption}"
    printer.print_caption(caption)
    printer.print_line()

    if article.get("title"):
        printer.print_subheader(article["title"])
    if article.get("byline"):
        printer.print_caption(article["byline"])

    # The driver truncates the buffered job too, but stop feeding paragraphs
    # early so a book-length article never renders a giant bitmap.
    body_width = 32
    lines_used = 8
    for paragraph in article.get("paragraphs", []):
        lines_used += max(1, -(-len(paragraph) // body_width)) + 1
        if max_print_lines and lines_used > max_print_lines:
            printer.print_caption("[truncated]")
            break
        printer.print_body(paragraph)


@register_module(
    type_id="newsstand",
    label="Newsstand",
    description="Top articles from calibre news recipes and feed URLs",
    icon="newspaper",
    offline=False,
    category="content",
    config_schema={
        "type": "object",
        "properties": {
            "sources": {
                "type": "string",
                "title": "Sources",
                "description": (
                    "One source per line: a builtin calibre recipe name "
                    "(example: guardian), a path or URL to a .recipe file, "
                    "or a feed URL"
                ),
                "default": "",
            },
            "articles_per_source": {
                "type": "integer",
                "title": "Articles per Source",
                "default": 3,
                "minimum": 1,
                "maximum": 10,
            },
            "sections_limit": {
                "type": "integer",
                "title": "Sections per Index Recipe",
                "description": (
                    "For recipes that build their edition in code (like "
                    "wsj_free): how many sections the digest includes. A "
                    "full edition can take minutes to crawl."
                ),
                "default": 3,
                "minimum": 1,
                "maximum": 10,
            },
            "qr_action": {
                "type": "string",
                "title": "QR Action",
                "description": (
                    "open: the QR holds the article link for your phone. "
                    "print: the QR makes this device fetch and print the "
                    "full article (phone must be on the same network)."
                ),
                "enum": ["open", "print"],
                "default": "open",
            },
            "browser_setup": {
                "type": "null",
                "title": "Install browser",
                "description": (
                    "Recipes that fetch through archive.today, such as "
                    "wsj_free, need a headless browser. Downloads roughly "
                    "300 MB the first time."
                ),
            },
        },
    },
    ui_schema={
        "sources": {
            "ui:widget": "textarea",
            "ui:placeholder": "guardian\nwsj_free\narchive:https://example.com/rss.xml",
        },
        "browser_setup": {
            "ui:widget": "action-button",
            "ui:options": {
                "action": "install_browser",
                "label": "Install browser",
            },
        },
    },
)
def format_newsstand_receipt(
    printer, config: Dict[str, Any] = None, module_name: str = None
):
    """Compiles and prints the Newsstand receipt."""
    results = get_newsstand_results(config)

    printer.print_header(module_name or "NEWSSTAND", icon="newspaper")
    printer.print_caption(format_print_datetime())
    printer.print_line()

    if not results:
        printer.print_body("No sources configured.")
        printer.print_caption(
            "Add recipe names, .recipe paths, or feed URLs in settings."
        )
        return

    qr_action = str(config.get("qr_action", "open") if config else "open")

    def _print_block(article: Dict[str, Any], result, source: str, section: str = ""):
        printer.print_article_block(
            source=source,
            title=article["title"],
            summary=article.get("summary", ""),
            url=_article_qr_url(article.get("url", ""), result, qr_action, section),
            qr_size=160,
            title_width=24,
            summary_width=28,
            max_summary_lines=2,
        )

    for i, result in enumerate(results):
        if "error" in result:
            printer.print_body(f"{result['name']}: {result['error']}")
        elif "sections" in result:
            if not result["sections"]:
                printer.print_body(f"{result['name']}: no sections in the edition")
            for section in result["sections"]:
                printer.print_subheader(section["name"])
                for article in section["articles"]:
                    _print_block(article, result, result["name"], section["name"])
        elif not result["articles"]:
            printer.print_body(f"{result['name']}: no recent articles")
        else:
            for article in result["articles"]:
                _print_block(article, result, article["source"])
        if i < len(results) - 1:
            printer.print_line()
