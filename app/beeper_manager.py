"""
Beeper integration manager.

Each Beeper module instance connects to the local Beeper Desktop API
(default http://localhost:23373) with a bearer token created in
Beeper Desktop under Settings -> Integrations.

The manager listens on the API WebSocket for message events and, per event,
refetches the affected chat's messages over REST using a saved per-chat
cursor (the WebSocket payload is not reliable). If the WebSocket is
unavailable it falls back to a 30-second poll loop and retries the
WebSocket with backoff.

Incoming messages from whitelisted senders are printed as receipts.
An empty whitelist allows nobody: the Beeper account sees every personal
chat across all networks, so the module is closed by default.
"""

import asyncio
import logging
import re
from collections import deque
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

import app.config as config
from app import hardware
from app.config import BEEPER_DEFAULT_API_URL, BeeperConfig, format_print_datetime

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30
WS_MAX_BACKOFF_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 15
SEEN_MESSAGE_IDS_MAX = 500

MESSAGE_TYPE_ICONS = {
    "IMAGE": "image",
    "VIDEO": "video",
    "AUDIO": "file-audio",
    "FILE": "file",
    "STICKER": "sticker",
    "LOCATION": "map-pin",
}

# Injected from app.main at startup to avoid a circular import.
_trigger_channel: Optional[Callable[..., Awaitable[None]]] = None

# Active connections keyed by access token (one API connection per token).
_connections: Dict[str, "BeeperConnection"] = {}

# Last reported status per module id (survives failed connections so the UI
# and status receipt can explain what is wrong).
_statuses: Dict[str, Dict[str, Any]] = {}

_reconcile_lock = asyncio.Lock()


class BeeperAuthError(Exception):
    """The Beeper Desktop API rejected the access token (HTTP 401)."""


class BeeperUnreachableError(Exception):
    """Beeper Desktop is not running or not reachable at the configured URL."""


