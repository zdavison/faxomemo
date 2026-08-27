"""Article-body fetch pipeline for the Newsstand module.

Fetches full articles either directly, through archive.today with a headless
browser, or through a recipe's get_obfuscated_article, then applies the
recipe's cleanup rules and extracts printable text.
"""

import logging
import re
import subprocess
import sys
import threading
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Union

import requests
from bs4 import BeautifulSoup

from app.newsstand_recipes import (
    RECIPE_USER_AGENT,
    IndexRecipeSpec,
    RecipeError,
    RecipeSpec,
    calibre_shims,
)

logger = logging.getLogger(__name__)

BROWSER_MISSING_MESSAGE = (
    "browser not installed - use the Install browser button in newsstand settings"
)
ARCHIVE_URL_PREFIX = "https://archive.ph/newest/"
READ_URL_TIMEOUT_SECONDS = 30
DIRECT_GET_TIMEOUT_SECONDS = 30


class BrowserNotInstalled(RecipeError):
    """Playwright or its Chromium build is missing."""


class ArchiveFetchError(RecipeError):
    """archive.today could not be fetched; carries the archive URL for humans."""

    def __init__(self, message: str, archive_url: str = ""):
        super().__init__(message)
        self.archive_url = archive_url


def _import_playwright():
    # Lazy import by design: Playwright is optional, installed from the
    # newsstand settings button, so the app must run without it.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserNotInstalled(BROWSER_MISSING_MESSAGE) from exc
    return sync_playwright


class _BrowserSession:
    """One headless Chromium, launched on first fetch, reused until close()."""

    def __init__(self):
        self._playwright = None
        self._browser = None

    def _ensure_browser(self):
        if self._browser is not None:
            return
        sync_playwright = _import_playwright()
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        except BrowserNotInstalled:
            raise
        except Exception as exc:
            self.close()
            message = str(exc)
            if "xecutable" in message or "install" in message.lower():
                raise BrowserNotInstalled(BROWSER_MISSING_MESSAGE) from exc
            raise

    def fetch(self, url: str, timeout: float) -> str:
        self._ensure_browser()
        page = self._browser.new_page(user_agent=RECIPE_USER_AGENT)
        try:
            page.goto(url, timeout=timeout * 1000, wait_until="load")
            return page.content()
        finally:
            page.close()

    def close(self):
        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._playwright and self._playwright.stop(),
        ):
            try:
                closer()
            except Exception:
                logger.debug("Error closing browser session", exc_info=True)
        self._browser = None
        self._playwright = None


_job_session: Optional[_BrowserSession] = None


@contextmanager
def browser_session():
    """Reuse one browser for every read_url within the block (one print job)."""
    global _job_session
    if _job_session is not None:
        yield _job_session
        return
    session = _BrowserSession()
    _job_session = session
    try:
        yield session
    finally:
        _job_session = None
        session.close()


def _fetch_with_retry(session: _BrowserSession, url: str, timeout: float) -> str:
    try:
        return session.fetch(url, timeout)
    except BrowserNotInstalled:
        raise
    except Exception as exc:
        logger.warning("read_url retrying %s after: %s", url, exc)
        return session.fetch(url, timeout)


def read_url(url: str, timeout: float = READ_URL_TIMEOUT_SECONDS) -> str:
    """Fetch a page with headless Chromium (runs challenge JavaScript)."""
    if _job_session is not None:
        return _fetch_with_retry(_job_session, url, timeout)
    with browser_session() as session:
        return _fetch_with_retry(session, url, timeout)


# --- Cleanup ---


def _find_matches(soup: BeautifulSoup, rules) -> List[Any]:
    matches: List[Any] = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        name = rule.get("name")
        # calibre rules put attribute filters both under 'attrs' and at the
        # dict's top level ({'id': [...]}, {'role': ...}); merge them, or an
        # attrs-less rule degenerates to find_all(None) and matches every tag.
        attrs = dict(rule.get("attrs") or {})
        for key, value in rule.items():
            if key not in ("name", "attrs"):
                attrs[key] = value
        if name is None and not attrs:
            continue
        matches.extend(soup.find_all(name, attrs=attrs))
    return matches


def apply_cleanup(
    soup: BeautifulSoup,
    keep_only_tags=None,
    remove_tags=None,
    remove_attributes=None,
) -> BeautifulSoup:
    """Apply calibre-style keep/remove rules (bs4 handles str/list/callable)."""
    kept = _find_matches(soup, keep_only_tags)
    if kept:
        kept_ids = {id(el) for el in kept}
        ordered = []
        claimed = set()
        for el in soup.find_all(True):
            if id(el) not in kept_ids or id(el) in claimed:
                continue
            ordered.append(el)
            for child in el.find_all(True):
                claimed.add(id(child))
        rebuilt = BeautifulSoup("<html><body></body></html>", "html.parser")
        for el in ordered:
            rebuilt.body.append(el.extract())
        soup = rebuilt

    for el in _find_matches(soup, remove_tags):
        if el.parent is not None:
            el.decompose()

    if remove_attributes:
        for tag in soup.find_all(True):
            for attr in remove_attributes:
                tag.attrs.pop(attr, None)
    return soup


