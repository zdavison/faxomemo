import importlib


def test_paper_width_dots_default_matches_m220_printable_width(monkeypatch):
    monkeypatch.delenv("PC1_PAPER_WIDTH_MM", raising=False)
    import app.config as config
    importlib.reload(config)
    assert config.PAPER_WIDTH_DOTS == 384


def test_paper_width_dots_configurable(monkeypatch):
    monkeypatch.setenv("PC1_PAPER_WIDTH_MM", "72")
    import app.config as config
    importlib.reload(config)
    assert config.PAPER_WIDTH_DOTS == 576
    monkeypatch.delenv("PC1_PAPER_WIDTH_MM")
    importlib.reload(config)


def test_driver_selection_default_is_auto():
    import app.config as config
    assert config.PRINTER_DRIVER == "auto"


import pytest
from app.drivers.phomemo_transport import (
    UsbTransport, BluetoothTransport, TransportError, make_transport,
)

def test_usb_transport_writes_bytes(tmp_path):
    dev = tmp_path / "lp0"
    dev.write_bytes(b"")
    t = UsbTransport(str(dev))
    t.open()
    t.write(b"\x1b\x4e")
    t.close()
    assert dev.read_bytes() == b"\x1b\x4e"
    assert not t.is_open

def test_usb_transport_missing_device_raises(tmp_path):
    t = UsbTransport(str(tmp_path / "nope"))
    with pytest.raises(TransportError):
        t.open()

def test_bluetooth_transport_requires_mac():
    with pytest.raises(TransportError):
        BluetoothTransport("").open()

def test_make_transport_usb(monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "PHOMEMO_TRANSPORT", "usb")
    monkeypatch.setattr(config, "PHOMEMO_USB_DEVICE", "/dev/usb/lp0")
    t = make_transport(config)
    assert isinstance(t, UsbTransport)

def test_usb_transport_short_write_loop(tmp_path, monkeypatch):
    """Verify short-write handling: os.write returning 1 byte at a time loops until all written."""
    dev = tmp_path / "lp0"
    dev.write_bytes(b"")
    t = UsbTransport(str(dev))
    t.open()

    # Monkeypatch os.write at the app.drivers.phomemo_transport module level
    write_calls = []
    original_os_write = __import__('os').write

    def mock_os_write(fd, data):
        write_calls.append(len(data))
        # Write only 1 byte at a time to simulate short-write
        return original_os_write(fd, data[:1])

    monkeypatch.setattr("app.drivers.phomemo_transport.os.write", mock_os_write)

    # Write 4 bytes, should be split into 1+1+1+1 by mock
    t.write(b"\x1b\x4e\x00\xff")
    t.close()

    # Verify all bytes ended up in file
    assert dev.read_bytes() == b"\x1b\x4e\x00\xff"
    # Verify write was called multiple times due to short-write
    assert len(write_calls) >= 4  # At least 4 calls, one per byte

def test_bluetooth_transport_closes_socket_on_connect_failure(monkeypatch):
    """Verify socket is closed if connect() raises OSError."""
    import app.drivers.phomemo_transport as bt_module

    class FakeSocket:
        def __init__(self):
            self.closed = False
            self.connect_called = False

        def settimeout(self, timeout):
            pass

        def connect(self, addr):
            self.connect_called = True
            raise OSError("Connection refused")

        def close(self):
            self.closed = True

    fake_sock = FakeSocket()

    # Mock socket module with necessary constants and socket constructor
    class MockSocket:
        AF_BLUETOOTH = 31
        SOCK_STREAM = 1
        BTPROTO_RFCOMM = 3

        @staticmethod
        def socket(*args, **kwargs):
            return fake_sock

    monkeypatch.setattr(bt_module, "socket", MockSocket())

    t = BluetoothTransport("00:11:22:33:44:55")
    with pytest.raises(TransportError):
        t.open()

    # Verify socket.close() was called
    assert fake_sock.closed
    assert fake_sock.connect_called
    assert not t.is_open


