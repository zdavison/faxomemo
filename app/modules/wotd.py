import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.config import format_print_datetime
from app.module_registry import register_module
import feedparser
import requests

MW_FEED_URL = "https://www.merriam-webster.com/wotd/feed/rss2"
TRANSPARENT_FEED_URL = "https://wotd.transparent.com/rss/{code}-widget.xml"
WOTD_NS = "{http://www.transparent.com/word-of-the-day/}"

# Prefer monospaced gothic faces so kana/kanji print at a uniform width.
# .ttc collections hold multiple faces; the preferred name is matched by index.
CJK_FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans Mono CJK JP"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "Noto Sans Mono CJK JP"),
    ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", None),
    ("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf", None),
]

JA_FONT_SIZE = 24
JA_RUBY_SIZE = 12
JA_LABEL_SIZE = 18

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "languages": {
            "type": "array",
            "title": "Languages",
            "default": ["english"],
            "maxItems": 12,
            "items": {
                "type": "string",
                "enum": [
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
                ],
                "enumNames": [
                    "English",
                    "French",
                    "German",
                    "Japanese (with furigana)",
                    "Spanish",
                    "Italian",
                    "Portuguese",
                    "Dutch",
                    "Polish",
                    "Latin",
                    "Irish",
                    "Esperanto",
                ],
                "default": "english",
            },
        },
    },
}

# key -> (native name, English name, transparent feed code; None = Merriam-Webster)
LANGUAGES = {
    "english": ("ENGLISH", "ENGLISH", None),
    "french": ("FRANÇAIS", "FRENCH", "fr"),
    "german": ("DEUTSCH", "GERMAN", "de"),
    "japanese": ("日本語", "JAPANESE", "ja"),
    "spanish": ("ESPAÑOL", "SPANISH", "es"),
    "italian": ("ITALIANO", "ITALIAN", "it"),
    "portuguese": ("PORTUGUÊS", "PORTUGUESE", "pt"),
    "dutch": ("NEDERLANDS", "DUTCH", "nl"),
    "polish": ("POLSKI", "POLISH", "pl"),
    "latin": ("LATINA", "LATIN", "la"),
    "irish": ("GAEILGE", "IRISH", "ga"),
    "esperanto": ("ESPERANTO", "ESPERANTO", "esp"),
}


def fetch_english_entry() -> Dict[str, Optional[str]]:
    response = requests.get(MW_FEED_URL, timeout=10)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if not feed.entries:
        raise ValueError("empty Merriam-Webster feed")
    entry = feed.entries[0]
    return {
        "word": entry.get("title", "No Word"),
        "translation": entry.get("merriam_shortdef", "No Definition"),
        "reading": None,
    }


def parse_transparent_feed(content: bytes) -> Dict[str, Optional[str]]:
    root = ET.fromstring(content)
    words = root.find("words")
    if words is None:
        raise ValueError("no <words> element in feed")

    def text(tag: str) -> Optional[str]:
        node = words.find(tag)
        value = (node.text or "").strip() if node is not None else ""
        return value or None

    word = text("word")
    if not word:
        raise ValueError("no word in feed")
    return {
        "word": word,
        "wordtype": text("wordtype"),
        "translation": text("translation"),
        "phrase": text("fnphrase"),
        "phrase_en": text("enphrase"),
        "reading": text(f"{WOTD_NS}transliteratedWord"),
    }


def fetch_transparent_entry(code: str) -> Dict[str, Optional[str]]:
    response = requests.get(TRANSPARENT_FEED_URL.format(code=code), timeout=10)
    response.raise_for_status()
    return parse_transparent_feed(response.content)


def find_cjk_font() -> Optional[Tuple[str, int]]:
    """Locate a CJK font, returning (path, face index) or None.

    Scans .ttc face indexes for the preferred family name so we get the
    monospaced JP face instead of whatever sits at index 0.
    """
    for path, preferred_name in CJK_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        if preferred_name is None:
            return (path, 0)
        for index in range(16):
            try:
                face = ImageFont.truetype(path, 12, index=index)
            except Exception:
                break
            if face.getname()[0] == preferred_name:
                return (path, index)
        return (path, 0)
    return None


def _load_cjk_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    found = find_cjk_font()
    if not found:
        return None
    path, index = found
    return ImageFont.truetype(path, size, index=index)


_KANJI_RE = re.compile(r"[㐀-鿿豈-﫿々〆ヵヶ]")


def _kata_to_hira(text: str) -> str:
    return "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in text
    )


def _segment_word(word: str) -> List[Tuple[str, bool]]:
    """Split into alternating (run, is_kanji) segments."""
    segments: List[List] = []
    for ch in word:
        is_kanji = bool(_KANJI_RE.match(ch))
        if segments and segments[-1][1] == is_kanji:
            segments[-1][0] += ch
        else:
            segments.append([ch, is_kanji])
    return [(text, is_kanji) for text, is_kanji in segments]


def align_furigana(word: str, reading: str) -> List[Tuple[str, Optional[str]]]:
    """Match the kana reading against the word's kana runs so each kanji run
    gets only its own slice of the reading. Falls back to whole-word ruby
    when the reading doesn't line up (e.g. irregular okurigana)."""
    segments = _segment_word(word)
    pattern = "".join(
        "(.+?)" if is_kanji else re.escape(_kata_to_hira(text))
        for text, is_kanji in segments
    )
    match = re.fullmatch(pattern, _kata_to_hira(reading))
    if not match:
        # Romaji comparison so ー-vs-vowel spellings (コーヒー / こおひい) match
        if kana_to_romaji(word) == kana_to_romaji(reading):
            return [(word, None)]
        return [(word, reading)]
    groups = iter(match.groups())
    return [
        (text, next(groups) if is_kanji else None) for text, is_kanji in segments
    ]


