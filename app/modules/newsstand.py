import calendar as _calendar
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import requests

from app.config import format_print_datetime
from app.module_registry import register_module
from app.modules.news import clean_url
from app.modules.rss import MAX_FEED_SIZE, clean_text
from app.newsstand_recipes import (
    RecipeError,
    RecipeNeedsCalibre,
    RecipeSpec,
    resolve_source,
    source_display_name,
)

SOURCE_TIMEOUT_SECONDS = 10
_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


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


def _fetch_one_source(line: str, articles_per_source: int) -> Dict[str, Any]:
    spec = resolve_source(line)
    name, articles = fetch_source_articles(spec, articles_per_source)
    return {"name": name, "articles": articles}


def _error_result(line: str, exc: Exception) -> Dict[str, Any]:
    name = source_display_name(line)
    if isinstance(exc, RecipeError):
        name = exc.display_name or name
    if isinstance(exc, RecipeNeedsCalibre):
        message = "recipe needs calibre (parse_index)"
    elif isinstance(exc, RecipeError):
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

    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(lines), 8)) as pool:
        futures = [
            pool.submit(_fetch_one_source, line, articles_per_source) for line in lines
        ]
        for line, future in zip(lines, futures):
            try:
                # Network calls inside the worker carry their own 10-second
                # timeouts, so this wait is bounded even on timeout.
                results.append(future.result(timeout=SOURCE_TIMEOUT_SECONDS + 5))
            except Exception as exc:
                results.append(_error_result(line, exc))
    return results


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
        },
    },
    ui_schema={
        "sources": {
            "ui:widget": "textarea",
            "ui:placeholder": "guardian\n/home/pi/recipes/taz.recipe\nhttps://example.com/rss.xml",
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

    for i, result in enumerate(results):
        if "error" in result:
            printer.print_body(f"{result['name']}: {result['error']}")
        elif not result["articles"]:
            printer.print_body(f"{result['name']}: no recent articles")
        else:
            for article in result["articles"]:
                printer.print_article_block(
                    source=article["source"],
                    title=article["title"],
                    summary=article.get("summary", ""),
                    url=clean_url(article.get("url", "")),
                    qr_size=160,
                    title_width=24,
                    summary_width=28,
                    max_summary_lines=2,
                )
        if i < len(results) - 1:
            printer.print_line()