def _fake_bt_socket(monkeypatch):
    import app.drivers.phomemo_transport as bt_module

    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.sendall_calls = []
            self.closed = False

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def connect(self, addr):
            pass

        def sendall(self, data):
            self.sendall_calls.append(bytes(data))

        def close(self):
            self.closed = True

    fake_sock = FakeSocket()

    class MockSocketModule:
        AF_BLUETOOTH = 31
        SOCK_STREAM = 1
        BTPROTO_RFCOMM = 3

        @staticmethod
        def socket(*args, **kwargs):
            return fake_sock

    monkeypatch.setattr(bt_module, "socket", MockSocketModule())
    return fake_sock


def test_bluetooth_write_timeout_outlives_connect_timeout(monkeypatch):
    """The 10s connect timeout must not govern sends: a multi-page job
    back-pressured to print speed stalls the link for well over 10s."""
    fake_sock = _fake_bt_socket(monkeypatch)

    t = BluetoothTransport("00:11:22:33:44:55")
    t.open()

    assert fake_sock.timeouts[0] == BluetoothTransport.CONNECT_TIMEOUT
    assert fake_sock.timeouts[-1] == BluetoothTransport.WRITE_TIMEOUT
    assert BluetoothTransport.WRITE_TIMEOUT > BluetoothTransport.CONNECT_TIMEOUT


def test_bluetooth_write_chunks_large_payloads(monkeypatch):
    """One giant sendall accumulates all print-speed stalls against a single
    timeout budget; chunked sends give each chunk a fresh budget."""
    fake_sock = _fake_bt_socket(monkeypatch)

    t = BluetoothTransport("00:11:22:33:44:55")
    t.open()

    payload = bytes(range(256)) * 1024  # 256 KiB
    t.write(payload)

    assert b"".join(fake_sock.sendall_calls) == payload
    assert len(fake_sock.sendall_calls) > 1
    assert all(
        len(c) <= BluetoothTransport.WRITE_CHUNK for c in fake_sock.sendall_calls
    )


# --- M220 raster protocol (Task 3) ---

from PIL import Image


def _driver_with_fake_transport(width_dots=456):
    from app.drivers.printer_phomemo import PrinterDriver

    class FakeTransport:
        def __init__(self):
            self.written = b""
            self.is_open = True
        def open(self): pass
        def write(self, data): self.written += data
        def close(self): self.is_open = False

    t = FakeTransport()
    d = PrinterDriver(width=49, transport=t, width_dots=width_dots)
    return d, t


def test_encode_bitmap_header_and_footer():
    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 10), 1)  # white
    data = d.encode_bitmap(img)
    assert data.startswith(b"\x1b\x4e\x0d\x05\x1b\x4e\x04\x0a\x1f\x11\x0b")
    assert data.endswith(b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00")


def test_encode_bitmap_raster_block_header():
    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 10), 1)
    data = d.encode_bitmap(img)
    # GS v 0, mode 0, 57 bytes/line LE, 10 lines LE
    assert b"\x1d\x76\x30\x00\x39\x00\x0a\x00" in data


def test_encode_bitmap_single_block_at_limit():
    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 1200), 1)
    data = d.encode_bitmap(img)
    assert data.count(b"\x1d\x76\x30\x00") == 1
    assert b"\x1d\x76\x30\x00\x39\x00\xb0\x04" in data  # 1200 lines, no divider


def _parse_blocks(data: bytes, bytes_per_line: int):
    """Return the line count of each GS v 0 block in encode order."""
    blocks = []
    pos = 0
    while True:
        pos = data.find(b"\x1d\x76\x30\x00", pos)
        if pos == -1:
            return blocks
        assert data[pos + 4 : pos + 6] == bytes_per_line.to_bytes(2, "little")
        lines = int.from_bytes(data[pos + 6 : pos + 8], "little")
        blocks.append(lines)
        pos += 8 + bytes_per_line * lines