_ROMAJI_DIGRAPHS = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
}

_ROMAJI_KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "wo",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゔ": "vu",
}


def kana_to_romaji(kana: str) -> str:
    """One hyphen-separated syllable per kana ("ka-ta-ka-na"). Digraphs are
    one syllable, sokuon doubles into the next ("ta-tta"), ー repeats the
    previous vowel as its own syllable."""
    text = _kata_to_hira(kana)
    out: List[str] = []
    pending_sokuon = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "っ":
            pending_sokuon = True
            i += 1
            continue
        if ch == "ー":
            prev = out[-1][-1] if out and out[-1][-1] in "aiueo" else ""
            if prev:
                out.append(prev)
            i += 1
            continue
        romaji = _ROMAJI_DIGRAPHS.get(text[i:i + 2])
        if romaji:
            i += 2
        else:
            romaji = _ROMAJI_KANA.get(ch, "n" if ch == "ん" else ch)
            i += 1
        if pending_sokuon and romaji[:1] not in "aiueo":
            romaji = ("t" if romaji.startswith("ch") else romaji[0]) + romaji
            pending_sokuon = False
        out.append(romaji)
    return "-".join(out)


def _wrap_by_width(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> List[str]:
    """Wrap character-by-character: Japanese has no spaces to break on."""
    lines = []
    current = ""
    for char in text:
        candidate = current + char
        if font.getlength(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def render_japanese_text_image(
    text: str,
    reading: Optional[str] = None,
    width: int = 380,
    font_size: int = JA_FONT_SIZE,
) -> Optional[Image.Image]:
    font = _load_cjk_font(font_size)
    if font is None:
        return None
    ruby_font = _load_cjk_font(JA_RUBY_SIZE)

    line_height = font_size + 4

    segments = align_furigana(text, reading) if reading else [(text, None)]
    has_ruby = any(ruby for _, ruby in segments)
    ruby_height = JA_RUBY_SIZE + 2 if has_ruby and ruby_font else 0

    lines = _wrap_by_width(text, font, width - 4)
    height = ruby_height + line_height * max(1, len(lines)) + 2

    image = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(image)

    if ruby_height:
        if len(lines) > 1:
            # Wrapped word: per-kanji x positions no longer hold, so center
            # the whole reading over the first line instead
            segments = [(lines[0], reading)]
        # Each ruby run is centered over its own kanji run
        x = 2.0
        for segment_text, ruby in segments:
            segment_width = font.getlength(segment_text)
            if ruby:
                ruby_width = ruby_font.getlength(ruby)
                draw.text((max(0, x + (segment_width - ruby_width) // 2), 0),
                          ruby, font=ruby_font, fill=0)
            x += segment_width

    y = ruby_height
    for line in lines:
        draw.text((2, y), line, font=font, fill=0)
        y += line_height

    return image


def _print_japanese_section(printer, entry: Dict[str, Optional[str]]):
    width = getattr(printer, "PRINTER_WIDTH_DOTS", 384) - 4

    label_image = render_japanese_text_image(
        "日本語 [JAPANESE]", width=width, font_size=JA_LABEL_SIZE
    )
    word_image = render_japanese_text_image(
        entry["word"], reading=entry.get("reading"), width=width
    )
    if word_image is None:
        printer.print_subheader("JAPANESE")
        printer.print_caption("Japanese font unavailable (install fonts-noto-cjk)")
        if entry.get("translation"):
            printer.print_body(entry["translation"])
        return

    if label_image is not None:
        printer.print_image(label_image)
    printer.print_image(word_image)
    if entry.get("reading"):
        printer.print_caption(kana_to_romaji(entry["reading"]))
    if entry.get("translation"):
        printer.print_body(entry["translation"])


def _print_latin_section(
    printer, native: str, english: str, entry: Dict[str, Optional[str]]
):
    label = native if native == english else f"{native} [{english}]"
    printer.print_subheader(label)
    printer.print_bold(entry["word"])
    if entry.get("translation"):
        printer.print_body(entry["translation"])


@register_module(
    type_id="wotd",
    label="Word of the Day",
    description="Prints words of the day in your choice of 12 languages, including Japanese with furigana",
    icon="book",
    offline=False,
    category="content",
    config_schema=CONFIG_SCHEMA,
)
def format_text_receipt(printer, config: Dict[str, Any] = None, module_name: str = None):
    config = config or {}
    selected = config.get("languages") or ["english"]
    enabled = []
    for key in selected:
        if key in LANGUAGES and key not in enabled:
            enabled.append(key)
    if not enabled:
        enabled = ["english"]

    printer.print_header(module_name or "WORD OF THE DAY", icon="book")
    printer.print_caption(format_print_datetime())
    printer.print_line()

    printed_any = False
    for key in enabled:
        native, english, code = LANGUAGES[key]
        try:
            if code is None:
                entry = fetch_english_entry()
            else:
                entry = fetch_transparent_entry(code)
        except Exception:
            continue

        if printed_any:
            printer.print_line()
        if key == "japanese":
            _print_japanese_section(printer, entry)
        else:
            _print_latin_section(printer, native, english, entry)
        printed_any = True