class BeeperConnection:
    """One live Beeper Desktop API connection for one module instance."""

    def __init__(self, module_id: str, module_name: str, cfg: BeeperConfig):
        self.module_id = module_id
        self.module_name = module_name
        self.cfg = cfg
        self.status = "connecting"
        self.detail = ""
        self.account_count = 0
        self.networks = ""
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._cursors: Dict[str, Optional[str]] = {}
        self._seen_ids: set = set()
        self._seen_order: deque = deque()
        # Participant cache per chat id: senderID -> {name, username, network}.
        self._participants: Dict[str, Dict[str, Dict[str, str]]] = {}

    @property
    def signature(self) -> tuple:
        """Config fingerprint used to detect changes that require a reconnect."""
        return (self.cfg.access_token, self.cfg.api_url)

    @property
    def api_url(self) -> str:
        return (self.cfg.api_url or BEEPER_DEFAULT_API_URL).strip().rstrip("/")

    async def start(self):
        self.status = "connecting"
        self.detail = ""
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.cfg.access_token.strip()}"},
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
        )
        try:
            accounts = _account_list(await self._api_get("/v1/accounts"))
        except BeeperAuthError:
            self.status = "error"
            self.detail = "Invalid access token"
            await self.stop(set_status=False)
            return
        except (BeeperUnreachableError, asyncio.TimeoutError):
            self.status = "error"
            self.detail = "Beeper Desktop not reachable"
            await self.stop(set_status=False)
            return
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.detail = str(exc)[:200]
            logger.error("Beeper module %s failed to connect: %s", self.module_id, exc)
            await self.stop(set_status=False)
            return

        self.account_count = len(accounts)
        self.networks = ", ".join(
            sorted({str(a.get("network", "")) for a in accounts if a.get("network")})
        )
        self.status = "connected"
        self.detail = ""
        self._task = asyncio.create_task(self._run())
        logger.info(
            "Beeper module %s connected (%d accounts: %s)",
            self.module_id,
            self.account_count,
            self.networks,
        )

    async def stop(self, *, set_status: bool = True):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                logger.debug("Error closing Beeper session", exc_info=True)
            self._session = None
        if set_status:
            self.status = "disconnected"

    # --- HTTP / WS ---

    async def _api_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            async with self._session.get(self.api_url + path, params=params) as resp:
                if resp.status == 401:
                    raise BeeperAuthError()
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientConnectorError as exc:
            raise BeeperUnreachableError() from exc
        except OSError as exc:
            raise BeeperUnreachableError() from exc

    async def _run(self):
        """Listen on the WebSocket; poll over REST while it is unavailable."""
        ws_backoff = 1.0
        while True:
            try:
                await self._listen_ws()
                ws_backoff = 1.0
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Beeper WebSocket unavailable for %s: %s", self.module_id, exc
                )

            ws_backoff = min(ws_backoff * 2, WS_MAX_BACKOFF_SECONDS)
            loop = asyncio.get_running_loop()
            retry_at = loop.time() + ws_backoff
            while True:
                try:
                    await self._poll_new_messages()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.debug("Beeper poll failed for %s", self.module_id, exc_info=True)
                remaining = retry_at - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    async def _listen_ws(self):
        ws_url = re.sub(r"^http", "ws", self.api_url) + "/v1/ws"
        async with self._session.ws_connect(ws_url, heartbeat=30) as ws:
            await ws.send_json({"type": "subscriptions.set", "subscriptions": ["*"]})
            self.status = "connected"
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
                    continue
                try:
                    event = msg.json()
                except ValueError:
                    continue
                await self._handle_ws_event(event)
        raise ConnectionError("Beeper WebSocket closed")

    async def _handle_ws_event(self, event: Dict[str, Any]):
        if event.get("type") != "message.upserted":
            return
        # Event payloads may be absent; REST with the saved cursor is the
        # source of truth. Look for the chat id in the common spots.
        payload = event.get("payload") or {}
        message = payload.get("message") or event.get("message") or {}
        chat_id = (
            event.get("chatID")
            or payload.get("chatID")
            or message.get("chatID")
        )
        if chat_id:
            for m in await self._fetch_new_messages(chat_id):
                await self.handle_message(m)
        else:
            await self._poll_new_messages()

    async def _poll_new_messages(self):
        data = await self._api_get("/v1/chats")
        for chat in data.get("items", []) or []:
            chat_id = chat.get("id")
            if not chat_id:
                continue
            for m in await self._fetch_new_messages(chat_id):
                await self.handle_message(m)

    async def _fetch_new_messages(self, chat_id: str) -> List[Dict[str, Any]]:
        """Fetch messages newer than the saved cursor for one chat.

        The first fetch for a chat only records the newest cursor ("now"),
        so the startup backlog is never printed.
        """
        cursor = self._cursors.get(chat_id)
        if cursor is None:
            data = await self._api_get(f"/v1/chats/{chat_id}/messages")
            self._cursors[chat_id] = data.get("newestCursor")
            return []

        data = await self._api_get(
            f"/v1/chats/{chat_id}/messages",
            params={"cursor": cursor, "direction": "after"},
        )
        if data.get("newestCursor"):
            self._cursors[chat_id] = data["newestCursor"]
        return data.get("items", []) or []

    # --- SENDER RESOLUTION ---

    async def _resolve_sender(self, chat_id: str, sender_id: str) -> Dict[str, str]:
        cached = self._participants.get(chat_id, {}).get(sender_id)
        if cached:
            return cached

        try:
            chat = await self._api_get(f"/v1/chats/{chat_id}")
        except Exception:  # noqa: BLE001
            logger.debug("Failed to fetch Beeper chat %s", chat_id, exc_info=True)
            return {"name": "Unknown", "username": "", "network": ""}

        network = str(chat.get("network", "") or "")
        participants = (chat.get("participants") or {}).get("items", []) or []
        chat_cache = self._participants.setdefault(chat_id, {})
        for p in participants:
            pid = p.get("id")
            if not pid:
                continue
            chat_cache[pid] = {
                "name": p.get("fullName") or p.get("username") or "Unknown",
                "username": p.get("username") or "",
                "network": network,
            }
        return chat_cache.get(
            sender_id, {"name": "Unknown", "username": "", "network": network}
        )

    # --- INBOUND MESSAGE HANDLING ---

    def _already_seen(self, message_id: str) -> bool:
        if message_id in self._seen_ids:
            return True
        self._seen_ids.add(message_id)
        self._seen_order.append(message_id)
        while len(self._seen_order) > SEEN_MESSAGE_IDS_MAX:
            self._seen_ids.discard(self._seen_order.popleft())
        return False

    async def handle_message(self, msg: Dict[str, Any]):
        """Whitelist-check, dedupe, and print one incoming message."""
        if msg.get("isSender"):
            return
        message_id = str(msg.get("id") or "")
        if not message_id or self._already_seen(message_id):
            return

        if not self.cfg.auto_print_messages:
            logger.info(
                "Ignoring Beeper message for module %s: auto-print is disabled.",
                self.module_id,
            )
            return

        sender = await self._resolve_sender(
            str(msg.get("chatID") or ""), str(msg.get("senderID") or "")
        )
        allowed = _parse_allowed_senders(self.cfg.allowed_senders)
        if not sender_allowed(allowed, sender.get("name", ""), sender.get("username", "")):
            logger.info(
                "Ignoring Beeper message from %s: not in the whitelist for module %s.",
                sender.get("name"),
                self.module_id,
            )
            return

        if not hardware.try_begin_print_job(debounce=False):
            logger.info("Skipping Beeper message print: printer is busy.")
            return

        from_line = f"From: {sender.get('name', 'Unknown')}"
        if sender.get("network"):
            from_line += f" ({sender['network']})"

        text = str(msg.get("text") or "")
        urls = extract_urls_from_text(text)
        msg_type = str(msg.get("type") or "TEXT").upper()
        attachment_types = [] if msg_type == "TEXT" else [msg_type.lower()]

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                _print_beeper_message_sync,
                self.module_name,
                from_line,
                text,
                urls,
                attachment_types,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to print Beeper message for module %s", self.module_id
            )
        finally:
            hardware.clear_print_reservation(clear_hold=False)


