import logging
import threading
import time

from PIL import Image

import app.config as config
from app.drivers.phomemo_transport import TransportError, make_transport
from app.drivers.printer_serial import PrinterDriver as SerialPrinterDriver

logger = logging.getLogger(__name__)

MAX_LINES_PER_BLOCK = 1200  # M220 firmware rejects GS v 0 blocks taller than this
MAX_IDLE_WAIT = 15.0  # upper bound on wait_for_idle's lines/200 estimate

_INVERT_TABLE = bytes(b ^ 0xFF for b in range(256))


class PrinterDriver(SerialPrinterDriver):
    """M220 raster-protocol driver.

    Inherits the upstream rendering pipeline (buffer ops, unified bitmap
    layout) from SerialPrinterDriver and only replaces the byte protocol
    and I/O transport: no serial port, no busy GPIO pin, no ESC/POS status
    channel.
    """

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

        line = 0
        row_bytes = bytes_per_line
        while line < height:
            lines = min(MAX_LINES_PER_BLOCK, height - line)
            out += b"\x1d\x76\x30\x00"
            out += bytes_per_line.to_bytes(2, "little")
            out += lines.to_bytes(2, "little")

            chunk_start = line * row_bytes
            chunk_end = chunk_start + lines * row_bytes
            out += pixels[chunk_start:chunk_end]

            line += lines

        out += b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00"  # footer
        return bytes(out)

    def _send_bitmap(self, img: Image.Image):
        if img is None:
            return
        data = self.encode_bitmap(img)
        logger.debug("Sending bitmap: %dx%d (%d bytes)", img.width, img.height, len(data))
        self._write(data)
        self.wait_for_idle(timeout=img.height / 200)
