"""Tests for the optional phomymo label designer mount."""

import asyncio
import io

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import app.main as main_module


def _make_designer(root):
    web_dir = root / "vendor" / "phomymo" / "src" / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "index.html").write_text("<html>phomymo</html>", encoding="utf-8")
    return web_dir


def test_label_designer_dir_is_none_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    assert main_module.label_designer_dir() is None


def test_label_designer_dir_is_none_without_index_html(monkeypatch, tmp_path):
    (tmp_path / "vendor" / "phomymo" / "src" / "web").mkdir(parents=True)
    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    assert main_module.label_designer_dir() is None


def test_label_designer_dir_found_when_installed(monkeypatch, tmp_path):
    web_dir = _make_designer(tmp_path)
    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    assert main_module.label_designer_dir() == web_dir


def test_status_reports_unavailable_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    status = asyncio.run(main_module.get_label_designer_status())

    assert status == {"available": False, "url": "/labels/"}


def test_status_reports_available_when_installed(monkeypatch, tmp_path):
    _make_designer(tmp_path)
    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    status = asyncio.run(main_module.get_label_designer_status())

    assert status == {"available": True, "url": "/labels/"}


def test_spa_catch_all_does_not_swallow_labels_path():
    """Without the designer installed, /labels must 404 rather than render the React app."""
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main_module.serve_frontend("labels/index.html"))

    assert excinfo.value.status_code == 404


def test_spa_catch_all_still_serves_client_routes():
    response = asyncio.run(main_module.serve_frontend("settings"))

    assert response.path.endswith("index.html")


# --- Direct printing from the designer ---


class _FakeRequest:
    def __init__(
        self,
        body: bytes,
        content_type: str = "image/png",
        label_width_mm="40",
        continuous=None,
    ):
        headers = {"content-type": content_type}
        if label_width_mm is not None:
            headers["x-label-width-mm"] = str(label_width_mm)
        if continuous is not None:
            headers["x-label-continuous"] = str(continuous)
        # Starlette headers are case-insensitive; a plain dict is not.
        self.headers = Headers(headers)
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _FakePrinter:
    def __init__(self):
        self.printed = []
        self.reset_lines = None
        self.flushed = False
        self.media_type = 11  # the device default: continuous roll
        self.media_during_print = []
        self.aligned = []

    def reset_buffer(self, max_lines):
        self.reset_lines = max_lines

    def print_image(self, image, align="center"):
        self.printed.append(image)
        self.aligned.append(align)
        self.media_during_print.append(self.media_type)

    def flush_buffer(self):
        self.flushed = True


def _png_bytes(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def printer_stub(monkeypatch):
    printer = _FakePrinter()
    released = []
    monkeypatch.setattr(main_module, "printer", printer)
    monkeypatch.setattr(main_module, "try_begin_print_job", lambda **kwargs: True)
    monkeypatch.setattr(
        main_module, "clear_print_reservation", lambda **kwargs: released.append(kwargs)
    )
    printer.released = released
    return printer


def test_print_label_rejects_non_image_content_type(printer_stub):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main_module.print_label(_FakeRequest(b"hello", "text/plain")))

    assert excinfo.value.status_code == 400
    assert printer_stub.printed == []


def test_print_label_rejects_svg(printer_stub):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main_module.print_label(_FakeRequest(b"<svg/>", "image/svg+xml")))

    assert excinfo.value.status_code == 400


def test_print_label_rejects_corrupt_image(printer_stub):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main_module.print_label(_FakeRequest(b"not a png", "image/png")))

    assert excinfo.value.status_code == 400
    assert printer_stub.released == []


def test_print_label_conflicts_when_printer_busy(monkeypatch, printer_stub):
    monkeypatch.setattr(main_module, "try_begin_print_job", lambda **kwargs: False)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(800, 400))))

    assert excinfo.value.status_code == 409
    assert printer_stub.printed == []


