"""Tests for the crossword module's downloaded-puzzle source."""

import puz
import pytest

from app.modules import crossword


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

    def feed(self, _lines):
        return None


def _mini_puzzle():
    """5x5 mini with black squares in the NW and SE corners.

    Slots: 1A TAPS, 5A SAGES, 6A TRIPE, 7A ADOBE, 8A BEEN,
    1D TARDE, 2D AGIOE, 3D PEPBN, 4D SSEE, 5D STAB.
    """
    p = puz.Puzzle()
    p.width = 5
    p.height = 5
    p.title = "Test Mini"
    p.author = "A. Setter"
    p.solution = ".TAPS" "SAGES" "TRIPE" "ADOBE" "BEEN."
    p.fill = "".join("." if c == "." else "-" for c in p.solution)
    # Standard puz order: interleaved by number, across before down
    p.clues = [
        "Faucets",       # 1A
        "Drink orders",  # 1D
        "Nimble sort",   # 2D
        "Zesty snack",   # 3D
        "Twin esses",    # 4D
        "Wise ones",     # 5A
        "Sudden pain",   # 5D
        "Offal dish",    # 6A
        "Clay brick",    # 7A
        "Past particle", # 8A
    ]
    return p


# --- Grid rendering ---


def test_render_downloaded_grid_is_monochrome_and_sized():
    image = crossword.render_downloaded_grid(_mini_puzzle(), cell_size=40)

    assert image.mode == "1"
    assert image.width == 5 * 40 + 2
    assert image.height == 5 * 40 + 2


def test_render_downloaded_grid_marks_black_and_white_cells():
    image = crossword.render_downloaded_grid(_mini_puzzle(), cell_size=40)

    # Center of the NW corner cell (a block) is black
    assert image.getpixel((20, 20)) == 0
    # Center of a letter cell is white
    assert image.getpixel((60, 20)) == 255 or image.getpixel((60, 20)) == 1


# --- Downloaded receipt ---


def test_downloaded_receipt_prints_title_clues_and_answers(monkeypatch):
    monkeypatch.setattr(
        crossword, "fetch_downloaded_puzzle", lambda outlet: _mini_puzzle()
    )
    printer = _FakePrinter()

    crossword.execute_crossword(
        printer, config={"source": "Downloaded"}, module_name="CROSSWORD"
    )

    joined = "\n".join(printer.lines)
    assert "Test Mini" in joined
    assert "A. Setter" in joined
    assert "ACROSS" in joined
    assert "DOWN" in joined
    assert "1. Faucets" in joined
    assert "5. Sudden pain" in joined
    # Grid image plus upside-down answer-key image
    assert len(printer.images) == 2


def test_downloaded_receipt_uses_configured_outlet(monkeypatch):
    seen = {}

    def fake_fetch(outlet):
        seen["outlet"] = outlet
        return _mini_puzzle()

    monkeypatch.setattr(crossword, "fetch_downloaded_puzzle", fake_fetch)

    crossword.execute_crossword(
        _FakePrinter(),
        config={"source": "Downloaded", "outlet": "New Yorker Mini"},
        module_name="CROSSWORD",
    )
    assert seen["outlet"] == "tnym"


def test_downloaded_receipt_defaults_unknown_outlet(monkeypatch):
    seen = {}

    def fake_fetch(outlet):
        seen["outlet"] = outlet
        return _mini_puzzle()

    monkeypatch.setattr(crossword, "fetch_downloaded_puzzle", fake_fetch)

    crossword.execute_crossword(
        _FakePrinter(),
        config={"source": "Downloaded", "outlet": "bogus"},
        module_name="CROSSWORD",
    )
    assert seen["outlet"] == "tnym"


def test_download_failure_prints_error_without_generated_fallback(monkeypatch):
    def boom(outlet):
        raise crossword.PuzzleDownloadError("outlet unreachable")

    monkeypatch.setattr(crossword, "fetch_downloaded_puzzle", boom)
    printer = _FakePrinter()

    crossword.execute_crossword(
        printer, config={"source": "Downloaded"}, module_name="CROSSWORD"
    )

    joined = "\n".join(printer.lines).lower()
    assert "download failed" in joined
    # No generated fallback: no grid or answer-key images
    assert printer.images == []


def test_generated_source_never_downloads(monkeypatch):
    def boom(outlet):
        raise AssertionError("download attempted for generated source")

    monkeypatch.setattr(crossword, "fetch_downloaded_puzzle", boom)
    printer = _FakePrinter()

    crossword.execute_crossword(printer, config={}, module_name="CROSSWORD")

    assert len(printer.images) == 2