_WHITESPACE = re.compile(r"\s+")
_BYLINE_CLASS = re.compile(r"auth|byline", re.IGNORECASE)


def _text(el) -> str:
    return _WHITESPACE.sub(" ", el.get_text(separator=" ", strip=True)).strip()


def extract_article_text(soup: BeautifulSoup) -> Dict[str, Any]:
    """Pull title, byline and body paragraphs out of a cleaned soup."""
    title = ""
    title_el = soup.find(["h1", "h2", "h3"])
    if title_el is not None:
        title = _text(title_el)

    byline = ""
    byline_el = soup.find(attrs={"class": _BYLINE_CLASS}) or soup.find(
        "a", attrs={"rel": "author"}
    )
    if byline_el is not None:
        byline = _text(byline_el)

    skip = {id(title_el), id(byline_el)}
    paragraphs: List[str] = []
    for el in soup.find_all(["p", "h2", "h3", "li"]):
        if id(el) in skip:
            continue
        if byline_el is not None and (el in byline_el.parents or byline_el in el.parents):
            continue
        text = _text(el)
        if not text or text == title:
            continue
        paragraphs.append(text)
    return {"title": title, "byline": byline, "paragraphs": paragraphs}


# --- Article pipeline ---


def _overrides(recipe, method_name: str) -> bool:
    if recipe is None:
        return False
    for klass in type(recipe).__mro__:
        if klass.__name__ == "BasicNewsRecipe" or klass is object:
            continue
        if method_name in klass.__dict__:
            return True
    return False


def fetch_article(
    spec: Optional[Union[RecipeSpec, IndexRecipeSpec]],
    url: str,
    use_archive: bool = False,
) -> Dict[str, Any]:
    """Fetch one article and return {title, byline, paragraphs} for printing."""
    recipe = getattr(spec, "recipe", None)

    # The override alone routes the fetch: recipes like economist_free define
    # the hook without setting articles_are_obfuscated.
    if _overrides(recipe, "get_obfuscated_article"):
        with calibre_shims():
            result = recipe.get_obfuscated_article(url)
        html = result.get("data") if isinstance(result, dict) else result
    elif _overrides(recipe, "print_version"):
        with calibre_shims():
            fetch_url = recipe.print_version(url)
        if fetch_url.startswith("file://"):
            path = fetch_url[len("file://"):].lstrip("/")
            html = Path("/" + path).read_bytes()
        else:
            response = requests.get(
                fetch_url,
                headers={"User-Agent": RECIPE_USER_AGENT},
                timeout=DIRECT_GET_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            html = response.text
    elif use_archive:
        archive_url = ARCHIVE_URL_PREFIX + url
        try:
            html = read_url(archive_url)
        except BrowserNotInstalled:
            raise
        except Exception as exc:
            raise ArchiveFetchError(
                f"archive fetch failed ({type(exc).__name__})",
                archive_url=archive_url,
            ) from exc
    else:
        response = requests.get(
            url,
            headers={"User-Agent": RECIPE_USER_AGENT},
            timeout=DIRECT_GET_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        html = response.text

    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    if _overrides(recipe, "preprocess_raw_html"):
        with calibre_shims():
            html = recipe.preprocess_raw_html(html, url) or html
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html, "html.parser")

    if _overrides(recipe, "preprocess_html"):
        with calibre_shims():
            soup = recipe.preprocess_html(soup) or soup

    soup = apply_cleanup(
        soup,
        keep_only_tags=getattr(recipe, "keep_only_tags", None),
        remove_tags=getattr(recipe, "remove_tags", None),
        remove_attributes=getattr(recipe, "remove_attributes", None),
    )
    return extract_article_text(soup)


# --- Browser install (triggered from newsstand settings) ---

_install_lock = threading.Lock()
_install_state = {"status": "idle", "detail": ""}


def install_state() -> Dict[str, str]:
    return dict(_install_state)


def install_browser() -> "tuple[bool, str]":
    """Install Playwright and its Chromium into this venv. Blocking, ~300 MB."""
    if not _install_lock.acquire(blocking=False):
        return False, "browser install already running"
    try:
        steps = [
            ([sys.executable, "-m", "pip", "install", "playwright"], "pip install playwright"),
            ([sys.executable, "-m", "playwright", "install", "chromium"], "playwright install chromium"),
        ]
        for cmd, label in steps:
            _install_state.update(status="running", detail=label)
            logger.info("Browser install: %s", label)
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                output = (proc.stderr or proc.stdout or "").strip()
                detail = f"{label} failed: {output[-400:]}"
                _install_state.update(status="error", detail=detail)
                logger.error("Browser install failed: %s", detail)
                return False, detail
        _install_state.update(status="done", detail="browser installed")
        return True, "browser installed"
    finally:
        _install_lock.release()
