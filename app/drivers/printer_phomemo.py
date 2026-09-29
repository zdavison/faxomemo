import logging
import threading
import time

from PIL import Image

import app.config as config
from app.drivers.phomemo_transport import TransportError, make_transport
from app.drivers.printer_serial import PrinterDriver as SerialPrinterDriver

logger = logging.getLogger(__name__)

# This M220's firmware prints nothing for GS v 0 blocks taller than this,
# and it inserts a fixed blank feed between blocks. So: split, seam at a
# blank row, and swallow that much blank content so the inserted feed
# restores the intended spacing.
MAX_LINES_PER_BLOCK = 1200
SEAM_SEARCH_ROWS = 240  # how far above the split point to look for a blank row
MAX_IDLE_WAIT = 15.0  # upper bound on wait_for_idle's lines/200 estimate

_INVERT_TABLE = bytes(b ^ 0xFF for b in range(256))


class PrinterDriver(SerialPrinterDriver):
    """M220 raster-protocol driver.

    Inherits the upstream rendering pipeline (buffer ops, unified bitmap
    layout) from SerialPrinterDriver and only replaces the byte protocol
    and I/O transport: no serial port, no busy GPIO pin, no ESC/POS status
    channel.
    """

    # Measured on the M220: each raster block boundary inserts 7.5-11.5mm of
    # blank paper (8mm static feed plus variable re-grip slip). Compensate
    # for the MINIMUM so a seam is never tighter than the designed layout;
    # the variable remainder shows as a few mm of extra whitespace at worst.
    # Model-specific — re-measure before reusing for other Phomemo models.
    BLOCK_GAP_ROWS = 60

    def __init__(self, width: int = 49, transport=None, width_dots: int = None):
        super().__init__(width=width, init_serial=False)
        self.PRINTER_WIDTH_DOTS = width_dots or config.PAPER_WIDTH_DOTS
        self.media_type = config.PHOMEMO_MEDIA_TYPE
        self.transport = transport if transport is not None else make_transport(config)
        self._transport_lock = threading.RLock()
        # M220 has no cutter; the inherited post-flush feed_direct() would
        # otherwise send ESC/POS feed bytes it doesn't understand.
        self.cutter_feed_dots = 0

    # --- transport plumbing (replaces serial) ---

    def _initialize_busy_pin(self):
        pass

    def is_printer_busy(self) -> bool:
        return False

    def wait_for_idle(self, timeout: float = 3.0, quiet_period: float = 0.35):
        time.sleep(max(quiet_period, min(timeout, MAX_IDLE_WAIT)))

    def is_available(self) -> bool:
        with self._transport_lock:
            if self.transport.is_open:
                return True
            try:
                self.transport.open()
                return True
            except TransportError as exc:
                self.last_init_error = str(exc)
                return False

    def _initialize_printer(self):
        self.is_available()

    def _write(self, data: bytes):
        with self._transport_lock:
            if not self.transport.is_open:
                self.transport.open()
            self.transport.write(data)

    def _read(self, size: int = 1, timeout: float = 1.0) -> bytes:
        return b""

    def close(self):
        with self._transport_lock:
            self.transport.close()

    def _ensure_ascii_mode(self):
        pass

    def _apply_ascii_settings(self):
        pass

    def clear_hardware_buffer(self):
        """Clear the software print buffer only; M220 has no ESC/POS reset."""
        with self._io_lock:
            self.print_buffer.clear()
            self.lines_printed = 0
            self.max_lines = 0

    # --- M220 protocol ---

    def encode_bitmap(self, img: Image.Image) -> bytes:
        if img is None:
            return b""

        width, height = img.size

        # Ensure width is a multiple of 8 for byte alignment (mirrors upstream _send_bitmap).
        if width % 8 != 0:
            new_width = ((width // 8) + 1) * 8
            new_img = Image.new("1", (new_width, height), 1)
            new_img.paste(img, (0, 0))
            img = new_img
            width = new_width

        img = img.convert("1")
        # PIL '1' mode packs white=1/black=0 (MSB first); protocol wants black=1.
        pixels = img.tobytes().translate(_INVERT_TABLE)
        bytes_per_line = width // 8

        out = bytearray()
        out += b"\x1b\x4e\x0d\x05"  # speed 5
        out += b"\x1b\x4e\x04\x0a"  # density 10
        out += b"\x1f\x11" + bytes([self.media_type])  # media type

        row_bytes = bytes_per_line
        blank_row = bytes(row_bytes)

        def is_blank(row: int) -> bool:
            return pixels[row * row_bytes : (row + 1) * row_bytes] == blank_row

        line = 0
        while line < height:
            remaining = height - line
            lines = min(remaining, MAX_LINES_PER_BLOCK)
            if remaining > MAX_LINES_PER_BLOCK:
                lowest = max(line + 1, line + lines - SEAM_SEARCH_ROWS)
                for row in range(line + lines - 1, lowest - 1, -1):
                    if is_blank(row):
                        # Back the seam up through the blank run so the rows
                        # below it are swallowable, up to one gap's worth.
                        seam = row
                        while (
                            seam > line + 1
                            and row - seam < self.BLOCK_GAP_ROWS
                            and is_blank(seam - 1)
                        ):
                            seam -= 1
                        lines = seam - line + 1
                        break

            out += b"\x1d\x76\x30\x00"
            out += bytes_per_line.to_bytes(2, "little")
            out += lines.to_bytes(2, "little")
            out += pixels[line * row_bytes : (line + lines) * row_bytes]

            line += lines

            # The firmware inserts BLOCK_GAP_ROWS of blank feed here; swallow
            # the same amount of blank content so spacing is preserved. Never
            # swallow ink — a seam forced through content just keeps the gap.
            skipped = 0
            while line < height and skipped < self.BLOCK_GAP_ROWS and is_blank(line):
                line += 1
                skipped += 1

        out += b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00"  # footer
        return bytes(out)

    def _send_bitmap(self, img: Image.Image):
        if img is None:
            return
        data = self.encode_bitmap(img)
        logger.debug("Sending bitmap: %dx%d (%d bytes)", img.width, img.height, len(data))
        self._write(data)
        self.wait_for_idle(timeout=img.height / 200)
