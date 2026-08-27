import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.config as config
import app.hardware as hardware_module
import app.beeper_manager as beeper_manager
from app.beeper_manager import (
    BeeperAuthError,
    BeeperConnection,
    BeeperUnreachableError,
    _parse_allowed_senders,
    extract_urls_from_text,
    sender_allowed,
)
from app.config import BeeperConfig, ModuleInstance
from app.modules.beeper import format_beeper_receipt


@pytest.fixture(autouse=True)
def reset_manager_state():
    beeper_manager._connections.clear()
    beeper_manager._statuses.clear()
    yield
    beeper_manager._connections.clear()
    beeper_manager._statuses.clear()


@pytest.fixture
def mock_printer(monkeypatch):
    printer = MagicMock()
    del printer.reset_buffer
    del printer.flush_buffer
    monkeypatch.setattr(hardware_module, "printer", printer)
    return printer


@pytest.fixture
def free_printer(monkeypatch):
    reservations = []
    monkeypatch.setattr(
        hardware_module,
        "try_begin_print_job",
        lambda debounce=False: reservations.append("begin") or True,
    )
    monkeypatch.setattr(
        hardware_module,
        "clear_print_reservation",
        lambda clear_hold=True: reservations.append(("clear", clear_hold)),
    )
    return reservations


ANNA = {"name": "Anna", "username": "anna_w", "network": "WhatsApp"}


def _make_connection(**cfg_overrides) -> BeeperConnection:
    cfg = BeeperConfig(access_token="beeper-tok", allowed_senders="Anna", **cfg_overrides)
    conn = BeeperConnection("beeper-1", "BEEPER", cfg)
    conn._resolve_sender = AsyncMock(return_value=dict(ANNA))
    return conn


def _msg(mid="m1", text="hello there", mtype="TEXT", **overrides):
    msg = {
        "id": mid,
        "chatID": "chat-1",
        "accountID": "acc-1",
        "senderID": "sender-1",
        "timestamp": "2026-08-27T10:00:00.000Z",
        "text": text,
        "type": mtype,
        "isSender": False,
    }
    msg.update(overrides)
    return msg


# --- WHITELIST ---


def test_parse_allowed_senders_trims_and_lowercases():
    assert _parse_allowed_senders("Anna,  Zach Davison , ") == ["anna", "zach davison"]
    assert _parse_allowed_senders("") == []
    assert _parse_allowed_senders(None) == []


def test_sender_allowed_matches_full_name_case_insensitive():
    allowed = _parse_allowed_senders("anna, Zach Davison")
    assert sender_allowed(allowed, "Anna", "anna_w")
    assert sender_allowed(allowed, "ZACH DAVISON", "")
    assert not sender_allowed(allowed, "Bob", "bob99")


def test_sender_allowed_matches_username():
    allowed = _parse_allowed_senders("anna_w")
    assert sender_allowed(allowed, "Anna Schmidt", "Anna_W")
    assert not sender_allowed(allowed, "Anna Schmidt", "other")


def test_empty_whitelist_allows_nobody():
    assert not sender_allowed([], "Anna", "anna_w")
    assert not sender_allowed(_parse_allowed_senders(""), "Anyone", "anyone")


# --- URL EXTRACTION ---


def test_extract_urls_from_text_dedupes():
    text = "see https://example.com and https://other.dev/x?a=1 and https://example.com"
    assert extract_urls_from_text(text) == [
        "https://example.com",
        "https://other.dev/x?a=1",
    ]
    assert extract_urls_from_text("no links here") == []
    assert extract_urls_from_text("") == []


# --- MESSAGE HANDLING ---


@pytest.mark.asyncio
async def test_handle_message_prints_receipt(mock_printer, free_printer):
    conn = _make_connection()
    await conn.handle_message(_msg(text="hi https://example.com"))

    assert free_printer == ["begin", ("clear", False)]
    mock_printer.print_header.assert_called_once()
    assert mock_printer.print_header.call_args.args[0] == "BEEPER"
    captions = [c.args[0] for c in mock_printer.print_caption.call_args_list]
    assert "From: Anna (WhatsApp)" in captions
    bodies = [c.args[0] for c in mock_printer.print_body.call_args_list]
    assert "hi https://example.com" in bodies
    assert mock_printer.print_qr.call_count == 1
    assert mock_printer.print_qr.call_args.kwargs["data"] == "https://example.com"


