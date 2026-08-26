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
