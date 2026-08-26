import logging
import os
import socket

logger = logging.getLogger(__name__)


class TransportError(Exception):
    pass


class Transport:
    """Base transport interface for Phomemo devices."""

    @property
    def is_open(self) -> bool:
        return False

    def open(self):
        raise NotImplementedError

    def write(self, data: bytes):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class UsbTransport(Transport):
    def __init__(self, device_path: str):
        self.device_path = device_path
        self._fd = None

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def open(self):
        try:
            self._fd = os.open(self.device_path, os.O_WRONLY)
        except OSError as exc:
            self._fd = None
            logger.warning(f"cannot open {self.device_path}: {exc}")
            raise TransportError(f"cannot open {self.device_path}: {exc}") from exc

    def write(self, data: bytes):
        if self._fd is None:
            raise TransportError("USB transport not open")
        try:
            offset = 0
            while offset < len(data):
                written = os.write(self._fd, data[offset:])
                if written == 0:
                    raise TransportError("USB write returned 0 bytes")
                offset += written
        except OSError as exc:
            self.close()
            logger.warning(f"USB write failed: {exc}")
            raise TransportError(f"USB write failed: {exc}") from exc

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


class BluetoothTransport(Transport):
    def __init__(self, mac: str, channel: int = 1):
        self.mac = (mac or "").strip()
        self.channel = channel
        self._sock = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def open(self):
        if not self.mac:
            raise TransportError("PC1_PHOMEMO_BT_ADDRESS is not set")
        sock = None
        try:
            sock = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
            )
            sock.settimeout(10.0)
            sock.connect((self.mac, self.channel))
            self._sock = sock
        except OSError as exc:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._sock = None
            logger.warning(f"bluetooth connect {self.mac} failed: {exc}")
            raise TransportError(f"bluetooth connect {self.mac} failed: {exc}") from exc

    def write(self, data: bytes):
        if self._sock is None:
            raise TransportError("bluetooth transport not open")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            self.close()
            raise TransportError(f"bluetooth write failed: {exc}") from exc

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def make_transport(cfg):
    if cfg.PHOMEMO_TRANSPORT == "bluetooth":
        return BluetoothTransport(cfg.PHOMEMO_BT_ADDRESS)
    return UsbTransport(cfg.PHOMEMO_USB_DEVICE)
