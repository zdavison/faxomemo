import importlib


def test_paper_width_dots_default(monkeypatch):
    monkeypatch.delenv("PC1_PAPER_WIDTH_MM", raising=False)
    import app.config as config
    importlib.reload(config)
    assert config.PAPER_WIDTH_DOTS == 456


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
    assert data.startswith(b"\x1b\x4e\x0d\x05\x1b\x4e\x04\x0a\x1f\x11\x0a")
    assert data.endswith(b"\x1f\xf0\x05\x00\x1f\xf0\x03\x00")


def test_encode_bitmap_raster_block_header():
    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 10), 1)
    data = d.encode_bitmap(img)
    # GS v 0, mode 0, 57 bytes/line LE, 10 lines LE
    assert b"\x1d\x76\x30\x00\x39\x00\x0a\x00" in data


def test_encode_bitmap_splits_blocks_over_1200_lines():
    d, _ = _driver_with_fake_transport()
    img = Image.new("1", (456, 1500), 1)
    data = d.encode_bitmap(img)
    assert data.count(b"\x1d\x76\x30\x00") == 2  # 1200 + 300


def test_encode_bitmap_black_pixel_sets_bit():
    d, _ = _driver_with_fake_transport(width_dots=8)
    img = Image.new("1", (8, 1), 1)
    img.putpixel((0, 0), 0)  # leftmost pixel black
    data = d.encode_bitmap(img)
    row = data[len(b"\x1b\x4e\x0d\x05\x1b\x4e\x04\x0a\x1f\x11\x0a") + 8 : -8]
    assert row == b"\x80"
