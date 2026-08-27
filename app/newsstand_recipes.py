"""Calibre-recipe loader and source resolver for the Newsstand module.

A .recipe file is Python source defining a BasicNewsRecipe subclass. This
module executes recipes against a small stub of calibre's recipe API and
extracts only the feed list and limits - enough to print headlines without
installing calibre itself.
"""

import hashlib
import importlib.abc
import importlib.machinery
import logging
import sys
import time
import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

RECIPE_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
INDEX_TO_SOUP_TIMEOUT_SECONDS = 30
INDEX_CACHE_SECONDS = 6 * 3600

BUILTIN_RECIPE_URL = (
    "https://raw.githubusercontent.com/kovidgoyal/calibre/master/recipes/{name}.recipe"
)
CACHE_DIR = Path(__file__).resolve().parent / "data" / "newsstand_cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
DOWNLOAD_TIMEOUT_SECONDS = 10


class RecipeError(Exception):
    """A source line could not be resolved to a usable recipe."""

    def __init__(self, message: str, display_name: Optional[str] = None):
        super().__init__(message)
        self.display_name = display_name


class RecipeNeedsCalibre(RecipeError):
    """The recipe imports things even our calibre shims cannot provide."""


class _RecipeLog:
    """Callable logger matching calibre's self.log (also has level methods)."""

    def __call__(self, *args, **kwargs):
        logger.info("recipe: %s", " ".join(str(a) for a in args))

    def debug(self, *args, **kwargs):
        self(*args, **kwargs)

    info = warn = warning = error = exception = debug


