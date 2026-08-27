"""In-memory QR print-token registry for the Newsstand module.

A receipt printed with qr_action=print encodes http://<device-ip>:<port>/a/<token>
per article; scanning it makes the device fetch and print the full article.
Tokens are memory-only by design: an evicted or restart-lost token just shows
the "receipt too old" page.
"""

import os
import secrets
import socket
import threading
from collections import OrderedDict
from typing import Dict, Optional

MAX_TOKENS = 200

_lock = threading.Lock()
_tokens: "OrderedDict[str, Dict[str, str]]" = OrderedDict()


def register_article(
    article_url: str,
    source_line: str,
    source_name: str = "",
    section: str = "",
) -> str:
    token = secrets.token_urlsafe(8)
    with _lock:
        _tokens[token] = {
            "article_url": article_url,
            "source_line": source_line,
            "source_name": source_name,
            "section": section,
        }
        while len(_tokens) > MAX_TOKENS:
            _tokens.popitem(last=False)
    return token


def resolve_token(token: str) -> Optional[Dict[str, str]]:
    with _lock:
        entry = _tokens.get(token)
        return dict(entry) if entry is not None else None


def clear_tokens() -> None:
    with _lock:
        _tokens.clear()


def _local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # connect() on UDP sends no packets; it just picks the outbound
        # interface, so this works without reaching 8.8.8.8.
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def device_base_url() -> str:
    port = os.environ.get("PORT", "8000")
    return f"http://{_local_ip()}:{port}"


def article_token_url(
    article_url: str,
    source_line: str,
    source_name: str = "",
    section: str = "",
) -> str:
    token = register_article(article_url, source_line, source_name, section)
    return f"{device_base_url()}/a/{token}"
