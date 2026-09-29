"""Tests for the multi-language word-of-the-day module."""

import pytest

from app.modules import wotd


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


class _FakeResponse:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


MW_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:merriam="http://www.merriam-webster.com/">
  <channel>
    <title>Merriam-Webster Word of the Day</title>
    <item>
      <title>ephemeral</title>
      <merriam:shortdef>lasting a very short time</merriam:shortdef>
    </item>
  </channel>
</rss>
"""

FR_XML = b"""<xml xmlns:wotd="http://www.transparent.com/word-of-the-day/">
<words>
<date>08-27-2026</date>
<langname>French</langname>
<wordtype>noun</wordtype>
<word>la marmite</word>
<translation>pot</translation>
<fnphrase>Les marmites de ma grand-m\xc3\xa8re.</fnphrase>
<enphrase>My grandma's pots.</enphrase>
<wotd:transliteratedWord></wotd:transliteratedWord>
<wotd:transliteratedSentence></wotd:transliteratedSentence>
</words>
</xml>
"""

DE_XML = b"""<xml xmlns:wotd="http://www.transparent.com/word-of-the-day/">
<words>
<date>08-27-2026</date>
<langname>German</langname>
<wordtype>noun</wordtype>
<word>der Moskito</word>
<translation>mosquito</translation>
<fnphrase>Die Moskitos lassen mich nicht in Ruhe!</fnphrase>
<enphrase>The mosquitoes will not leave me alone!</enphrase>
<wotd:transliteratedWord></wotd:transliteratedWord>
<wotd:transliteratedSentence></wotd:transliteratedSentence>
</words>
</xml>
"""

JA_XML = """<xml xmlns:wotd="http://www.transparent.com/word-of-the-day/">
<words>
<date>08-27-2026</date>
<langname></langname>
<wordtype>verb</wordtype>
<word>貢献する</word>
<translation>to contribute</translation>
<fnphrase>ジョーダンは優勝に貢献しました。</fnphrase>
<enphrase>Jordan contributed to the victories.</enphrase>
<wotd:transliteratedWord>こうけんする</wotd:transliteratedWord>
<wotd:transliteratedSentence></wotd:transliteratedSentence>
</words>
</xml>
""".encode("utf-8")

ES_XML = """<xml xmlns:wotd="http://www.transparent.com/word-of-the-day/">
<words>
<date>08-27-2026</date>
<langname>Spanish</langname>
<wordtype>noun</wordtype>
<word>el cadáver</word>
<translation>corpse</translation>
<fnphrase>El cadáver fue encontrado en el río.</fnphrase>
<enphrase>The corpse was found in the river.</enphrase>
<wotd:transliteratedWord></wotd:transliteratedWord>
<wotd:transliteratedSentence></wotd:transliteratedSentence>
</words>
</xml>
""".encode("utf-8")

FEEDS = {
    wotd.MW_FEED_URL: MW_RSS,
    wotd.TRANSPARENT_FEED_URL.format(code="fr"): FR_XML,
    wotd.TRANSPARENT_FEED_URL.format(code="de"): DE_XML,
    wotd.TRANSPARENT_FEED_URL.format(code="ja"): JA_XML,
    wotd.TRANSPARENT_FEED_URL.format(code="es"): ES_XML,
}


@pytest.fixture
def fake_feeds(monkeypatch):
    def fake_get(url, timeout=None):  # noqa: ARG001
        if url not in FEEDS:
            raise RuntimeError(f"unexpected URL {url}")
        return _FakeResponse(FEEDS[url])

    monkeypatch.setattr(wotd.requests, "get", fake_get)


# --- Feed parsing ---


def test_parse_transparent_feed_extracts_fields():
    entry = wotd.parse_transparent_feed(JA_XML)

    assert entry["word"] == "貢献する"
    assert entry["reading"] == "こうけんする"
    assert entry["wordtype"] == "verb"
    assert entry["translation"] == "to contribute"
    assert entry["phrase"] == "ジョーダンは優勝に貢献しました。"
    assert entry["phrase_en"] == "Jordan contributed to the victories."


def test_parse_transparent_feed_empty_reading_is_none():
    entry = wotd.parse_transparent_feed(FR_XML)

    assert entry["word"] == "la marmite"
    assert entry["reading"] is None


# --- Receipt rendering ---


def test_default_config_prints_english_only(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={}, module_name="WOTD")

    joined = "\n".join(printer.lines)
    assert "ephemeral" in joined
    assert "lasting a very short time" in joined
    assert "marmite" not in joined
    assert "Moskito" not in joined


def test_french_and_german_sections_print_word_and_translation(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(
        printer,
        config={"languages": ["french", "german"]},
    )

    joined = "\n".join(printer.lines)
    assert "la marmite" in joined
    assert "pot" in joined
    assert "der Moskito" in joined
    assert "mosquito" in joined
    assert "ephemeral" not in joined
    assert joined.index("marmite") < joined.index("Moskito")


def test_sections_are_three_lines_with_native_label(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["french", "german"]})

    body = printer.lines[printer.lines.index("---") + 1:]
    assert body == [
        "FRANÇAIS [FRENCH]",
        "la marmite",
        "pot",
        "---",
        "DEUTSCH [GERMAN]",
        "der Moskito",
        "mosquito",
    ]


def test_no_example_sentences_or_wordtype_printed(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(
        printer, config={"languages": ["english", "french", "german"]}
    )

    joined = "\n".join(printer.lines)
    assert "grand-m" not in joined
    assert "Moskitos lassen" not in joined
    assert "noun" not in joined


def test_english_label_has_no_bracket(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["english"]})

    assert "ENGLISH" in printer.lines
    assert "ENGLISH [ENGLISH]" not in printer.lines


def test_failed_feed_does_not_block_other_languages(fake_feeds, monkeypatch):
    real_fetch = wotd.fetch_transparent_entry

    def flaky_fetch(code):
        if code == "fr":
            raise RuntimeError("feed down")
        return real_fetch(code)

    monkeypatch.setattr(wotd, "fetch_transparent_entry", flaky_fetch)
    printer = _FakePrinter()

    wotd.format_text_receipt(
        printer,
        config={"languages": ["french", "german"]},
    )

    joined = "\n".join(printer.lines)
    assert "marmite" not in joined
    assert "der Moskito" in joined


# --- Furigana alignment ---


def test_align_furigana_maps_reading_to_kanji_runs():
    assert wotd.align_furigana("貢献する", "こうけんする") == [
        ("貢献", "こうけん"),
        ("する", None),
    ]


def test_align_furigana_handles_interleaved_kana():
    assert wotd.align_furigana("思い立ったらすぐ", "おもいたったらすぐ") == [
        ("思", "おも"),
        ("い", None),
        ("立", "た"),
        ("ったらすぐ", None),
    ]


def test_align_furigana_kana_only_word_needs_no_ruby():
    assert wotd.align_furigana("すぐ", "すぐ") == [("すぐ", None)]


def test_align_furigana_katakana_word_matches_hiragana_reading():
    assert wotd.align_furigana("コーヒー", "こおひい") == [("コーヒー", None)]


def test_align_furigana_falls_back_to_whole_word_on_mismatch():
    assert wotd.align_furigana("貢献する", "まったくちがう") == [
        ("貢献する", "まったくちがう")
    ]


# --- Romaji ---


def test_kana_to_romaji_hyphenates_each_kana():
    assert wotd.kana_to_romaji("こうけんする") == "ko-u-ke-n-su-ru"
    assert wotd.kana_to_romaji("カタカナ") == "ka-ta-ka-na"


def test_kana_to_romaji_sokuon_and_digraphs():
    assert wotd.kana_to_romaji("おもいたったらすぐ") == "o-mo-i-ta-tta-ra-su-gu"
    assert wotd.kana_to_romaji("きゃく") == "kya-ku"
    assert wotd.kana_to_romaji("しゃしん") == "sha-shi-n"
    assert wotd.kana_to_romaji("ちゃ") == "cha"


def test_kana_to_romaji_n_is_its_own_syllable():
    assert wotd.kana_to_romaji("きんえん") == "ki-n-e-n"


def test_kana_to_romaji_katakana_and_long_vowel():
    assert wotd.kana_to_romaji("コーヒー") == "ko-o-hi-i"


# --- Japanese rendering ---


def test_furigana_only_above_kanji():
    if wotd.find_cjk_font() is None:
        pytest.skip("no CJK font installed")

    image = wotd.render_japanese_text_image(
        "貢献する", reading="こうけんする", width=380
    )

    font = wotd._load_cjk_font(wotd.JA_FONT_SIZE)
    kanji_width = int(font.getlength("貢献"))
    word_width = int(font.getlength("貢献する"))
    ruby_band = image.crop((0, 0, 380, wotd.JA_RUBY_SIZE + 2))
    pixels = ruby_band.load()

    def band_has_ink(x0, x1):
        return any(
            pixels[x, y] == 0
            for x in range(x0, x1)
            for y in range(ruby_band.height)
        )

    assert band_has_ink(0, kanji_width + 4)
    assert not band_has_ink(kanji_width + 8, word_width + 8)


def test_romaji_printed_under_japanese_word(fake_feeds):
    if wotd.find_cjk_font() is None:
        pytest.skip("no CJK font installed")
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["japanese"]})

    assert "ko-u-ke-n-su-ru" in printer.lines



def test_japanese_section_prints_cjk_image_with_translation(fake_feeds):
    if wotd.find_cjk_font() is None:
        pytest.skip("no CJK font installed")
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["japanese"]})

    assert printer.images, "expected the Japanese word to be printed as an image"
    joined = "\n".join(printer.lines)
    assert "to contribute" in joined


def test_japanese_word_image_renders_furigana_above_word():
    if wotd.find_cjk_font() is None:
        pytest.skip("no CJK font installed")

    with_furigana = wotd.render_japanese_text_image(
        "貢献する", reading="こうけんする", width=380, font_size=34
    )
    without_furigana = wotd.render_japanese_text_image(
        "貢献する", reading=None, width=380, font_size=34
    )

    assert with_furigana.mode == "1"
    assert with_furigana.width == 380
    assert with_furigana.height > without_furigana.height


def test_cjk_font_prefers_monospaced_jp_face():
    found = wotd.find_cjk_font()
    if found is None:
        pytest.skip("no CJK font installed")

    from PIL import ImageFont

    path, index = found
    family = ImageFont.truetype(path, 12, index=index).getname()[0]
    assert family in ("Noto Sans Mono CJK JP", "IPAGothic")


def test_japanese_text_uses_one_consistent_size(fake_feeds):
    if wotd.find_cjk_font() is None:
        pytest.skip("no CJK font installed")
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["japanese"]})

    # label image + word image, nothing else
    assert len(printer.images) == 2


def test_japanese_without_cjk_font_falls_back_to_ascii(fake_feeds, monkeypatch):
    monkeypatch.setattr(wotd, "find_cjk_font", lambda: None)
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["japanese"]})

    assert not printer.images
    joined = "\n".join(printer.lines)
    assert "to contribute" in joined


# --- Config schema ---


def test_schema_offers_supported_language_picker():
    languages = wotd.CONFIG_SCHEMA["properties"]["languages"]

    assert languages["type"] == "array"
    assert languages["default"] == ["english"]
    assert languages["items"]["enum"] == [
        "english",
        "french",
        "german",
        "japanese",
        "spanish",
        "italian",
        "portuguese",
        "dutch",
        "polish",
        "latin",
        "irish",
        "esperanto",
    ]
    assert len(languages["items"]["enumNames"]) == len(languages["items"]["enum"])


def test_schema_enum_matches_language_table():
    enum = wotd.CONFIG_SCHEMA["properties"]["languages"]["items"]["enum"]

    assert set(enum) == set(wotd.LANGUAGES)
    assert wotd.CONFIG_SCHEMA["properties"]["languages"]["maxItems"] >= len(enum)


def test_spanish_section_prints_from_transparent_feed(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(printer, config={"languages": ["spanish"]})

    joined = "\n".join(printer.lines)
    assert "el cadáver" in joined
    assert "corpse" in joined


def test_unknown_and_duplicate_selections_are_ignored(fake_feeds):
    printer = _FakePrinter()

    wotd.format_text_receipt(
        printer,
        config={"languages": ["german", "german", "klingon"]},
    )

    joined = "\n".join(printer.lines)
    assert joined.count("der Moskito") == 1
    assert "ephemeral" not in joined