def test_encode_bitmap_splits_and_swallows_gap_rows_over_1200_lines():
    from app.drivers.printer_phomemo import MAX_LINES_PER_BLOCK

    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 1500), 1)
    data = d.encode_bitmap(img)
    bytes_per_line = 456 // 8

    # All-blank image: the seam backs up a full gap's worth from the block
    # limit so the swallowed rows exactly cancel the firmware's inserted gap.
    gap = d.BLOCK_GAP_ROWS
    block1 = MAX_LINES_PER_BLOCK - gap
    assert _parse_blocks(data, bytes_per_line) == [
        block1,
        1500 - block1 - gap,
    ]
    # No dashed divider rows anymore.
    dash_row = bytes(0xFF if i % 2 == 0 else 0x00 for i in range(bytes_per_line))
    assert dash_row not in data


def test_encode_bitmap_seam_backs_up_through_blank_run_before_ink():
    from PIL import ImageDraw

    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 1500), 1)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 1150, 455, 1249], fill=0)  # ink across the split zone
    data = d.encode_bitmap(img)
    bytes_per_line = 456 // 8

    # Deepest blank row before the inked band is 1149. The seam backs up a
    # full gap so all 56 swallowed rows come out of the blank run above the
    # ink, and block 2 starts exactly at the ink.
    gap = d.BLOCK_GAP_ROWS
    block1 = 1150 - gap
    assert _parse_blocks(data, bytes_per_line) == [block1, 1500 - 1150]


def test_encode_bitmap_gap_swallow_stops_at_ink():
    from PIL import ImageDraw

    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 1500), 1)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 455, 1179], fill=0)  # solid ink above a small pocket
    draw.rectangle([0, 1200, 455, 1499], fill=0)  # and ink below it again
    data = d.encode_bitmap(img)
    bytes_per_line = 456 // 8

    # Only a 20-row blank pocket (1180-1199) exists: the seam sits at its
    # start and just those 19 rows after it may be swallowed — never ink.
    assert d.BLOCK_GAP_ROWS > 19
    assert _parse_blocks(data, bytes_per_line) == [1181, 1500 - 1181 - 19]


def test_encode_bitmap_black_pixel_sets_bit():
    d, _ = _driver_with_fake_transport(width_dots=8)
    img = Image.new("1", (8, 1), 1)
    img.putpixel((0, 0), 0)  # leftmost pixel black
    data = d.encode_bitmap(img)
    row = data[len(b"\x1b\x4e\x0d\x05\x1b\x4e\x04\x0a\x1f\x11\x0b") + 8 : -8]
    assert row == b"\x80"


def test_encode_bitmap_uses_configured_media_type(monkeypatch):
    import app.config as config
    from app.drivers.printer_phomemo import PrinterDriver

    class FakeTransport:
        def __init__(self):
            self.written = b""
            self.is_open = True
        def open(self): pass
        def write(self, data): self.written += data
        def close(self): self.is_open = False

    monkeypatch.setattr(config, "PHOMEMO_MEDIA_TYPE", 38)  # 38 = black marks
    d = PrinterDriver(width=49, transport=FakeTransport(), width_dots=456)
    img = Image.new("1", (456, 10), 1)
    data = d.encode_bitmap(img)
    assert b"\x1f\x11\x26" in data


def test_flush_buffer_propagates_transport_error():
    from app.drivers.printer_phomemo import PrinterDriver
    from app.drivers.phomemo_transport import TransportError

    class FailingTransport:
        is_open = True
        def open(self): pass
        def write(self, data):
            raise TransportError("printer unplugged")
        def close(self): pass

    d = PrinterDriver(width=49, transport=FailingTransport(), width_dots=456)
    d.print_body("hello")
    with pytest.raises(TransportError):
        d.flush_buffer()
