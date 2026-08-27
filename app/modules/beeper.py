import logging
from typing import Any, Dict, Optional

from app.config import BEEPER_DEFAULT_API_URL, BeeperConfig, format_print_datetime
from app.drivers.printer_mock import PrinterDriver
from app.module_registry import register_module

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "connected": "Connected",
    "connecting": "Connecting...",
    "error": "Connection error",
    "disconnected": "Not connected",
    "duplicate": "Duplicate token",
    "unconfigured": "Not configured",
}


@register_module(
    type_id="beeper",
    label="Beeper",
    description="Print incoming Beeper messages from whitelisted senders",
    icon="chats-circle",
    offline=False,
    category="utilities",
    config_schema={
        "type": "object",
        "description": (
            "Connect Beeper Desktop running on this machine. Messages from "
            "whitelisted senders are printed as they arrive, even when this "
            "module is not on the active channel."
        ),
        "properties": {
            "access_token": {
                "type": "string",
                "title": "Access Token",
                "description": "Created in Beeper Desktop under Settings > Integrations.",
            },
            "auto_print_messages": {
                "type": "boolean",
                "title": "Print incoming messages",
                "description": "Print messages from allowed senders as they arrive.",
                "default": True,
            },
            "allowed_senders": {
                "type": "string",
                "title": "Allowed Senders",
                "description": (
                    "Comma-separated sender names or usernames. Only listed "
                    "senders print; an empty list allows nobody."
                ),
            },
            "advanced_connection": {
                "type": "null",
                "title": "Advanced",
            },
            "api_url": {
                "type": "string",
                "title": "API URL",
                "description": "Beeper Desktop API base URL.",
                "default": BEEPER_DEFAULT_API_URL,
            },
        },
    },
    ui_schema={
        "access_token": {"ui:widget": "password"},
        "allowed_senders": {"ui:placeholder": "Anna, Zach Davison"},
        "advanced_connection": {
            "ui:widget": "advanced-section",
            "ui:options": {"fields": ["api_url"]},
        },
        "api_url": {"ui:placeholder": BEEPER_DEFAULT_API_URL},
    },
)
def format_beeper_receipt(
    printer: PrinterDriver,
    config: Optional[Dict[str, Any]] = None,
    module_name: str = None,
    module_id: str = None,
) -> None:
    """Status receipt for the print button: connection state and usage hint."""
    if isinstance(config, BeeperConfig):
        cfg = config
    else:
        cfg = BeeperConfig(**(config or {}))
    module_name = module_name or "BEEPER"

    printer.print_header(module_name, icon="chats-circle")
    printer.print_caption(format_print_datetime())
    printer.print_line()

    if not cfg.access_token.strip():
        printer.print_body("Not configured.")
        printer.print_caption("Create an access token in Beeper")
        printer.print_caption("Desktop (Settings > Integrations)")
        printer.print_caption("and add it in the web settings.")
        return

    status = _lookup_status(module_id)
    state = status.get("status", "disconnected")
    printer.print_body(f"Status: {STATUS_LABELS.get(state, state)}")

    if status.get("accounts"):
        printer.print_caption(f"Accounts: {status['accounts']}")
    if status.get("networks"):
        printer.print_caption(f"Networks: {status['networks']}")
    if status.get("detail"):
        printer.print_caption(str(status["detail"]))

    printer.print_line()
    if state == "connected":
        if not cfg.auto_print_messages:
            printer.print_caption("Incoming message printing is off.")
        elif cfg.allowed_senders.strip():
            printer.print_body("Messages from allowed senders")
            printer.print_body("print here as they arrive.")
            printer.print_caption(f"Allowed: {cfg.allowed_senders.strip()}")
        else:
            printer.print_body("Whitelist is empty: nobody")
            printer.print_body("can print yet.")
            printer.print_caption("Add allowed senders in the")
            printer.print_caption("web settings to start printing.")
    else:
        printer.print_caption("Check Beeper Desktop is running")
        printer.print_caption("and the token in web settings,")
        printer.print_caption("then save to reconnect.")


def _lookup_status(module_id: Optional[str]) -> Dict[str, Any]:
    if not module_id:
        return {}
    try:
        from app import beeper_manager

        return beeper_manager.get_module_status(module_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not read Beeper status", exc_info=True)
        return {}
