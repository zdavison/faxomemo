"""Newsstand engine v2: index recipes, browser fetch pipeline, QR print flow."""

import asyncio
import sys
import time as time_module
from pathlib import Path

import pytest

import app.newsstand_fetch as fetch
import app.newsstand_qr as qr
import app.newsstand_recipes as recipes
from app.modules import newsstand

FIXTURES = Path(__file__).resolve().parent / "newsstand_fixtures"

WSJ_SOURCE = (FIXTURES / "wsj_trimmed.recipe").read_text(encoding="utf-8")
WSJ_ISSUE_JSON = (FIXTURES / "wsj_issue.json").read_text(encoding="utf-8")
WSJ_SECTION_JSON = (FIXTURES / "wsj_section.json").read_text(encoding="utf-8")
WSJ_ARTICLE_HTML = (FIXTURES / "wsj_article.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_state():
    recipes.clear_index_cache()
    qr.clear_tokens()
    yield
    recipes.clear_index_cache()
    qr.clear_tokens()


class _FakeResponse:
    def __init__(self, body, headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.content = body
        self.text = body.decode("utf-8")
        self.request_headers = headers

    def raise_for_status(self):
        return None


# --- Loader: index recipes ---


def test_parse_index_recipe_loads_as_index_spec():
    spec = recipes.load_recipe_file(FIXTURES / "parse_index.recipe")

    assert isinstance(spec, recipes.IndexRecipeSpec)
    assert spec.title == "Index News"


def test_run_parse_index_normalizes_sections():
    spec = recipes.load_recipe_file(FIXTURES / "parse_index.recipe")

    sections = recipes.run_parse_index(spec)

    assert sections == [
        ("Section", [{"title": "A", "description": "", "url": "https://example.com/a"}])
    ]


def test_import_failure_inside_exec_raises_needs_calibre():
    source = (
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "import qt.core\n"
        "class NeedsQt(BasicNewsRecipe):\n"
        "    title = 'Needs Qt'\n"
        "    def parse_index(self):\n"
        "        return []\n"
    )
    with pytest.raises(recipes.RecipeNeedsCalibre):
        recipes.load_recipe_source(source, name="needs_qt")


def test_parse_index_failure_is_recipe_error_not_needs_calibre():
    source = (
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "class Exploding(BasicNewsRecipe):\n"
        "    title = 'Exploding'\n"
        "    def parse_index(self):\n"
        "        raise ValueError('boom')\n"
    )
    spec = recipes.load_recipe_source(source, name="exploding")

    with pytest.raises(recipes.RecipeError) as excinfo:
        recipes.run_parse_index(spec)
    assert not isinstance(excinfo.value, recipes.RecipeNeedsCalibre)
    assert "parse_index failed" in str(excinfo.value)


def test_no_calibre_stubs_left_after_index_load_and_run():
    spec = recipes.load_recipe_file(FIXTURES / "parse_index.recipe")
    recipes.run_parse_index(spec)

    assert not any(
        name == "calibre" or name.startswith("calibre.") for name in sys.modules
    )


# --- Shims ---


HEADER_RECIPE = """
from calibre.web.feeds.news import BasicNewsRecipe

class HeaderNews(BasicNewsRecipe):
    title = 'Header News'

    def get_browser(self, *args, **kw):
        br = BasicNewsRecipe.get_browser(self, *args, **kw)
        br.addheaders += [
            ('apollographql-client-name', 'wsj-mobile-android-release'),
        ]
        return br

    def parse_index(self):
        raw = self.index_to_soup('https://graphql.example/query', raw=True)
        self.log('got', len(raw), 'bytes')
        self.timefmt = ' [today]'
        return [('S', [{'title': raw.decode('utf-8'), 'url': 'https://x/a'}])]
"""


def test_index_to_soup_sends_get_browser_headers(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _FakeResponse("payload")

    monkeypatch.setattr(recipes.requests, "get", fake_get)
    spec = recipes.load_recipe_source(HEADER_RECIPE, name="header")

    sections = recipes.run_parse_index(spec)

    assert captured["url"] == "https://graphql.example/query"
    assert (
        captured["headers"].get("apollographql-client-name")
        == "wsj-mobile-android-release"
    )
    assert sections[0][1][0]["title"] == "payload"


def test_index_to_soup_returns_soup_when_not_raw(monkeypatch):
    monkeypatch.setattr(
        recipes.requests,
        "get",
        lambda url, **kw: _FakeResponse("<html><p>hi</p></html>"),
    )
    spec = recipes.load_recipe_file(FIXTURES / "parse_index.recipe")

    with recipes.calibre_shims():
        soup = spec.recipe.index_to_soup("https://x/page")

    assert soup.find("p").get_text() == "hi"


def test_recipe_specific_options_resolve_to_defaults():
    spec = recipes.load_recipe_source(WSJ_SOURCE, name="wsj_trimmed")

    assert spec.recipe.recipe_specific_options["res"] == "600"
    assert spec.recipe.recipe_specific_options.get("date") is None


def test_calibre_ebooks_beautifulsoup_maps_to_bs4():
    import bs4

    source = (
        "from calibre.ebooks.BeautifulSoup import BeautifulSoup\n"
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "class BsNews(BasicNewsRecipe):\n"
        "    title = 'Bs News'\n"
        "    soup_cls = BeautifulSoup\n"
        "    feeds = ['https://example.com/f.xml']\n"
    )
    recipes.load_recipe_source(source, name="bs_news")
    # Re-exec to grab the class: load_recipe_source only returns the spec, so
    # verify through a parse_index recipe instead.
    source_idx = source.replace(
        "    feeds = ['https://example.com/f.xml']\n",
        "    def parse_index(self):\n        return []\n",
    )
    spec = recipes.load_recipe_source(source_idx, name="bs_news_idx")
    assert type(spec.recipe).soup_cls is bs4.BeautifulSoup


# --- Index cache ---


COUNTING_RECIPE = """
from calibre.web.feeds.news import BasicNewsRecipe

CALLS = []

class CountingNews(BasicNewsRecipe):
    title = 'Counting News'

    def parse_index(self):
        CALLS.append(1)
        return [('S%d' % len(CALLS), [{'title': 'T', 'url': 'https://x/t'}])]
"""


def test_index_cache_hit_within_six_hours():
    spec = recipes.load_recipe_source(COUNTING_RECIPE, name="counting")

    first = recipes.get_index(spec, cache_key="counting", now=1000.0)
    second = recipes.get_index(spec, cache_key="counting", now=1000.0 + 3600)

    assert first == second
    assert first[0][0] == "S1"


def test_index_cache_expires_after_six_hours():
    spec = recipes.load_recipe_source(COUNTING_RECIPE, name="counting")

    recipes.get_index(spec, cache_key="counting", now=1000.0)
    later = recipes.get_index(spec, cache_key="counting", now=1000.0 + 6 * 3600 + 1)

    assert later[0][0] == "S2"


def test_clear_index_cache():
    spec = recipes.load_recipe_source(COUNTING_RECIPE, name="counting")

    recipes.get_index(spec, cache_key="counting", now=1000.0)
    recipes.clear_index_cache()
    again = recipes.get_index(spec, cache_key="counting", now=1000.0)

    assert again[0][0] == "S2"


# --- Fetch pipeline: read_url / browser ---


def test_read_url_raises_typed_error_when_playwright_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    with pytest.raises(fetch.BrowserNotInstalled) as excinfo:
        fetch.read_url("https://archive.ph/newest/https://x/a")

    assert (
        str(excinfo.value)
        == "browser not installed - use the Install browser button in newsstand settings"
    )


class _FakeSession:
    instances = []

    def __init__(self):
        _FakeSession.instances.append(self)
        self.fetches = []
        self.fail_first = False
        self.closed = False

    def fetch(self, url, timeout):
        self.fetches.append(url)
        if self.fail_first and len(self.fetches) == 1:
            raise RuntimeError("flaky")
        return "<html><p>fetched</p></html>"

    def close(self):
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch):
    _FakeSession.instances = []
    monkeypatch.setattr(fetch, "_BrowserSession", _FakeSession)
    return _FakeSession


def test_read_url_retries_once(fake_session):
    with fetch.browser_session() as session:
        session.fail_first = True
        html = fetch.read_url("https://x/a")

    assert html == "<html><p>fetched</p></html>"
    assert session.fetches == ["https://x/a", "https://x/a"]


def test_browser_session_reused_across_reads_and_closed(fake_session):
    with fetch.browser_session():
        fetch.read_url("https://x/a")
        fetch.read_url("https://x/b")

    assert len(fake_session.instances) == 1
    assert fake_session.instances[0].fetches == ["https://x/a", "https://x/b"]
    assert fake_session.instances[0].closed


# --- Fetch pipeline: fetch_article ---


PLAIN_ARTICLE_HTML = """
<html><body>
<h1>Plain Title</h1>
<div class="byline">By Sam Reporter</div>
<p>First paragraph.</p>
<p>Second paragraph.</p>
</body></html>
"""


def test_fetch_article_direct_get_for_plain_feed_source(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(PLAIN_ARTICLE_HTML)

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    spec = recipes.RecipeSpec(title="Plain", feeds=["https://x/f.xml"])

    article = fetch.fetch_article(spec, "https://x/article")

    assert calls == ["https://x/article"]
    assert article["title"] == "Plain Title"
    assert article["byline"] == "By Sam Reporter"
    assert "First paragraph." in article["paragraphs"]


def test_fetch_article_archive_mode_routes_through_read_url(monkeypatch):
    seen = []

    def fake_read_url(url, timeout=30):
        seen.append(url)
        return PLAIN_ARTICLE_HTML

    monkeypatch.setattr(fetch, "read_url", fake_read_url)
    spec = recipes.RecipeSpec(title="Plain", feeds=["https://x/f.xml"])

    article = fetch.fetch_article(spec, "https://x/article", use_archive=True)

    assert seen == ["https://archive.ph/newest/https://x/article"]
    assert article["title"] == "Plain Title"


def test_fetch_article_obfuscated_recipe_uses_read_url_shim(monkeypatch):
    seen = []

    def fake_read_url(url, timeout=30):
        seen.append(url)
        return WSJ_ARTICLE_HTML

    monkeypatch.setattr(fetch, "read_url", fake_read_url)
    spec = recipes.load_recipe_source(WSJ_SOURCE, name="wsj_trimmed")

    article = fetch.fetch_article(spec, "https://www.wsj.com/finance/markets-rally")

    assert seen == ["https://archive.is/latest/https://www.wsj.com/finance/markets-rally"]
    assert article["title"] == "Markets Rally on Rate Hopes"


def test_fetch_article_browser_missing_propagates(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    spec = recipes.load_recipe_source(WSJ_SOURCE, name="wsj_trimmed")

    with pytest.raises(fetch.BrowserNotInstalled):
        fetch.fetch_article(spec, "https://www.wsj.com/finance/markets-rally")


# --- Cleanup helpers ---


def test_cleanup_keep_only_and_remove_with_lambda_attrs():
    from bs4 import BeautifulSoup

    html = """
    <html><body>
    <div id="junk">chrome</div>
    <article style="a;article-body;b">
      <p>Keep me.</p>
      <div data-testid="ad-container">ad</div>
      <div id="comments_sector_x">comments</div>
    </article>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    cleaned = fetch.apply_cleanup(
        soup,
        keep_only_tags=[
            dict(name="article", attrs={"style": lambda x: x and "article-body" in x})
        ],
        remove_tags=[
            dict(attrs={"data-testid": "ad-container"}),
            dict(attrs={"id": lambda x: x and x.startswith("comments_sector")}),
        ],
        remove_attributes=["style"],
    )

    text = cleaned.get_text()
    assert "Keep me." in text
    assert "chrome" not in text
    assert "ad" not in text
    assert "comments" not in text
    assert not cleaned.find("article").attrs.get("style")


def test_cleanup_top_level_attribute_keys_match_calibre_semantics():
    """Calibre rules put attribute filters at the dict's top level, not only
    under 'attrs' — e.g. guardian's {'id': [...]}, {'role': [...]}. A rule
    whose filter matches nothing must remove nothing (regression: it removed
    the whole document)."""
    from bs4 import BeautifulSoup

    html = """
    <html><body>
    <nav role="navigation">menu</nav>
    <a href="#maincontent">skip</a>
    <div id="bannerandheader">banner</div>
    <article><p>Keep me.</p></article>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")

    cleaned = fetch.apply_cleanup(
        soup,
        remove_tags=[
            {"id": ["dfp-ad--survey", "bannerandheader"]},
            {"role": ["navigation", "button"]},
            {"href": ["#maincontent", "#navigation"]},
            {"for": "the-checkbox"},
            {"name": "img", "src": lambda x: x and "tracker" in x},
        ],
    )

    text = cleaned.get_text()
    assert "Keep me." in text
    assert "menu" not in text
    assert "skip" not in text
    assert "banner" not in text


def test_preprocess_html_is_called(monkeypatch):
    monkeypatch.setattr(
        fetch.requests, "get", lambda url, **kw: _FakeResponse(PLAIN_ARTICLE_HTML)
    )
    source = (
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "class Pre(BasicNewsRecipe):\n"
        "    title = 'Pre'\n"
        "    def parse_index(self):\n"
        "        return []\n"
        "    def preprocess_html(self, soup):\n"
        "        h = soup.find('h1')\n"
        "        h.string = 'Rewritten ' + self.tag_to_string(h)\n"
        "        return soup\n"
    )
    spec = recipes.load_recipe_source(source, name="pre")

    article = fetch.fetch_article(spec, "https://x/article")

    assert article["title"] == "Rewritten Plain Title"


# --- Token registry ---


def test_token_register_and_resolve():
    token = qr.register_article("https://x/a", "https://x/f.xml")

    entry = qr.resolve_token(token)
    assert entry["article_url"] == "https://x/a"
    assert entry["source_line"] == "https://x/f.xml"


def test_unknown_token_resolves_to_none():
    assert qr.resolve_token("nope") is None


def test_token_registry_evicts_oldest_beyond_cap():
    first = qr.register_article("https://x/0", "src")
    for i in range(1, qr.MAX_TOKENS + 1):
        qr.register_article(f"https://x/{i}", "src")

    assert qr.resolve_token(first) is None
    assert len(qr._tokens) == qr.MAX_TOKENS


def test_article_token_url_uses_device_ip_and_port(monkeypatch):
    monkeypatch.setattr(qr, "_local_ip", lambda: "1.2.3.4")
    monkeypatch.setenv("PORT", "8000")

    url = qr.article_token_url("https://x/a", "src")

    assert url.startswith("http://1.2.3.4:8000/a/")
    token = url.rsplit("/", 1)[-1]
    assert qr.resolve_token(token)["article_url"] == "https://x/a"


# --- /a/{token} endpoint ---


@pytest.fixture
def main_module(monkeypatch):
    import app.main as main_module

    reservations = {"begin": 0, "clear": 0, "busy": False}

    def fake_begin(**kw):
        if reservations["busy"]:
            return False
        reservations["begin"] += 1
        return True

    monkeypatch.setattr(main_module, "try_begin_print_job", fake_begin)
    monkeypatch.setattr(
        main_module,
        "clear_print_reservation",
        lambda **kw: reservations.update(clear=reservations["clear"] + 1),
    )
    return main_module, reservations


def _response_text(response):
    return bytes(response.body).decode("utf-8")


def test_qr_endpoint_unknown_token_says_receipt_too_old(main_module):
    main, _ = main_module

    response = asyncio.run(main.print_qr_article("missing-token"))

    assert response.status_code == 404
    assert "too old" in _response_text(response)


def test_qr_endpoint_prints_article(main_module, monkeypatch):
    main, reservations = main_module
    printed = []
    token = qr.register_article("https://x/a", "https://x/f.xml")
    monkeypatch.setattr(
        main, "_print_qr_article_sync", lambda entry: printed.append(entry)
    )

    response = asyncio.run(main.print_qr_article(token))

    assert response.status_code == 200
    assert "Printing" in _response_text(response)
    assert printed[0]["article_url"] == "https://x/a"
    assert reservations["begin"] == 1
    assert reservations["clear"] == 1


def test_qr_endpoint_busy_printer(main_module):
    main, reservations = main_module
    reservations["busy"] = True
    token = qr.register_article("https://x/a", "https://x/f.xml")

    response = asyncio.run(main.print_qr_article(token))

    assert response.status_code == 409
    assert "busy" in _response_text(response).lower()


def test_qr_endpoint_browser_missing_message(main_module, monkeypatch):
    main, _ = main_module
    token = qr.register_article("https://x/a", "https://x/f.xml")

    def boom(entry):
        raise fetch.BrowserNotInstalled(fetch.BROWSER_MISSING_MESSAGE)

    monkeypatch.setattr(main, "_print_qr_article_sync", boom)

    response = asyncio.run(main.print_qr_article(token))

    assert response.status_code == 500
    assert "Install browser button" in _response_text(response)


def test_qr_endpoint_archive_error_links_to_archive(main_module, monkeypatch):
    main, _ = main_module
    token = qr.register_article("https://x/a", "archive:https://x/f.xml")

    def boom(entry):
        raise fetch.ArchiveFetchError(
            "archive fetch failed", archive_url="https://archive.ph/newest/https://x/a"
        )

    monkeypatch.setattr(main, "_print_qr_article_sync", boom)

    response = asyncio.run(main.print_qr_article(token))

    assert response.status_code == 502
    assert "https://archive.ph/newest/https://x/a" in _response_text(response)


# --- Receipt rendering ---


class _RecordingPrinter:
    def __init__(self):
        self.headers = []
        self.subheaders = []
        self.captions = []
        self.bodies = []
        self.article_blocks = []
        self.line_count = 0

    def print_header(self, text, *args, **kwargs):  # noqa: ARG002
        self.headers.append(str(text))

    def print_subheader(self, text):
        self.subheaders.append(str(text))

    def print_caption(self, text):
        self.captions.append(str(text))

    def print_body(self, text):
        self.bodies.append(str(text))

    def print_line(self):
        self.line_count += 1

    def print_article_block(self, **kwargs):
        self.article_blocks.append(kwargs)


def test_receipt_index_source_prints_section_subheaders():
    printer = _RecordingPrinter()
    config = {"sources": str(FIXTURES / "parse_index.recipe"), "articles_per_source": 3}

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert printer.subheaders == ["Section"]
    assert printer.article_blocks[0]["title"] == "A"


def _five_section_recipe(tmp_path):
    source = (
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "class Many(BasicNewsRecipe):\n"
        "    title = 'Many Sections'\n"
        "    def parse_index(self):\n"
        "        return [\n"
        "            ('S%d' % i, [{'title': 'T%d' % i, 'url': 'https://x/%d'}])\n"
        "            for i in range(5)\n"
        "        ]\n"
    )
    path = tmp_path / "many.recipe"
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_show_qr_off_prints_headlines_without_qr():
    printer = _RecordingPrinter()
    config = {
        "sources": str(FIXTURES / "parse_index.recipe"),
        "articles_per_source": 3,
        "show_qr": False,
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert printer.article_blocks
    assert all(block["url"] == "" for block in printer.article_blocks)


def test_show_qr_off_registers_no_print_tokens():
    from app import newsstand_qr

    newsstand_qr.clear_tokens()
    printer = _RecordingPrinter()
    config = {
        "sources": str(FIXTURES / "parse_index.recipe"),
        "articles_per_source": 3,
        "show_qr": False,
        "qr_action": "print",
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert newsstand_qr.token_count() == 0


def test_show_qr_defaults_on():
    printer = _RecordingPrinter()
    config = {"sources": str(FIXTURES / "parse_index.recipe"), "articles_per_source": 3}

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert all(block["url"] for block in printer.article_blocks)


def test_sections_limit_caps_printed_sections(tmp_path):
    printer = _RecordingPrinter()
    config = {
        "sources": _five_section_recipe(tmp_path),
        "articles_per_source": 3,
        "sections_limit": 2,
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert printer.subheaders == ["S0", "S1"]
    assert len(printer.article_blocks) == 2


def _patch_feed(monkeypatch):
    from datetime import datetime, timezone
    from email.utils import format_datetime

    now = format_datetime(datetime.now(timezone.utc))
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>'
        "<item><title>Alpha</title><link>https://x/alpha</link>"
        f"<description>Sum</description><pubDate>{now}</pubDate></item>"
        "</channel></rss>"
    )
    monkeypatch.setattr(
        newsstand.requests, "get", lambda url, **kw: _FakeResponse(xml)
    )


def test_qr_action_print_swaps_qr_payload_for_token_url(monkeypatch):
    _patch_feed(monkeypatch)
    monkeypatch.setattr(qr, "_local_ip", lambda: "1.2.3.4")
    monkeypatch.setenv("PORT", "8000")
    printer = _RecordingPrinter()
    config = {
        "sources": "https://x/f.xml",
        "articles_per_source": 1,
        "qr_action": "print",
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    url = printer.article_blocks[0]["url"]
    assert url.startswith("http://1.2.3.4:8000/a/")
    token = url.rsplit("/", 1)[-1]
    entry = qr.resolve_token(token)
    assert entry["article_url"] == "https://x/alpha"
    assert entry["source_line"] == "https://x/f.xml"


def test_qr_action_open_is_default_article_url(monkeypatch):
    _patch_feed(monkeypatch)
    printer = _RecordingPrinter()
    config = {"sources": "https://x/f.xml", "articles_per_source": 1}

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert printer.article_blocks[0]["url"] == "https://x/alpha"


def test_archive_prefix_open_mode_qr_holds_archive_link(monkeypatch):
    _patch_feed(monkeypatch)
    printer = _RecordingPrinter()
    config = {"sources": "archive:https://x/f.xml", "articles_per_source": 1}

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert (
        printer.article_blocks[0]["url"]
        == "https://archive.ph/newest/https://x/alpha"
    )


def test_archive_prefix_print_mode_token_keeps_prefixed_source(monkeypatch):
    _patch_feed(monkeypatch)
    monkeypatch.setattr(qr, "_local_ip", lambda: "1.2.3.4")
    printer = _RecordingPrinter()
    config = {
        "sources": "archive:https://x/f.xml",
        "articles_per_source": 1,
        "qr_action": "print",
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    token = printer.article_blocks[0]["url"].rsplit("/", 1)[-1]
    assert qr.resolve_token(token)["source_line"] == "archive:https://x/f.xml"


def test_index_source_error_line_isolates_other_sources(monkeypatch, tmp_path):
    _patch_feed(monkeypatch)
    exploding = tmp_path / "exploding.recipe"
    exploding.write_text(
        "from calibre.web.feeds.news import BasicNewsRecipe\n"
        "class Exploding(BasicNewsRecipe):\n"
        "    title = 'Exploding'\n"
        "    def parse_index(self):\n"
        "        raise ValueError('boom')\n",
        encoding="utf-8",
    )
    printer = _RecordingPrinter()
    config = {
        "sources": f"{exploding}\nhttps://x/f.xml",
        "articles_per_source": 1,
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert any("parse_index failed" in body for body in printer.bodies)
    assert printer.article_blocks, "healthy source should still print"


# --- Full-article print ---


def test_print_full_article_format():
    printer = _RecordingPrinter()
    article = {
        "title": "Markets Rally",
        "byline": "Jane Doe",
        "paragraphs": ["Para one.", "Para two."],
    }

    newsstand.print_full_article(
        printer, article, source_name="The Wall Street Journal", section="Front Section"
    )

    assert printer.headers == ["The Wall Street Journal"]
    assert printer.subheaders == ["Markets Rally"]
    assert "Jane Doe" in printer.captions
    assert "Para one." in printer.bodies
    assert "Para two." in printer.bodies


def test_print_full_article_truncates_with_caption():
    printer = _RecordingPrinter()
    article = {
        "title": "Long",
        "byline": "",
        "paragraphs": ["word " * 40] * 50,
    }

    newsstand.print_full_article(
        printer, article, source_name="Src", max_print_lines=20
    )

    assert printer.captions[-1] == "[truncated]"
    assert len(printer.bodies) < 50


# --- wsj_trimmed end to end ---


def _wsj_fake_get(url, headers=None, **kwargs):
    assert (headers or {}).get("apollographql-client-name") == (
        "wsj-mobile-android-release"
    )
    if "IssueQuery" in url:
        return _FakeResponse(WSJ_ISSUE_JSON)
    if "SectionQuery" in url:
        return _FakeResponse(WSJ_SECTION_JSON)
    raise AssertionError(f"unexpected URL {url}")


def test_wsj_trimmed_end_to_end(monkeypatch):
    monkeypatch.setattr(recipes.requests, "get", _wsj_fake_get)
    monkeypatch.setattr(time_module, "sleep", lambda s: None)
    monkeypatch.setattr(
        fetch, "read_url", lambda url, timeout=30: WSJ_ARTICLE_HTML
    )

    spec = recipes.load_recipe_source(WSJ_SOURCE, name="wsj_trimmed")
    assert isinstance(spec, recipes.IndexRecipeSpec)
    assert spec.title == "The Wall Street Journal"

    sections = recipes.run_parse_index(spec)

    assert [s for s, _ in sections] == [
        "Front Section",
        "Business & Finance",
        "Opinion",
    ]
    front = sections[0][1]
    assert front[0] == {
        "title": "Markets Rally on Rate Hopes",
        "description": "Stocks climbed as traders bet on cuts.",
        "url": "https://www.wsj.com/finance/markets-rally",
    }
    assert front[1]["description"] == ""

    article = fetch.fetch_article(spec, front[0]["url"])

    assert article["title"] == "Markets Rally on Rate Hopes"
    assert article["byline"] == "Jane Doe"
    joined = "\n".join(article["paragraphs"])
    assert "Stocks climbed on Wednesday" in joined
    assert "S&P 500 rose 1.2%" in joined
    assert "Fed officials signaled patience" in joined
    assert "ADVERTISEMENT" not in joined
    assert "Comment thread junk" not in joined
    assert "What to Read Next" not in joined


def test_wsj_receipt_digest_with_sections(monkeypatch, tmp_path):
    monkeypatch.setattr(recipes.requests, "get", _wsj_fake_get)
    monkeypatch.setattr(time_module, "sleep", lambda s: None)
    path = tmp_path / "wsj_trimmed.recipe"
    path.write_text(WSJ_SOURCE, encoding="utf-8")

    printer = _RecordingPrinter()
    config = {
        "sources": str(path),
        "articles_per_source": 1,
        "sections_limit": 2,
    }

    newsstand.format_newsstand_receipt(printer, config, "NEWSSTAND")

    assert printer.subheaders == ["Front Section", "Business & Finance"]
    assert len(printer.article_blocks) == 2
    assert printer.article_blocks[0]["title"] == "Markets Rally on Rate Hopes"


# --- Config schema ---


def test_config_schema_accepts_and_rejects_v2_fields():
    from app.module_registry import validate_module_config

    validate_module_config(
        "newsstand",
        {
            "sources": "guardian",
            "articles_per_source": 3,
            "qr_action": "print",
            "sections_limit": 3,
        },
    )
    with pytest.raises(ValueError):
        validate_module_config("newsstand", {"qr_action": "bogus"})
    with pytest.raises(ValueError):
        validate_module_config("newsstand", {"sections_limit": 0})
