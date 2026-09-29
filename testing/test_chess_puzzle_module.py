"""Tests for the Daily Chess Puzzle module."""

import json
from pathlib import Path

import pytest

from app.modules import chess_puzzle

FIXTURES = Path(__file__).resolve().parent / "chess_puzzle_fixtures"

DAILY = json.loads((FIXTURES / "daily.json").read_text(encoding="utf-8"))


class _FakePrinter:
    PRINTER_WIDTH_DOTS = 384

    def __init__(self):
        self.lines = []
        self.images = []

    def print_header(self, text, *args, **kwargs):  # noqa: ARG002
        self.lines.append(str(text))

    def print_caption(self, text):
        self.lines.append(str(text))

    def print_subheader(self, text):
        self.lines.append(str(text))

    def print_bold(self, text):
        self.lines.append(str(text))

    def print_body(self, text):
        self.lines.append(str(text))

    def print_line(self):
        self.lines.append("---")

    def print_image(self, image):
        self.images.append(image)
        self.lines.append("[image]")

    def _get_font(self, _name):
        return None

    def feed(self, _lines):
        return None


# --- Parsing ---


def test_parse_daily_puzzle_extracts_fields():
    puzzle = chess_puzzle.parse_daily_puzzle(DAILY)

    assert puzzle["id"] == "Varaa"
    assert puzzle["rating"] == 2081
    assert puzzle["side_to_move"] == "Black"
    assert puzzle["fen"].startswith("5rk1/")


def test_parse_daily_puzzle_converts_solution_to_san():
    puzzle = chess_puzzle.parse_daily_puzzle(DAILY)

    assert puzzle["solution_san"] == [
        "Bd4+", "Kf1", "Rxf4+", "Nxf4", "Rf2+", "Kg1", "Nf3+", "Kh1", "Rh2#",
    ]


def test_format_solution_text_numbers_moves_from_side_to_move():
    text = chess_puzzle.format_solution_text(
        ["Bd4+", "Kf1", "Rxf4+"], side_to_move="Black"
    )
    assert text == "1... Bd4+ 2. Kf1 Rxf4+"

    text = chess_puzzle.format_solution_text(["e4", "e5", "Nf3"], side_to_move="White")
    assert text == "1. e4 e5 2. Nf3"


# --- Board rendering ---


def test_draw_board_image_is_monochrome_and_sized():
    puzzle = chess_puzzle.parse_daily_puzzle(DAILY)
    image = chess_puzzle.draw_board_image(puzzle["fen"], width=380, flipped=True)

    assert image.mode == "1"
    assert image.width <= 380
    # Board with coordinates should be roughly square
    assert abs(image.width - image.height) < 40


def test_render_solution_image_is_rotated_monochrome():
    image = chess_puzzle.render_solution_image("1... Bd4+ 2. Kf1", width=380)
    assert image.mode == "1"
    assert image.width == 380


# --- Receipt formatting ---


def test_format_receipt_prints_diagram_and_solution(monkeypatch):
    monkeypatch.setattr(chess_puzzle, "fetch_daily_puzzle", lambda: DAILY)
    printer = _FakePrinter()

    chess_puzzle.format_chess_puzzle_receipt(printer, config={}, module_name="CHESS")

    joined = "\n".join(printer.lines)
    assert "Black to move" in joined
    assert "2081" in joined
    # Board diagram + upside-down solution
    assert len(printer.images) == 2


def test_format_receipt_hides_rating_when_configured(monkeypatch):
    monkeypatch.setattr(chess_puzzle, "fetch_daily_puzzle", lambda: DAILY)
    printer = _FakePrinter()

    chess_puzzle.format_chess_puzzle_receipt(
        printer, config={"show_rating": False}, module_name="CHESS"
    )

    assert "2081" not in "\n".join(printer.lines)


def test_format_receipt_reports_fetch_failure(monkeypatch):
    def boom():
        raise chess_puzzle.PuzzleFetchError("network down")

    monkeypatch.setattr(chess_puzzle, "fetch_daily_puzzle", boom)
    printer = _FakePrinter()

    chess_puzzle.format_chess_puzzle_receipt(printer, config={}, module_name="CHESS")

    joined = "\n".join(printer.lines)
    assert "network down" in joined or "unavailable" in joined.lower()


# --- Fetch caching ---


def test_fetch_writes_cache_and_serves_stale_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(chess_puzzle, "CACHE_DIR", tmp_path)

    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return DAILY

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=0, headers=None):  # noqa: ARG001
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(chess_puzzle.requests, "get", fake_get)
    assert chess_puzzle.fetch_daily_puzzle()["puzzle"]["id"] == "Varaa"
    assert calls["n"] == 1

    # Same day: served from cache, no second request
    assert chess_puzzle.fetch_daily_puzzle()["puzzle"]["id"] == "Varaa"
    assert calls["n"] == 1


def test_fetch_falls_back_to_stale_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(chess_puzzle, "CACHE_DIR", tmp_path)
    stale = tmp_path / "daily-2020-01-01.json"
    stale.write_text(json.dumps(DAILY), encoding="utf-8")

    def fake_get(url, timeout=0, headers=None):  # noqa: ARG001
        raise chess_puzzle.requests.RequestException("offline")

    monkeypatch.setattr(chess_puzzle.requests, "get", fake_get)
    assert chess_puzzle.fetch_daily_puzzle()["puzzle"]["id"] == "Varaa"


def test_fetch_raises_when_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(chess_puzzle, "CACHE_DIR", tmp_path)

    def fake_get(url, timeout=0, headers=None):  # noqa: ARG001
        raise chess_puzzle.requests.RequestException("offline")

    monkeypatch.setattr(chess_puzzle.requests, "get", fake_get)
    with pytest.raises(chess_puzzle.PuzzleFetchError):
        chess_puzzle.fetch_daily_puzzle()


# --- Registration ---


def test_module_is_registered():
    from app.module_registry import get_module

    module_def = get_module("chess_puzzle")
    assert module_def is not None
    assert module_def.offline is False