@pytest.mark.asyncio
async def test_handle_message_blocks_unlisted_sender(mock_printer, free_printer):
    conn = _make_connection(allowed_senders="Somebody Else")
    await conn.handle_message(_msg())
    mock_printer.print_header.assert_not_called()
    assert free_printer == []


@pytest.mark.asyncio
async def test_handle_message_empty_whitelist_blocks_everyone(mock_printer, free_printer):
    conn = _make_connection(allowed_senders="")
    await conn.handle_message(_msg())
    mock_printer.print_header.assert_not_called()
    assert free_printer == []


@pytest.mark.asyncio
async def test_handle_message_dedupes_on_id(mock_printer, free_printer):
    conn = _make_connection()
    await conn.handle_message(_msg(mid="same"))
    await conn.handle_message(_msg(mid="same"))
    assert mock_printer.print_header.call_count == 1


@pytest.mark.asyncio
async def test_handle_message_skips_own_messages(mock_printer, free_printer):
    conn = _make_connection()
    await conn.handle_message(_msg(isSender=True))
    mock_printer.print_header.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_respects_auto_print_toggle(mock_printer, free_printer):
    conn = _make_connection(auto_print_messages=False)
    await conn.handle_message(_msg())
    mock_printer.print_header.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_drops_when_printer_busy(monkeypatch, mock_printer):
    monkeypatch.setattr(hardware_module, "try_begin_print_job", lambda debounce=False: False)
    cleared = []
    monkeypatch.setattr(
        hardware_module, "clear_print_reservation", lambda clear_hold=True: cleared.append(clear_hold)
    )
    conn = _make_connection()
    await conn.handle_message(_msg())
    mock_printer.print_header.assert_not_called()
    assert cleared == []


@pytest.mark.asyncio
async def test_print_failure_still_clears_reservation(mock_printer, free_printer):
    mock_printer.print_header.side_effect = RuntimeError("printer exploded")
    conn = _make_connection()
    await conn.handle_message(_msg())
    assert free_printer == ["begin", ("clear", False)]


@pytest.mark.asyncio
async def test_non_text_message_prints_placeholder(mock_printer, free_printer):
    conn = _make_connection()
    await conn.handle_message(_msg(mid="img-1", text="", mtype="IMAGE"))

    mock_printer.print_icon.assert_called_once()
    assert mock_printer.print_icon.call_args.args[0] == "image"
    captions = [c.args[0] for c in mock_printer.print_caption.call_args_list]
    assert "image" in captions


# --- CURSOR / DEDUPE OVER REST ---


@pytest.mark.asyncio
async def test_first_fetch_initializes_cursor_without_printing():
    conn = _make_connection()
    conn._api_get = AsyncMock(
        return_value={"hasMore": False, "items": [_msg(mid="old")], "newestCursor": "cur-1"}
    )

    items = await conn._fetch_new_messages("chat-1")

    assert items == []
    assert conn._cursors["chat-1"] == "cur-1"
    conn._api_get.assert_awaited_once()
    params = conn._api_get.call_args.kwargs.get("params") or {}
    assert "direction" not in params


@pytest.mark.asyncio
async def test_second_fetch_uses_cursor_and_returns_new_messages():
    conn = _make_connection()
    conn._cursors["chat-1"] = "cur-1"
    new_msg = _msg(mid="m2")
    conn._api_get = AsyncMock(
        return_value={"hasMore": False, "items": [new_msg], "newestCursor": "cur-2"}
    )

    items = await conn._fetch_new_messages("chat-1")

    assert items == [new_msg]
    assert conn._cursors["chat-1"] == "cur-2"
    params = conn._api_get.call_args.kwargs["params"]
    assert params["cursor"] == "cur-1"
    assert params["direction"] == "after"


