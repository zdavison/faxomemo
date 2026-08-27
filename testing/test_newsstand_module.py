"""Tests for the Newsstand module and its calibre-recipe loader."""

import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

import app.newsstand_recipes as recipes
from app.modules import newsstand

FIXTURES = Path(__file__).resolve().parent / "newsstand_fixtures"

SIMPLE_RECIPE_SOURCE = (FIXTURES / "simple.recipe").read_text(encoding="utf-8")


# --- Recipe loader ---


def test_loader_parses_simple_feeds_recipe():
    spec = recipes.load_recipe_file(FIXTURES / "simple.recipe")

    assert spec.title == "Simple News"
    assert spec.feeds == [
        "https://example.com/front.xml",
        "https://example.com/all.xml",
    ]
    assert spec.oldest_article == 2
    assert spec.max_articles_per_feed == 5


def test_loader_applies_calibre_defaults():
    source = (
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "class Minimal(BasicNewsRecipe):\n"
        "    title = 'Minimal'\n"
        "    feeds = ['https://example.com/feed.xml']\n"
    )
    spec = recipes.load_recipe_source(source, name="minimal")

    assert spec.oldest_article == 7
    assert spec.max_articles_per_feed == 100


def test_loader_rejects_parse_index_recipe():
    with pytest.raises(recipes.RecipeNeedsCalibre):
        recipes.load_recipe_file(FIXTURES / "parse_index.recipe")


def test_loader_reports_broken_recipe():
    with pytest.raises(recipes.RecipeError) as excinfo:
        recipes.load_recipe_file(FIXTURES / "broken.recipe")
    assert not isinstance(excinfo.value, recipes.RecipeNeedsCalibre)


def test_loader_does_not_leave_calibre_stubs_in_sys_modules():
    import sys

    recipes.load_recipe_source(SIMPLE_RECIPE_SOURCE, name="simple")
    assert "calibre" not in sys.modules
    assert "calibre.web.feeds.news" not in sys.modules


# --- Source classification ---


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("guardian", "builtin"),
        ("le_monde", "builtin"),
        ("https://example.com/rss.xml", "url"),
        ("http://example.com/feed", "url"),
        ("https://raw.githubusercontent.com/z/my-recipes/main/berliner.recipe", "recipe_url"),
        ("http://example.com/foo.recipe", "recipe_url"),
        ("https://example.com/foo.recipe?ref=main", "recipe_url"),
        ("/home/z/recipes/taz.recipe", "path"),
        ("recipes/taz.recipe", "path"),
        ("taz.recipe", "path"),
    ],
)
def test_classify_source(line, expected):
    assert recipes.classify_source(line) == expected


# --- Source resolution ---


def test_path_source_loads_local_recipe():
    spec = recipes.resolve_source(str(FIXTURES / "simple.recipe"))
    assert spec.title == "Simple News"


def test_url_source_becomes_one_feed_recipe():
    spec = recipes.resolve_source("https://example.com/rss.xml")
    assert spec.title is None
    assert spec.feeds == ["https://example.com/rss.xml"]


def test_missing_path_raises_recipe_error():
    with pytest.raises(recipes.RecipeError):
        recipes.resolve_source("/nonexistent/nowhere.recipe")