class _StubBrowser:
    """Minimal mechanize-Browser stand-in: holds addheaders, supports open()."""

    def __init__(self):
        self.addheaders: List[Tuple[str, str]] = [("User-Agent", RECIPE_USER_AGENT)]

    def open(self, url, timeout: Optional[float] = None):
        response = requests.get(
            url,
            headers=dict(self.addheaders),
            timeout=timeout or INDEX_TO_SOUP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        class _Result:
            def __init__(self, content):
                self._content = content

            def read(self):
                return self._content

        return _Result(response.content)

    def __getattr__(self, name):
        # mechanize has dozens of configuration methods recipes may call.
        return lambda *args, **kwargs: None


class BasicNewsRecipe:
    """Stand-in for calibre's BasicNewsRecipe.

    Feed-only recipes are read via class attributes without instantiation.
    Index recipes are instantiated and their parse_index / preprocess_html /
    get_obfuscated_article run against the live shim methods below.
    """

    title: Optional[str] = None
    feeds: Optional[list] = None
    oldest_article: float = 7
    max_articles_per_feed: int = 100
    articles_are_obfuscated: bool = False
    keep_only_tags: list = []
    remove_tags: list = []
    remove_attributes: list = []
    recipe_specific_options: dict = {}
    timefmt: str = ""
    timeout: float = INDEX_TO_SOUP_TIMEOUT_SECONDS

    def __init__(self):
        self.log = _RecipeLog()
        options: Dict[str, Any] = {}
        for key, value in (type(self).recipe_specific_options or {}).items():
            options[key] = value.get("default") if isinstance(value, dict) else None
        self.recipe_specific_options = options
        self._browser_cache = None

    def get_browser(self, *args, **kwargs):
        return _StubBrowser()

    def browser(self):
        if self._browser_cache is None:
            self._browser_cache = self.get_browser()
        return self._browser_cache

    def index_to_soup(self, url, raw: bool = False, **kwargs):
        response = requests.get(
            url,
            headers=dict(self.browser().addheaders),
            timeout=INDEX_TO_SOUP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        if raw:
            return response.content
        from bs4 import BeautifulSoup

        return BeautifulSoup(response.content, "html.parser")

    def tag_to_string(self, tag, *args, **kwargs) -> str:
        if tag is None:
            return ""
        if hasattr(tag, "get_text"):
            return tag.get_text(separator=" ", strip=True)
        return str(tag)

    def cleanup(self):
        pass


class _CalibreStubValue:
    """Permissive placeholder for calibre helpers recipes may import or call."""

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        return self


def _read_url_shim(storage, url=None, timeout: float = 60):
    """Shim for calibre.scraper.simple.read_url (Playwright-backed here)."""
    if url is None:
        storage, url = [], storage
    from app.newsstand_fetch import read_url

    return read_url(url)


class _CalibreStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Creates a permissive stub module for calibre and any calibre.* import.

    Known modules get real shims (BasicNewsRecipe, bs4's BeautifulSoup, the
    Playwright-backed read_url); everything else resolves attributes to a
    permissive stub value so feed-only recipes that import extras still load.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "calibre" or fullname.startswith("calibre."):
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        module = types.ModuleType(spec.name)
        module.__path__ = []
        stub = _CalibreStubValue()
        module.__dict__["__getattr__"] = lambda _name: stub
        return module

    def exec_module(self, module):
        name = module.__name__
        if name == "calibre.web.feeds.news":
            module.BasicNewsRecipe = BasicNewsRecipe
        elif name == "calibre.ebooks.BeautifulSoup":
            from bs4 import BeautifulSoup

            module.BeautifulSoup = BeautifulSoup
        elif name == "calibre.scraper.simple":
            module.read_url = _read_url_shim


_STUB_FINDER = _CalibreStubFinder()


@contextmanager
def calibre_shims():
    """Make calibre.* imports resolve to shims for the duration of the block.

    Reentrant: nested blocks leave installation and cleanup to the outermost
    one. Any calibre modules created inside the block are removed on exit so
    stubs never leak into sys.modules.
    """
    installed = _STUB_FINDER not in sys.meta_path
    if installed:
        sys.meta_path.insert(0, _STUB_FINDER)
    before = {
        name
        for name in sys.modules
        if name == "calibre" or name.startswith("calibre.")
    }
    try:
        yield
    finally:
        if installed:
            sys.meta_path.remove(_STUB_FINDER)
            for name in [
                name
                for name in list(sys.modules)
                if (name == "calibre" or name.startswith("calibre."))
                and name not in before
            ]:
                sys.modules.pop(name, None)


@dataclass
class RecipeSpec:
    """The subset of a feed-based recipe the Newsstand module needs."""

    title: Optional[str]
    feeds: List[str]
    oldest_article: float = 7
    max_articles_per_feed: int = 100


@dataclass
class IndexRecipeSpec:
    """A recipe that builds its article list in code via parse_index().

    Holds a live instance so parse_index, preprocess_html and
    get_obfuscated_article can run against the shims in this module.
    """

    title: Optional[str]
    recipe: Any
    name: str = ""


def classify_source(line: str) -> str:
    """Classify a source line as "url", "recipe_url", "path", or "builtin" name."""
    s = line.strip()
    if s.lower().startswith("http"):
        if urlparse(s).path.endswith(".recipe"):
            return "recipe_url"
        return "url"
    if "/" in s or s.endswith(".recipe"):
        return "path"
    return "builtin"


def source_display_name(line: str) -> str:
    """Best-effort human name for a source line, used in error messages."""
    s = line.strip()
    kind = classify_source(s)
    if kind == "recipe_url":
        return Path(urlparse(s).path).stem
    if kind == "url":
        return urlparse(s).netloc or s
    if kind == "path":
        return Path(s).stem
    return s


def _overrides_parse_index(cls: type) -> bool:
    return any(
        "parse_index" in klass.__dict__
        for klass in cls.__mro__
        if klass not in (BasicNewsRecipe, object)
    )


def _normalize_feeds(feeds, name: str) -> List[str]:
    urls: List[str] = []
    for entry in feeds or []:
        if isinstance(entry, str):
            url = entry.strip()
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            url = str(entry[1]).strip()
        else:
            continue
        if url:
            urls.append(url)
    if not urls:
        raise RecipeError("recipe has no feeds", display_name=name)
    return urls


def load_recipe_source(
    source: str, name: str = "recipe"
) -> Union[RecipeSpec, "IndexRecipeSpec"]:
    """Execute recipe source code and extract what the module needs.

    Feed recipes yield a RecipeSpec (feed list and limits); parse_index
    recipes yield an IndexRecipeSpec holding a live instance.

    Recipes run as trusted code: the user chooses them, and the app already
    runs with the user's privileges.
    """
    namespace = {"BasicNewsRecipe": BasicNewsRecipe, "__name__": f"newsstand_recipe_{name}"}
    with calibre_shims():
        try:
            exec(compile(source, f"{name}.recipe", "exec"), namespace)
        except ImportError as exc:
            logger.warning("Recipe '%s' needs calibre: %s", name, exc)
            raise RecipeNeedsCalibre(
                f"recipe needs calibre ({getattr(exc, 'name', None) or exc})",
                display_name=name,
            ) from exc
        except Exception as exc:
            logger.warning("Recipe '%s' failed to load: %s", name, exc)
            raise RecipeError(
                f"recipe failed to load ({type(exc).__name__})", display_name=name
            ) from exc

        recipe_cls = None
        for value in namespace.values():
            if (
                isinstance(value, type)
                and issubclass(value, BasicNewsRecipe)
                and value is not BasicNewsRecipe
            ):
                recipe_cls = value
        if recipe_cls is None:
            raise RecipeError("no BasicNewsRecipe subclass found", display_name=name)

        title = getattr(recipe_cls, "title", None)
        display = str(title) if title else name

        if _overrides_parse_index(recipe_cls):
            try:
                instance = recipe_cls()
            except Exception as exc:
                logger.warning("Recipe '%s' failed to initialize: %s", name, exc)
                raise RecipeError(
                    f"recipe failed to initialize ({type(exc).__name__})",
                    display_name=display,
                ) from exc
            return IndexRecipeSpec(
                title=str(title) if title else None, recipe=instance, name=display
            )

    try:
        oldest_article = float(getattr(recipe_cls, "oldest_article", 7) or 7)
    except (TypeError, ValueError):
        oldest_article = 7
    try:
        max_articles = int(getattr(recipe_cls, "max_articles_per_feed", 100) or 100)
    except (TypeError, ValueError):
        max_articles = 100

    return RecipeSpec(
        title=str(title) if title else None,
        feeds=_normalize_feeds(getattr(recipe_cls, "feeds", None), display),
        oldest_article=oldest_article,
        max_articles_per_feed=max_articles,
    )


def load_recipe_file(path: Union[str, Path]) -> RecipeSpec:
    path = Path(path).expanduser()
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeError("recipe file not readable", display_name=path.stem) from exc
    return load_recipe_source(source, name=path.stem)


def _fetch_recipe_cached(url: str, cache_name: str, name: str, cache_dir: Path) -> str:
    """Download a recipe file with a 7-day disk cache and stale-cache fallback."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / cache_name
    if cached.exists():
        age = time.time() - cached.stat().st_mtime
        if age < CACHE_MAX_AGE_SECONDS:
            return cached.read_text(encoding="utf-8")

    try:
        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:
        if cached.exists():
            logger.warning(
                "Recipe '%s' refresh failed (%s); using stale cache", name, exc
            )
            return cached.read_text(encoding="utf-8")
        raise RecipeError("recipe download failed", display_name=name) from exc

    cached.write_text(response.text, encoding="utf-8")
    return response.text


def fetch_builtin_recipe(name: str, cache_dir: Path = CACHE_DIR) -> str:
    """Download a builtin calibre recipe by name, with a 7-day disk cache."""
    return _fetch_recipe_cached(
        BUILTIN_RECIPE_URL.format(name=name), f"{name}.recipe", name, cache_dir
    )


def fetch_recipe_url(url: str, cache_dir: Path = CACHE_DIR) -> str:
    """Download a .recipe file from any URL, with a 7-day disk cache."""
    name = source_display_name(url)
    # Hash the URL into the cache filename so two hosts serving the same
    # recipe filename don't share a cache entry.
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return _fetch_recipe_cached(url, f"{name}-{digest}.recipe", name, cache_dir)


def resolve_source(
    line: str, cache_dir: Path = CACHE_DIR
) -> Union[RecipeSpec, IndexRecipeSpec]:
    """Resolve one source line (builtin name, .recipe path/URL, or feed URL)."""
    s = line.strip()
    kind = classify_source(s)
    if kind == "url":
        return RecipeSpec(title=None, feeds=[s])
    if kind == "recipe_url":
        return load_recipe_source(
            fetch_recipe_url(s, cache_dir=cache_dir), name=source_display_name(s)
        )
    if kind == "path":
        return load_recipe_file(s)
    return load_recipe_source(fetch_builtin_recipe(s, cache_dir=cache_dir), name=s)


def _normalize_index_article(entry: Any) -> Optional[Dict[str, str]]:
    if not isinstance(entry, dict):
        return None
    url = str(entry.get("url", "") or "").strip()
    if not url:
        return None
    return {
        "title": str(entry.get("title", "") or "No Title"),
        "description": str(entry.get("description", "") or ""),
        "url": url,
    }


def run_parse_index(spec: IndexRecipeSpec) -> List[Tuple[str, List[Dict[str, str]]]]:
    """Run the recipe's parse_index and normalize its output.

    A full run is slow for real recipes: wsj_free sleeps 3 seconds per
    section and crawls the whole edition, so callers should go through
    get_index() and its 6-hour cache instead of calling this per print.
    """
    with calibre_shims():
        try:
            raw = spec.recipe.parse_index()
        except RecipeError:
            raise
        except Exception as exc:
            logger.warning(
                "Recipe '%s' parse_index failed: %s", spec.name or spec.title, exc
            )
            raise RecipeError(
                f"parse_index failed ({type(exc).__name__})",
                display_name=spec.title or spec.name,
            ) from exc

    sections: List[Tuple[str, List[Dict[str, str]]]] = []
    for entry in raw or []:
        if not isinstance(entry, (tuple, list)) or len(entry) < 2:
            continue
        section, articles = str(entry[0]), entry[1]
        normalized = [
            article
            for article in (_normalize_index_article(a) for a in articles or [])
            if article is not None
        ]
        sections.append((section, normalized))
    return sections


_index_cache: Dict[str, Tuple[float, List[Tuple[str, List[Dict[str, str]]]]]] = {}


def get_index(
    spec: IndexRecipeSpec, cache_key: str, now: Optional[float] = None
) -> List[Tuple[str, List[Dict[str, str]]]]:
    """parse_index with a per-source in-process cache (6 hours)."""
    now = time.time() if now is None else now
    hit = _index_cache.get(cache_key)
    if hit is not None and now - hit[0] < INDEX_CACHE_SECONDS:
        return hit[1]
    sections = run_parse_index(spec)
    _index_cache[cache_key] = (now, sections)
    return sections


def clear_index_cache() -> None:
    _index_cache.clear()