def test_print_label_prints_at_the_designed_physical_size(printer_stub):
    """A 40x30mm label must print 40x30mm, whatever pixel scale the designer exported."""
    from app import config

    dots_per_mm = config.PAPER_WIDTH_DOTS / config.PAPER_WIDTH_MM
    png = _png_bytes(40 * 32, 30 * 32)  # phomymo renders 8 px/mm, exported at 4x

    result = asyncio.run(main_module.print_label(_FakeRequest(png, label_width_mm="40")))

    assert result == {"status": "ok"}
    assert len(printer_stub.printed) == 1
    assert printer_stub.printed[0].width == round(40 * dots_per_mm)
    assert printer_stub.printed[0].height == round(30 * dots_per_mm)
    assert printer_stub.flushed is True
    assert printer_stub.released == [{"clear_hold": False}]


def test_print_label_clamps_a_label_wider_than_the_paper(printer_stub):
    from app import config

    png = _png_bytes(80 * 32, 40 * 32)

    asyncio.run(main_module.print_label(_FakeRequest(png, label_width_mm="80")))

    assert printer_stub.printed[0].width == config.PAPER_WIDTH_DOTS


def test_print_label_requires_the_label_width(printer_stub):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            main_module.print_label(_FakeRequest(_png_bytes(320, 240), label_width_mm=None))
        )

    assert excinfo.value.status_code == 400
    assert printer_stub.printed == []


@pytest.mark.parametrize("value", ["", "wide", "0", "-5", "5000"])
def test_print_label_rejects_an_unusable_label_width(printer_stub, value):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            main_module.print_label(_FakeRequest(_png_bytes(320, 240), label_width_mm=value))
        )

    assert excinfo.value.status_code == 400
    assert printer_stub.printed == []


def test_print_label_releases_reservation_when_printing_fails(printer_stub):
    def boom(image, align="center"):
        raise RuntimeError("printer offline")

    printer_stub.print_image = boom

    with pytest.raises(RuntimeError):
        asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(800, 400))))

    assert printer_stub.released == [{"clear_hold": False}]


# --- Bridge script injection ---


def test_index_injects_the_bridge_script(monkeypatch, tmp_path):
    web_dir = _make_designer(tmp_path)
    (web_dir / "index.html").write_text("<html><body><h1>hi</h1></body></html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    html = main_module.label_designer_index_html()

    assert html.count("faxomemo-bridge.js") == 1
    assert html.endswith("</body></html>")


def test_bridge_script_file_exists():
    assert main_module.LABEL_BRIDGE_SCRIPT.is_file()


# --- Die-cut versus continuous stock ---


def test_a_sized_label_prints_in_gap_mode(printer_stub):
    """A 40x30mm label is die-cut stock, so the printer must index to the gap."""
    from app import config

    asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(1280, 960), continuous="false")))

    assert printer_stub.media_during_print == [config.MEDIA_TYPE_GAP]


def test_gap_mode_is_the_default_for_the_designer(printer_stub):
    from app import config

    asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(1280, 960))))

    assert printer_stub.media_during_print == [config.MEDIA_TYPE_GAP]


def test_a_continuous_label_prints_in_continuous_mode(printer_stub):
    from app import config

    asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(1280, 960), continuous="true")))

    assert printer_stub.media_during_print == [config.MEDIA_TYPE_CONTINUOUS]


def test_the_device_media_type_is_restored_after_a_label(printer_stub):
    printer_stub.media_type = 38  # black marks, so the restore is visible

    asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(1280, 960), continuous="false")))

    assert printer_stub.media_type == 38


def test_the_device_media_type_is_restored_when_printing_fails(printer_stub):
    def boom(image, align="center"):
        raise RuntimeError("printer offline")

    printer_stub.print_image = boom

    with pytest.raises(RuntimeError):
        asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(1280, 960))))

    assert printer_stub.media_type == 11


@pytest.mark.parametrize("value", ["yes", "1", ""])
def test_print_label_rejects_an_unusable_continuous_flag(printer_stub, value):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            main_module.print_label(_FakeRequest(_png_bytes(1280, 960), continuous=value))
        )

    assert excinfo.value.status_code == 400
    assert printer_stub.printed == []


def test_a_label_is_printed_flush_against_the_paper_edge(printer_stub):
    """Centring a narrow label would push it off the die-cut stock."""
    asyncio.run(main_module.print_label(_FakeRequest(_png_bytes(1280, 960))))

    assert printer_stub.aligned == ["left"]