class _FakeRecipeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_builtin_recipe_downloads_once_and_caches(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeRecipeResponse(SIMPLE_RECIPE_SOURCE)

    monkeypatch.setattr(recipes.requests, "get", fake_get)

    spec_one = recipes.resolve_source("simple", cache_dir=tmp_path)
    spec_two = recipes.resolve_source("simple", cache_dir=tmp_path)

    assert len(calls) == 1
    assert "recipes/simple.recipe" in calls[0]
    assert spec_one.title == spec_two.title == "Simple News"


def test_recipe_url_downloads_once_and_caches(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeRecipeResponse(SIMPLE_RECIPE_SOURCE)

    monkeypatch.setattr(recipes.requests, "get", fake_get)
    url = "https://raw.githubusercontent.com/z/my-recipes/main/berliner.recipe"

    spec_one = recipes.resolve_source(url, cache_dir=tmp_path)
    spec_two = recipes.resolve_source(url, cache_dir=tmp_path)

    assert calls == [url]
    assert spec_one.title == spec_two.title == "Simple News"


def test_recipe_url_cache_does_not_collide_on_same_filename(tmp_path, monkeypatch):
    sources = {
        "https://a.example/taz.recipe": SIMPLE_RECIPE_SOURCE,
        "https://b.example/taz.recipe": SIMPLE_RECIPE_SOURCE.replace(
            "Simple News", "Other News"
        ),
    }

    def fake_get(url, **kwargs):
        return _FakeRecipeResponse(sources[url])

    monkeypatch.setattr(recipes.requests, "get", fake_get)

    spec_a = recipes.resolve_source("https://a.example/taz.recipe", cache_dir=tmp_path)
    spec_b = recipes.resolve_source("https://b.example/taz.recipe", cache_dir=tmp_path)

    assert spec_a.title == "Simple News"
    assert spec_b.title == "Other News"


def test_recipe_url_display_name_is_file_stem():
    name = recipes.source_display_name("https://a.example/recipes/berliner.recipe")
    assert name == "berliner"


def test_builtin_recipe_cache_refreshes_after_seven_days(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeRecipeResponse(SIMPLE_RECIPE_SOURCE)

    monkeypatch.setattr(recipes.requests, "get", fake_get)

    recipes.resolve_source("simple", cache_dir=tmp_path)
    stale = time.time() - 8 * 24 * 3600
    os.utime(tmp_path / "simple.recipe", (stale, stale))
    recipes.resolve_source("simple", cache_dir=tmp_path)

    assert len(calls) == 2


# --- Feed fetching ---


def _rss_xml(feed_title, items):
    entries = []
    for title, published in items:
        entries.append(
            "<item>"
            f"<title>{title}</title>"
            f"<link>https://example.com/{title.lower()}</link>"
            f"<description>Summary of {title}</description>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            "</item>"
        )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>{feed_title}</title>{''.join(entries)}"
        "</channel></rss>"
    )


class _FakeFeedResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def _patch_feed(monkeypatch, xml_by_url):
    def fake_get(url, **kwargs):
        return _FakeFeedResponse(xml_by_url[url].encode("utf-8"))

    monkeypatch.setattr(newsstand.requests, "get", fake_get)


def test_fetch_drops_articles_older_than_oldest_article(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml(
        "Feed",
        [
            ("Fresh", now - timedelta(days=1)),
            ("Stale", now - timedelta(days=10)),
            ("Recent", now - timedelta(days=2)),
        ],
    )
    _patch_feed(monkeypatch, {"https://x/f.xml": xml})
    spec = recipes.RecipeSpec(title="Src", feeds=["https://x/f.xml"], oldest_article=7)

    name, articles = newsstand.fetch_source_articles(spec, articles_per_source=10)

    assert name == "Src"
    assert [a["title"] for a in articles] == ["Fresh", "Recent"]


def test_fetch_limits_to_articles_per_source_newest_first(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml(
        "Feed",
        [
            ("Second", now - timedelta(hours=2)),
            ("First", now - timedelta(hours=1)),
            ("Third", now - timedelta(hours=3)),
        ],
    )
    _patch_feed(monkeypatch, {"https://x/f.xml": xml})
    spec = recipes.RecipeSpec(title="Src", feeds=["https://x/f.xml"])

    _, articles = newsstand.fetch_source_articles(spec, articles_per_source=2)

    assert [a["title"] for a in articles] == ["First", "Second"]


def test_fetch_respects_max_articles_per_feed(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml(
        "Feed",
        [("One", now - timedelta(hours=1)), ("Two", now - timedelta(hours=2))],
    )
    _patch_feed(monkeypatch, {"https://x/f.xml": xml})
    spec = recipes.RecipeSpec(
        title="Src", feeds=["https://x/f.xml"], max_articles_per_feed=1
    )

    _, articles = newsstand.fetch_source_articles(spec, articles_per_source=10)

    assert [a["title"] for a in articles] == ["One"]


def test_fetch_skips_broken_feed_but_keeps_working_feeds(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml("Feed", [("Alive", now - timedelta(hours=1))])

    def fake_get(url, **kwargs):
        if url == "https://x/dead.xml":
            raise newsstand.requests.exceptions.HTTPError("404")
        return _FakeFeedResponse(xml.encode("utf-8"))

    monkeypatch.setattr(newsstand.requests, "get", fake_get)
    spec = recipes.RecipeSpec(
        title="Src", feeds=["https://x/dead.xml", "https://x/live.xml"]
    )

    _, articles = newsstand.fetch_source_articles(spec, articles_per_source=3)

    assert [a["title"] for a in articles] == ["Alive"]


def test_fetch_raises_when_all_feeds_fail(monkeypatch):
    def fake_get(url, **kwargs):
        raise newsstand.requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(newsstand.requests, "get", fake_get)
    spec = recipes.RecipeSpec(title="Src", feeds=["https://x/a.xml", "https://x/b.xml"])

    with pytest.raises(Exception):
        newsstand.fetch_source_articles(spec, articles_per_source=3)


def test_fetch_dedupes_same_article_across_feeds(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml("Feed", [("Shared", now - timedelta(hours=1))])
    _patch_feed(
        monkeypatch, {"https://x/a.xml": xml, "https://x/b.xml": xml}
    )
    spec = recipes.RecipeSpec(
        title="Src", feeds=["https://x/a.xml", "https://x/b.xml"]
    )

    _, articles = newsstand.fetch_source_articles(spec, articles_per_source=10)

    assert [a["title"] for a in articles] == ["Shared"]


def test_fetch_url_source_takes_name_from_feed_title(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml("Example Bulletin", [("Item", now - timedelta(hours=1))])
    _patch_feed(monkeypatch, {"https://x/f.xml": xml})
    spec = recipes.RecipeSpec(title=None, feeds=["https://x/f.xml"])

    name, articles = newsstand.fetch_source_articles(spec, articles_per_source=3)

    assert name == "Example Bulletin"
    assert articles[0]["source"] == "Example Bulletin"


# --- Receipt rendering ---


class _RecordingPrinter:
    def __init__(self):
        self.headers = []
        self.captions = []
        self.bodies = []
        self.article_blocks = []
        self.line_count = 0

    def print_header(self, text, *args, **kwargs):  # noqa: ARG002
        self.headers.append(str(text))

    def print_caption(self, text):
        self.captions.append(str(text))

    def print_body(self, text):
        self.bodies.append(str(text))

    def print_line(self):
        self.line_count += 1

    def print_article_block(self, **kwargs):
        self.article_blocks.append(kwargs)


def test_receipt_renders_articles_for_each_source(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml(
        "Feed",
        [("Alpha", now - timedelta(hours=1)), ("Beta", now - timedelta(hours=2))],
    )
    monkeypatch.setattr(
        newsstand.requests,
        "get",
        lambda url, **kwargs: _FakeFeedResponse(xml.encode("utf-8")),
    )

    printer = _RecordingPrinter()
    config = {
        "sources": "https://x/one.xml\nhttps://x/two.xml",
        "articles_per_source": 1,
    }
    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert printer.headers == ["NEWSSTAND"]
    assert len(printer.article_blocks) == 2
    assert printer.article_blocks[0]["title"] == "Alpha"
    # Header divider plus one divider between the two sources.
    assert printer.line_count == 2


def test_receipt_partial_failure_prints_error_line_and_other_sources(monkeypatch):
    now = datetime.now(timezone.utc)
    xml = _rss_xml("Feed", [("Alpha", now - timedelta(hours=1))])
    monkeypatch.setattr(
        newsstand.requests,
        "get",
        lambda url, **kwargs: _FakeFeedResponse(xml.encode("utf-8")),
    )

    printer = _RecordingPrinter()
    sources = "\n".join(
        [str(FIXTURES / "simple.recipe"), str(FIXTURES / "parse_index.recipe")]
    )
    newsstand.format_newsstand_receipt(
        printer, {"sources": sources, "articles_per_source": 2}, "NEWSSTAND"
    )

    assert any(
        body == "Index News: recipe needs calibre (parse_index)"
        for body in printer.bodies
    )
    assert printer.article_blocks, "healthy source should still print articles"


def test_receipt_without_sources_prints_hint():
    printer = _RecordingPrinter()

    newsstand.format_newsstand_receipt(printer, {"sources": ""}, "NEWSSTAND")

    assert "No sources configured." in printer.bodies
    assert not printer.article_blocks


def test_config_schema_validates():
    from app.module_registry import validate_module_config

    validate_module_config(
        "newsstand", {"sources": "guardian", "articles_per_source": 3}
    )
    with pytest.raises(ValueError):
        validate_module_config("newsstand", {"articles_per_source": 99})