@pytest.mark.asyncio
async def test_refetch_does_not_reprint_seen_messages(mock_printer, free_printer):
    conn = _make_connection()
    conn._cursors["chat-1"] = "cur-1"
    msg = _msg(mid="m9")
    conn._api_get = AsyncMock(
        return_value={"hasMore": False, "items": [msg, msg], "newestCursor": "cur-1"}
    )

    for m in await conn._fetch_new_messages("chat-1"):
        await conn.handle_message(m)
    for m in await conn._fetch_new_messages("chat-1"):
        await conn.handle_message(m)

    assert mock_printer.print_header.call_count == 1


# --- MANAGER RECONCILE ---


def _beeper_module(module_id, token="beeper-tok", api_url="http://localhost:23373", **cfg):
    return ModuleInstance(
        id=module_id,
        type="beeper",
        name=f"Beeper {module_id}",
        config={"access_token": token, "api_url": api_url, **cfg},
    )


def _install_fake_settings(monkeypatch, modules):
    fake_settings = types.SimpleNamespace(modules=modules, channels={}, max_print_lines=200)
    monkeypatch.setattr(config, "settings", fake_settings)
    return fake_settings


def test_desired_instances_flags_unconfigured(monkeypatch):
    _install_fake_settings(
        monkeypatch,
        {
            "a": _beeper_module("a"),
            "b-empty": _beeper_module("b-empty", token=""),
        },
    )
    desired = beeper_manager.desired_instances()
    assert [entry[0] for entry in desired.values()] == ["a"]
    assert beeper_manager._statuses["b-empty"]["status"] == "unconfigured"


@pytest.mark.asyncio
async def test_reconcile_token_change_restarts_name_change_does_not(monkeypatch):
    started, stopped = [], []

    async def fake_start(self):
        self.status = "connected"
        started.append(self.module_id)

    async def fake_stop(self, *, set_status=True):
        stopped.append(self.module_id)
        if set_status:
            self.status = "disconnected"

    monkeypatch.setattr(BeeperConnection, "start", fake_start)
    monkeypatch.setattr(BeeperConnection, "stop", fake_stop)

    modules = {"a": _beeper_module("a")}
    _install_fake_settings(monkeypatch, modules)

    await beeper_manager.reconcile()
    assert started == ["a"]
    assert beeper_manager.get_module_status("a")["status"] == "connected"

    # Name / whitelist change -> no reconnect, config refreshed in place.
    modules["a"] = _beeper_module("a", allowed_senders="Anna")
    modules["a"].name = "RENAMED"
    await beeper_manager.reconcile()
    assert started == ["a"]
    assert stopped == []
    conn = next(iter(beeper_manager._connections.values()))
    assert conn.module_name == "RENAMED"
    assert conn.cfg.allowed_senders == "Anna"

    # Token change -> restart.
    modules["a"] = _beeper_module("a", token="new-token")
    await beeper_manager.reconcile()
    assert stopped == ["a"]
    assert started == ["a", "a"]

    # api_url change -> restart.
    modules["a"] = _beeper_module("a", token="new-token", api_url="http://localhost:9999")
    await beeper_manager.reconcile()
    assert stopped == ["a", "a"]
    assert started == ["a", "a", "a"]

    # Module removed -> stop and clear.
    modules.pop("a")
    await beeper_manager.reconcile()
    assert stopped == ["a", "a", "a"]
    assert not beeper_manager.has_active_instances()


@pytest.mark.asyncio
async def test_reconcile_retries_errored_connections(monkeypatch):
    attempts = []

    async def fake_start(self):
        attempts.append(self.module_id)
        self.status = "error"
        self.detail = "Beeper Desktop not reachable"

    async def fake_stop(self, *, set_status=True):
        pass

    monkeypatch.setattr(BeeperConnection, "start", fake_start)
    monkeypatch.setattr(BeeperConnection, "stop", fake_stop)
    _install_fake_settings(monkeypatch, {"a": _beeper_module("a")})

    await beeper_manager.reconcile()
    await beeper_manager.reconcile()

    assert attempts == ["a", "a"]
    assert beeper_manager.get_module_status("a")["status"] == "error"


# --- CREDENTIAL TEST ---