def init_runtime(trigger_channel: Callable[..., Awaitable[None]]):
    """Inject the channel trigger coroutine from app.main."""
    global _trigger_channel
    _trigger_channel = trigger_channel


def desired_instances() -> Dict[str, tuple]:
    """Map of access token -> (module_id, module_name, BeeperConfig) to run.

    The first instance (by module id) wins for a duplicated token; the
    duplicates are reported via status instead of opening a second connection.
    """
    desired: Dict[str, tuple] = {}
    settings = config.settings
    modules = getattr(settings, "modules", {}) or {}

    for module_id in sorted(modules.keys()):
        module = modules[module_id]
        if getattr(module, "type", None) != "beeper":
            continue

        cfg = BeeperConfig(**(module.config or {}))
        name = getattr(module, "name", "") or "BEEPER"

        if not cfg.access_token.strip():
            _statuses[module_id] = {
                "status": "unconfigured",
                "detail": "Access token is required.",
                "accounts": 0,
                "networks": "",
            }
            continue

        token = cfg.access_token.strip()
        if token in desired:
            other_id = desired[token][0]
            _statuses[module_id] = {
                "status": "duplicate",
                "detail": f"Same access token as module {other_id}; not connecting twice.",
                "accounts": 0,
                "networks": "",
            }
            logger.warning(
                "Beeper modules %s and %s share an access token; only %s will connect.",
                other_id,
                module_id,
                other_id,
            )
            continue

        desired[token] = (module_id, name, cfg)

    return desired


async def reconcile():
    """Start/stop/restart Beeper connections to match the current settings."""
    async with _reconcile_lock:
        desired = desired_instances()
        desired_module_ids = {entry[0] for entry in desired.values()}

        # Drop stale statuses for modules that no longer exist.
        settings_modules = getattr(config.settings, "modules", {}) or {}
        for module_id in list(_statuses.keys()):
            if module_id not in settings_modules:
                _statuses.pop(module_id, None)

        # Stop connections that are gone or whose config changed.
        for token in list(_connections.keys()):
            conn = _connections[token]
            entry = desired.get(token)
            if (
                entry is None
                or entry[0] != conn.module_id
                or (entry[2].access_token, entry[2].api_url) != conn.signature
            ):
                await conn.stop()
                _connections.pop(token, None)
                if conn.module_id not in desired_module_ids:
                    _statuses.pop(conn.module_id, None)

        # Start missing connections; retry ones that previously errored.
        for token, (module_id, name, cfg) in desired.items():
            conn = _connections.get(token)
            if conn is None:
                conn = BeeperConnection(module_id, name, cfg)
                _connections[token] = conn
                await conn.start()
            elif conn.status == "error":
                conn.module_name = name
                conn.cfg = cfg
                await conn.stop(set_status=False)
                await conn.start()
            else:
                # Refresh non-connection config (name, auto-print, whitelist).
                conn.module_name = name
                conn.cfg = cfg
            _publish_status(conn)


def _publish_status(conn: BeeperConnection):
    _statuses[conn.module_id] = {
        "status": conn.status,
        "detail": conn.detail,
        "accounts": conn.account_count,
        "networks": conn.networks,
    }


