"""Tests for the Wikiquote-backed quotes module."""

import json

import pytest

from app.modules import quotes


class _FakePrinter:
    PRINTER_WIDTH_DOTS = 384

    def __init__(self):
        self.lines = []

    def print_header(self, text, *args, **kwargs):  # noqa: ARG002
        self.lines.append(str(text))

    def print_caption(self, text):
        self.lines.append(str(text))

    def print_body(self, text):
        self.lines.append(str(text))

    def print_line(self):
        self.lines.append("---")

    def feed(self, _lines):
        return None


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


GOETHE_WIKITEXT = (
    "{{Wikiquote:Quote of the day/Template\n"
    "| image1 = Goethe (Stieler 1828).jpg\n"
    "| image1px = 272px\n"
    "| quote = <!-- ⨀ <br /> -->The [[greatest]] [[happiness]] for the "
    "[[thinking]] [[Human|man]] is to have fathomed the fathomable, and to "
    "[[quietly]] [[revere]] the unfathomable.\n"
    "| author = Johann Wolfgang von Goethe\n"
    "}}"
)


def _api_payload(wikitext):
    return {"parse": {"title": "whatever", "wikitext": {"*": wikitext}}}


def test_fetches_and_parses_todays_quote(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        captured["params"] = params
        captured["headers"] = headers or {}
        return _FakeResponse(_api_payload(GOETHE_WIKITEXT))

    monkeypatch.setattr(quotes.requests, "get", fake_get)

    quote = quotes.get_wikiquote_qotd()
    assert quote == {
        "quoteText": (
            "The greatest happiness for the thinking man is to have fathomed "
            "the fathomable, and to quietly revere the unfathomable."
        ),
        "quoteAuthor": "Johann Wolfgang von Goethe",
    }
    assert "Wikiquote:Quote of the day/" in captured["params"]["page"]
    # Wikimedia rejects the default python-requests UA with a 403.
    assert "faxomemo" in captured["headers"]["User-Agent"]


def test_strips_italics_and_br_markup(monkeypatch):
    wikitext = (
        "{{Wikiquote:Quote of the day/Template\n"
        "| quote = ''Doubt'' is not a pleasant condition, <br /> but certainty "
        "is absurd.\n"
        "| author = [[Voltaire]]\n"
        "}}"
    )
    monkeypatch.setattr(
        quotes.requests,
        "get",
        lambda *a, **k: _FakeResponse(_api_payload(wikitext)),
    )

    quote = quotes.get_wikiquote_qotd()
    assert quote["quoteText"] == (
        "Doubt is not a pleasant condition, but certainty is absurd."
    )
    assert quote["quoteAuthor"] == "Voltaire"


def test_returns_none_on_network_error(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("no network")

    monkeypatch.setattr(quotes.requests, "get", boom)
    assert quotes.get_wikiquote_qotd() is None


def test_returns_none_when_page_missing(monkeypatch):
    payload = {"error": {"code": "missingtitle"}}
    monkeypatch.setattr(
        quotes.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )
    assert quotes.get_wikiquote_qotd() is None


def test_receipt_prints_wikiquote_quote(monkeypatch):
    monkeypatch.setattr(
        quotes.requests,
        "get",
        lambda *a, **k: _FakeResponse(_api_payload(GOETHE_WIKITEXT)),
    )

    printer = _FakePrinter()
    quotes.format_quotes_receipt(printer, module_name="QUOTE")

    body = "\n".join(printer.lines)
    assert "fathomed the fathomable" in body
    assert "— Johann Wolfgang von Goethe" in body
    assert "[[" not in body


def test_receipt_falls_back_to_local_db_offline(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("no network")

    monkeypatch.setattr(quotes.requests, "get", boom)
    monkeypatch.setattr(
        quotes,
        "get_random_quote",
        lambda: {"quoteText": "Local fallback wisdom.", "quoteAuthor": "Anon"},
    )

    printer = _FakePrinter()
    quotes.format_quotes_receipt(printer, module_name="QUOTE")

    body = "\n".join(printer.lines)
    assert "Local fallback wisdom." in body
    assert "— Anon" in body
