import json
import random
import re
from datetime import date
from typing import Dict, Any, Optional
from pathlib import Path

import requests

from app.config import format_print_datetime
from app.module_registry import register_module

WIKIQUOTE_API_URL = "https://en.wikiquote.org/w/api.php"


# Resolve path relative to the app package directory.
def _get_quotes_db_path() -> Path:
    """Get the quotes database path relative to the app package."""
    return Path(__file__).resolve().parent.parent / "data" / "quotes.json"


LOCAL_DB_PATH = _get_quotes_db_path()


def get_random_quote() -> Dict[str, str]:
    """Reads a random quote from the local database."""
    try:
        quotes_path = _get_quotes_db_path()
        with open(quotes_path, "r", encoding="utf-8") as f:
            quotes = json.load(f)
            if not quotes:
                return {"quoteText": "No quotes found.", "quoteAuthor": "System"}
            if not isinstance(quotes, list):
                return {
                    "quoteText": "Invalid quotes database format.",
                    "quoteAuthor": "System",
                }
            return random.choice(quotes)
    except FileNotFoundError:
        return {
            "quoteText": "Offline quotes database is missing.",
            "quoteAuthor": "System",
        }
    except json.JSONDecodeError as e:
        return {
            "quoteText": f"Invalid JSON in quotes database: {e}",
            "quoteAuthor": "System",
        }
    except Exception as e:
        return {
            "quoteText": f"Error reading quotes database: {str(e)}",
            "quoteAuthor": "System",
        }


def _strip_wikitext(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = text.replace("''", "")
    return " ".join(text.split())


def _extract_template_field(wikitext: str, field: str) -> str:
    match = re.search(
        rf"\|\s*{field}\s*=\s*(.*?)(?=\n\s*\|\s*\w+\s*=|\n\s*}}}})",
        wikitext,
        flags=re.DOTALL,
    )
    return match.group(1) if match else ""


def get_wikiquote_qotd() -> Optional[Dict[str, str]]:
    """Fetches today's curated Quote of the Day from English Wikiquote."""
    today = date.today()
    page = f"Wikiquote:Quote of the day/{today:%B} {today.day}, {today.year}"
    try:
        response = requests.get(
            WIKIQUOTE_API_URL,
            params={
                "action": "parse",
                "page": page,
                "format": "json",
                "prop": "wikitext",
            },
            # Wikimedia rejects the default python-requests UA with a 403.
            headers={"User-Agent": "faxomemo/1.0 (thermal receipt printer)"},
            timeout=10,
        )
        response.raise_for_status()
        wikitext = response.json()["parse"]["wikitext"]["*"]
        quote = _strip_wikitext(_extract_template_field(wikitext, "quote"))
        author = _strip_wikitext(_extract_template_field(wikitext, "author"))
        if not quote:
            return None
        return {"quoteText": quote, "quoteAuthor": author or "Unknown"}
    except Exception:
        return None


@register_module(
    type_id="quotes",
    label="Daily Quote",
    description="Prints Wikiquote's curated Quote of the Day (falls back to offline database)",
    icon="quotes",
    offline=False,
    category="content",
    config_schema={
        "type": "object",
        "properties": {},
    },
)
def format_quotes_receipt(
    printer, config: Dict[str, Any] = None, module_name: str = None
):
    """Prints today's Wikiquote quote, or a random local one when offline."""
    quote = get_wikiquote_qotd() or get_random_quote()
    text = quote.get("quoteText", "")
    author = quote.get("quoteAuthor", "Unknown")

    # Header
    printer.print_header(module_name or "QUOTE", icon="quotes")
    printer.print_caption(format_print_datetime())
    printer.print_line()

    # Quote body in italics-style (using medium weight for emphasis)
    # Clean text: remove any embedded newlines and normalize whitespace
    import re
    text = re.sub(r'[\n\r\t]+', ' ', text)  # Replace all whitespace chars with single space
    text = " ".join(text.split())  # Normalize multiple spaces to single space
    
    # Don't pre-wrap! Let the printer's font-metric-based wrapping handle it.
    # The printer driver uses _wrap_text_by_width which uses actual font pixel metrics,
    # not character counts. Pre-wrapping with character-based wrap_text causes
    # double-wrapping and incorrect line breaks.
    # 
    # Simply pass the text with quotes - the printer will wrap it correctly based on
    # actual rendered width, and the quotes will naturally be at the start and end.
    printer.print_body(f'"{text}"')

    printer.feed(1)

    # Attribution
    if author:
        printer.print_caption(f"— {author}")