async def shutdown():
    """Close all connections (app shutdown)."""
    for conn in list(_connections.values()):
        await conn.stop()
    _connections.clear()


def get_statuses() -> Dict[str, Dict[str, Any]]:
    """Per-module-id connection status (for /api/health and the status receipt)."""
    for conn in _connections.values():
        _publish_status(conn)
    return dict(_statuses)


def get_module_status(module_id: str) -> Dict[str, Any]:
    return get_statuses().get(
        module_id,
        {"status": "disconnected", "detail": "", "accounts": 0, "networks": ""},
    )


def has_active_instances() -> bool:
    return bool(_connections)


async def _probe_accounts(access_token: str, api_url: str) -> List[Dict[str, Any]]:
    """One-shot GET /v1/accounts with its own session (credential test)."""
    base = (api_url or BEEPER_DEFAULT_API_URL).strip().rstrip("/")
    try:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
        ) as session:
            async with session.get(base + "/v1/accounts") as resp:
                if resp.status == 401:
                    raise BeeperAuthError()
                resp.raise_for_status()
                return _account_list(await resp.json())
    except aiohttp.ClientConnectorError as exc:
        raise BeeperUnreachableError() from exc
    except OSError as exc:
        raise BeeperUnreachableError() from exc


async def test_credentials(access_token: str, api_url: str = "") -> Dict[str, Any]:
    """Validate an access token without touching the running connections."""
    access_token = (access_token or "").strip()
    if not access_token:
        return {"ok": False, "error": "Access token is required."}

    try:
        accounts = await _probe_accounts(access_token, api_url)
    except BeeperAuthError:
        return {"ok": False, "error": "Invalid access token"}
    except (BeeperUnreachableError, asyncio.TimeoutError):
        return {
            "ok": False,
            "error": "Beeper Desktop not reachable. Is it running on this machine?",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}

    return {
        "ok": True,
        "accounts": [
            {
                "network": a.get("network", ""),
                "status": a.get("status", ""),
            }
            for a in accounts
        ],
        "networks": sorted({str(a.get("network", "")) for a in accounts if a.get("network")}),
    }


def _account_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items") or data.get("accounts") or []
        if isinstance(items, list):
            return items
    return []


# --- WHITELIST ---


def _parse_allowed_senders(raw: Optional[str]) -> List[str]:
    return [part.strip().lower() for part in (raw or "").split(",") if part.strip()]


def sender_allowed(allowed: List[str], full_name: str, username: str) -> bool:
    """Empty whitelist allows nobody (closed by default)."""
    if not allowed:
        return False
    candidates = {
        (full_name or "").strip().lower(),
        (username or "").strip().lower(),
    }
    candidates.discard("")
    return any(name in candidates for name in allowed)


# --- PRINTING ---


def extract_urls_from_text(text: str) -> List[str]:
    urls: List[str] = []
    for match in re.findall(r"https?://[^\s<>\"']+", text or ""):
        url = match.rstrip(".,;:!?)")
        if url and url not in urls:
            urls.append(url)
    return urls


def _print_beeper_message_sync(
    module_name: str,
    from_line: str,
    text: str,
    urls: List[str],
    attachment_types: List[str],
):
    """Blocking receipt print for one Beeper message (runs in an executor)."""
    printer = hardware.printer
    if hasattr(printer, "reset_buffer"):
        max_lines = getattr(config.settings, "max_print_lines", 200)
        printer.reset_buffer(max_lines)

    printer.print_header(module_name or "BEEPER", icon="chats-circle")
    printer.print_caption(from_line)
    printer.print_caption(format_print_datetime())
    printer.print_line()

    if text.strip():
        printer.print_body(text)

    for attachment_type in attachment_types:
        printer.feed(1)
        icon = MESSAGE_TYPE_ICONS.get(attachment_type.upper(), "file")
        printer.print_icon(icon, 48)
        printer.print_caption(attachment_type)

    for i, url in enumerate(urls, 1):
        printer.feed(2)
        qr_url = url
        if not qr_url.startswith(("http://", "https://")):
            qr_url = "https://" + qr_url

        printer.print_body(f"[{i}]")
        printer.print_qr(
            data=qr_url,
            # Highest error correction maximizes scanability from paper.
            error_correction="H",
        )

    if hasattr(printer, "flush_buffer"):
        printer.flush_buffer()
