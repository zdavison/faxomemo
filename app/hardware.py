import logging
import os
import platform
import threading
import time

from app.config import PRINTER_WIDTH, PRINTER_DRIVER

logger = logging.getLogger(__name__)

# Auto-detect platform and use appropriate drivers
_is_raspberry_pi = platform.system() == "Linux" and os.path.exists(
    "/proc/device-tree/model"
)

if PRINTER_DRIVER == "phomemo":
    from app.drivers.printer_phomemo import PrinterDriver
    from app.drivers.dial_mock import DialDriver
    from app.drivers.button_mock import ButtonDriver
elif PRINTER_DRIVER == "mock":
    from app.drivers.printer_mock import PrinterDriver
    from app.drivers.dial_mock import DialDriver
    from app.drivers.button_mock import ButtonDriver
elif _is_raspberry_pi:
    try:
        from app.drivers.printer_serial import PrinterDriver
        from app.drivers.dial_gpio import DialDriver
        from app.drivers.button_gpio import ButtonDriver
    except ImportError:
        from app.drivers.printer_mock import PrinterDriver
        from app.drivers.dial_mock import DialDriver
        from app.drivers.button_mock import ButtonDriver
else:
    from app.drivers.printer_mock import PrinterDriver
    from app.drivers.dial_mock import DialDriver
    from app.drivers.button_mock import ButtonDriver

# Global Hardware Instances
printer = PrinterDriver(width=PRINTER_WIDTH)
dial = DialDriver()

# Main Interface Button (Print / WiFi Setup / Reset) - GPIO 25 (Pin 22)
button = ButtonDriver(pin=25)

# --- PRINT ORCHESTRATION ---
# Shared reservation state so any producer (button, scheduler, email poll,
# webhooks, Slack, etc.) can claim the printer without racing the others.
print_lock = threading.Lock()
print_in_progress = False
hold_action_in_progress = False
hold_action_started_at = 0.0
last_print_time = 0.0
PRINT_DEBOUNCE_SECONDS = 3.0  # Minimum time between print jobs
HOLD_ACTION_TIMEOUT_SECONDS = 20.0


def printer_reserved_locked() -> bool:
    """Return True when the printer is reserved for a print or hold action."""
    return print_in_progress or hold_action_in_progress


def expire_stale_hold_action_locked(current_time: float):
    """Release a long-hold reservation if its matching release event was lost."""
    global hold_action_in_progress, hold_action_started_at

    if not hold_action_in_progress:
        return
    if (current_time - hold_action_started_at) < HOLD_ACTION_TIMEOUT_SECONDS:
        return

    hold_action_in_progress = False
    hold_action_started_at = 0.0
    logger.warning(
        "Cleared stale hold reservation after %.1fs without a matching release.",
        HOLD_ACTION_TIMEOUT_SECONDS,
    )


def printing_paused() -> bool:
    """True when the global printing switch is off."""
    import app.config as config_module

    return not getattr(config_module.settings, "printing_enabled", True)


def try_begin_print_job(*, debounce: bool = False, manual: bool = False) -> bool:
    """Reserve the printer for a new print job.

    ``manual`` marks a job the user asked for directly (button, web UI), which
    still prints while the global switch is off.
    """
    global print_in_progress, last_print_time

    if not manual and printing_paused():
        return False

    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if printer_reserved_locked():
            return False

        if debounce and (current_time - last_print_time) < PRINT_DEBOUNCE_SECONDS:
            return False

        print_in_progress = True
        last_print_time = current_time
        return True


def reserve_hold_action() -> bool:
    """Reserve the printer once the user crosses a long-hold threshold."""
    global hold_action_in_progress, hold_action_started_at, last_print_time

    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if print_in_progress:
            return False

        hold_action_in_progress = True
        hold_action_started_at = current_time
        last_print_time = current_time
        return True


def promote_hold_to_print_job() -> bool:
    """Convert a hold reservation into an active print job."""
    global print_in_progress, hold_action_in_progress, hold_action_started_at, last_print_time

    with print_lock:
        current_time = time.time()
        expire_stale_hold_action_locked(current_time)

        if print_in_progress:
            return False

        hold_action_in_progress = False
        hold_action_started_at = 0.0
        print_in_progress = True
        last_print_time = current_time
        return True


def clear_print_reservation(*, clear_hold: bool = True):
    """Release active print/hold reservations."""
    global print_in_progress, hold_action_in_progress, hold_action_started_at, last_print_time

    with print_lock:
        if print_in_progress:
            # Start debounce from the end of the physical print window, not the start.
            last_print_time = time.time()
        print_in_progress = False
        if clear_hold:
            hold_action_in_progress = False
            hold_action_started_at = 0.0

    if hasattr(button, "drain_pending_events"):
        try:
            button.drain_pending_events()
        except Exception:
            logger.debug("Failed to drain button events after print completion", exc_info=True)
