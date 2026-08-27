"""Calibre-recipe loader and source resolver for the Newsstand module.

A .recipe file is Python source defining a BasicNewsRecipe subclass. This
module executes recipes against a small stub of calibre's recipe API and
extracts only the feed list and limits - enough to print headlines without
installing calibre itself.
"""

import hashlib
import logging
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

BUILTIN_RECIPE_URL = (
    "https://raw.githubusercontent.com/kovidgoyal/calibre/master/recipes/{name}.recipe"
)
CACHE_DIR = Path(__file__).resolve().parent / "data" / "newsstand_cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
DOWNLOAD_TIMEOUT_SECONDS = 10

_STUB_MODULE_NAMES = (
    "calibre",
    "calibre.web",
    "calibre.web.feeds",
    "calibre.web.feeds.news",
)


class RecipeError(Exception):
    """A source line could not be resolved to a usable recipe."""

    def __init__(self, message: str, display_name: Optional[str] = None):
        super().__init__(message)
        self.display_name = display_name


class RecipeNeedsCalibre(RecipeError):
    """The recipe overrides parse_index, which needs the real calibre engine."""


class BasicNewsRecipe:
    """Attribute-recording stand-in for calibre's BasicNewsRecipe."""

    title: Optional[str] = None
    feeds: Optional[list] = None
    oldest_article: float = 7
    max_articles_per_feed: int = 100


class _CalibreStubValue:
    """Permissive placeholder for calibre helpers recipes may import or call."""

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        return self


@dataclass
class RecipeSpec:
    """The subset of a recipe the Newsstand module needs."""

    title: Optional[str]
    feeds: List[str]
    oldest_article: float = 7
    max_articles_per_feed: int = 100


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


def _install_calibre_stubs() -> dict:
    saved = {}
    stub_value = _CalibreStubValue()
    for name in _STUB_MODULE_NAMES:
        saved[name] = sys.modules.get(name)
        module = types.ModuleType(name)
        # PEP 562 module __getattr__: unknown calibre helpers resolve to a
        # permissive stub so feed-only recipes that import extras still load.
        module.__dict__["__getattr__"] = lambda _name: stub_value
        sys.modules[name] = module
    sys.modules["calibre.web.feeds.news"].BasicNewsRecipe = BasicNewsRecipe
    return saved


def _remove_calibre_stubs(saved: dict) -> None:
    for name, original in saved.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


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


def load_recipe_source(source: str, name: str = "recipe") -> RecipeSpec:
    """Execute recipe source code and extract its feed list and limits.

    Recipes run as trusted code: the user chooses them, and the app already
    runs with the user's privileges.
    """
    namespace = {"BasicNewsRecipe": BasicNewsRecipe, "__name__": f"newsstand_recipe_{name}"}
    saved = _install_calibre_stubs()
    try:
        exec(compile(source, f"{name}.recipe", "exec"), namespace)
    except Exception as exc:
        logger.warning("Recipe '%s' failed to load: %s", name, exc)
        raise RecipeError(
            f"recipe failed to load ({type(exc).__name__})", display_name=name
        ) from exc
    finally:
        _remove_calibre_stubs(saved)

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
        raise RecipeNeedsCalibre(
            "recipe needs calibre (parse_index)", display_name=display
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


def resolve_source(line: str, cache_dir: Path = CACHE_DIR) -> RecipeSpec:
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