# --- Fetch wrapper ---


def test_fetch_downloaded_puzzle_wraps_xword_dl(monkeypatch):
    import xword_dl

    monkeypatch.setattr(
        xword_dl, "by_keyword", lambda keyword: (_mini_puzzle(), "f.puz")
    )
    puzzle = crossword.fetch_downloaded_puzzle("latm")
    assert puzzle.title == "Test Mini"


def test_fetch_downloaded_puzzle_raises_download_error(monkeypatch):
    import xword_dl

    def boom(keyword):
        raise RuntimeError("site changed")

    monkeypatch.setattr(xword_dl, "by_keyword", boom)
    with pytest.raises(crossword.PuzzleDownloadError):
        crossword.fetch_downloaded_puzzle("latm")


# --- Config schema ---


def test_config_schema_offers_source_and_outlet():
    from app.module_registry import get_module

    module_def = get_module("crossword")
    props = module_def.config_schema["properties"]

    assert props["source"]["enum"] == ["Generated", "Downloaded"]
    assert props["source"]["default"] == "Generated"
    # The settings dropdown shows enum values verbatim, so they are labels
    assert props["outlet"]["default"] == "New Yorker Mini"
    assert props["outlet"]["enum"] == [
        "New Yorker Mini",
        "LA Times Mini",
        "Guardian Quick",
        "Simply Daily Quick",
        "New Yorker",
        "LA Times",
        "Atlantic",
        "Crossword Club",
        "Daily Beast",
        "Daily Pop",
        "Der Standard",
        "Guardian Cryptic",
        "Guardian Everyman",
        "Guardian Quiptic",
        "Guardian Speedy",
        "Guardian Weekend",
        "Newsday",
        "Princetonian",
        "Princetonian Mini",
        "Puzzmo",
        "Puzzmo Big",
        "Simply Daily",
        "Simply Daily Cryptic",
        "Universal",
        "Vox",
        "Vulture",
        "The Walrus",
        "Washington Post",
    ]

    ui = module_def.ui_schema
    assert ui["outlet"]["ui:showWhen"] == {"field": "source", "value": "Downloaded"}
    assert ui["num_words"]["ui:showWhen"] == {"field": "source", "value": "Generated"}
    assert ui["difficulty"]["ui:showWhen"] == {"field": "source", "value": "Generated"}


# --- Upside-down answers ---


def test_render_upside_down_text_places_ink_at_far_edge():
    # Upright text starts at the left edge; after the 180° rotation the ink
    # must end flush against the right edge instead.
    image = crossword.render_upside_down_text("HI", 384)

    assert image.mode == "1"
    assert image.width == 384
    ink_bbox = image.convert("L").point(lambda p: 255 - p).getbbox()
    assert ink_bbox is not None
    assert ink_bbox[0] > 384 // 2


def test_render_upside_down_text_wraps_long_text():
    short = crossword.render_upside_down_text("HI", 200)
    long = crossword.render_upside_down_text(
        "ANSWERS: 1A:TAPS, 5A:SAGES, 6A:TRIPE, 7A:ADOBE, 8A:BEEN, 1D:TARDE", 200
    )
    assert long.height > short.height


class _FeedTrackingPrinter(_FakePrinter):
    def __init__(self):
        super().__init__()
        self.feeds = []

    def feed(self, lines):
        self.feeds.append(lines)


def test_downloaded_answers_print_upside_down_without_gap(monkeypatch):
    monkeypatch.setattr(
        crossword, "fetch_downloaded_puzzle", lambda outlet: _mini_puzzle()
    )
    printer = _FeedTrackingPrinter()

    crossword.execute_crossword(
        printer, config={"source": "Downloaded"}, module_name="CROSSWORD"
    )

    joined = "\n".join(printer.lines)
    assert "ANSWERS" not in joined
    # Grid image plus upside-down answers image
    assert len(printer.images) == 2
    assert max(printer.feeds) <= 2


def test_generated_answers_print_upside_down_without_gap():
    printer = _FeedTrackingPrinter()

    crossword.execute_crossword(printer, config={}, module_name="CROSSWORD")

    joined = "\n".join(printer.lines)
    assert "ANSWERS" not in joined
    assert len(printer.images) == 2
    assert max(printer.feeds) <= 2