@pytest.mark.asyncio
async def test_test_credentials_requires_token():
    result = await beeper_manager.test_credentials("", "")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_test_credentials_maps_errors(monkeypatch):
    monkeypatch.setattr(
        beeper_manager, "_probe_accounts", AsyncMock(side_effect=BeeperAuthError())
    )
    result = await beeper_manager.test_credentials("bad", "http://localhost:23373")
    assert result["ok"] is False
    assert "Invalid access token" in result["error"]

    monkeypatch.setattr(
        beeper_manager, "_probe_accounts", AsyncMock(side_effect=BeeperUnreachableError())
    )
    result = await beeper_manager.test_credentials("tok", "http://localhost:23373")
    assert result["ok"] is False
    assert "not reachable" in result["error"]


@pytest.mark.asyncio
async def test_test_credentials_returns_accounts(monkeypatch):
    accounts = [
        {"accountID": "a1", "network": "whatsapp", "status": "active"},
        {"accountID": "a2", "network": "signal", "status": "active"},
    ]
    monkeypatch.setattr(
        beeper_manager, "_probe_accounts", AsyncMock(return_value=accounts)
    )
    result = await beeper_manager.test_credentials("tok", "")
    assert result["ok"] is True
    assert len(result["accounts"]) == 2


# --- STATUS RECEIPT ---


class _RecordingPrinter:
    def __init__(self):
        self.lines = []

    def print_header(self, text, icon=None, **kwargs):
        self.lines.append(f"HEADER:{text}")

    def print_caption(self, text):
        self.lines.append(f"CAPTION:{text}")

    def print_body(self, text):
        self.lines.append(f"BODY:{text}")

    def print_line(self):
        self.lines.append("LINE")


def test_status_receipt_unconfigured():
    printer = _RecordingPrinter()
    format_beeper_receipt(printer, {}, module_name="BEEPER", module_id="missing")
    assert any("Not configured" in line for line in printer.lines)


def test_status_receipt_connected():
    beeper_manager._statuses["beeper-1"] = {
        "status": "connected",
        "detail": "",
        "accounts": 2,
        "networks": "Signal, WhatsApp",
    }
    printer = _RecordingPrinter()
    format_beeper_receipt(
        printer,
        {"access_token": "tok", "allowed_senders": "Anna"},
        module_name="MY BEEPER",
        module_id="beeper-1",
    )
    joined = "\n".join(printer.lines)
    assert "HEADER:MY BEEPER" in joined
    assert "Status: Connected" in joined
    assert "Signal, WhatsApp" in joined
    assert "Anna" in joined


def test_status_receipt_connected_empty_whitelist_warns():
    beeper_manager._statuses["beeper-1"] = {
        "status": "connected",
        "detail": "",
        "accounts": 1,
        "networks": "WhatsApp",
    }
    printer = _RecordingPrinter()
    format_beeper_receipt(
        printer,
        {"access_token": "tok", "allowed_senders": ""},
        module_name="BEEPER",
        module_id="beeper-1",
    )
    joined = "\n".join(printer.lines)
    assert "nobody" in joined.lower() or "no messages" in joined.lower()


def test_status_receipt_error():
    beeper_manager._statuses["beeper-2"] = {
        "status": "error",
        "detail": "Beeper Desktop not reachable",
        "accounts": 0,
        "networks": "",
    }
    printer = _RecordingPrinter()
    format_beeper_receipt(
        printer,
        {"access_token": "tok"},
        module_name="BEEPER",
        module_id="beeper-2",
    )
    joined = "\n".join(printer.lines)
    assert "Status: Connection error" in joined
    assert "not reachable" in joined


def test_status_receipt_connecting_and_disconnected():
    for state, label in (("connecting", "Connecting"), ("disconnected", "Not connected")):
        beeper_manager._statuses["beeper-3"] = {
            "status": state,
            "detail": "",
            "accounts": 0,
            "networks": "",
        }
        printer = _RecordingPrinter()
        format_beeper_receipt(
            printer,
            {"access_token": "tok"},
            module_name="BEEPER",
            module_id="beeper-3",
        )
        assert any(label in line for line in printer.lines)
