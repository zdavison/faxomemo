"""Tests for the universal show_header module option."""

import app.modules  # noqa: F401  (populates the module registry)
from app.module_registry import (
    execute_module_by_type,
    list_module_types,
    register_module,
)


class _RecordingPrinter:
    PRINTER_WIDTH_DOTS = 384

    def __init__(self):
        self.lines = []

    def print_header(self, text, *args, **kwargs):  # noqa: ARG002
        self.lines.append(("header", str(text)))

    def print_subheader(self, text):
        self.lines.append(("subheader", str(text)))

    def print_body(self, text):
        self.lines.append(("body", str(text)))

    def print_text(self, text, *args, **kwargs):  # noqa: ARG002
        self.lines.append(("text", str(text)))

    def print_caption(self, text):
        self.lines.append(("caption", str(text)))

    def print_line(self):
        self.lines.append(("line", ""))


def test_every_module_schema_offers_show_header():
    modules = list_module_types()
    assert modules, "registry should not be empty"
    for module in modules:
        schema = module["configSchema"]
        assert schema is not None, f"{module['id']} has no config schema"
        prop = schema.get("properties", {}).get("show_header")
        assert prop is not None, f"{module['id']} schema missing show_header"
        assert prop["type"] == "boolean"
        assert prop["default"] is True


def _register_probe_module():
    @register_module(type_id="_show_header_probe", label="Probe")
    def _probe(printer, config=None, module_name=None):  # noqa: ARG001
        printer.print_header(module_name or "PROBE")
        printer.print_body("probe body")


def test_show_header_false_suppresses_header_only():
    _register_probe_module()
    printer = _RecordingPrinter()
    assert execute_module_by_type(
        "_show_header_probe", printer, {"show_header": False}, "PROBE"
    )
    assert ("header", "PROBE") not in printer.lines
    assert ("body", "probe body") in printer.lines


def test_show_header_default_prints_header():
    _register_probe_module()
    printer = _RecordingPrinter()
    assert execute_module_by_type("_show_header_probe", printer, {}, "PROBE")
    assert ("header", "PROBE") in printer.lines


def test_execute_module_special_case_respects_show_header(monkeypatch):
    import app.main as main_module
    from app.config import ModuleInstance

    printer = _RecordingPrinter()
    monkeypatch.setattr(main_module, "printer", printer)

    module = ModuleInstance(
        id="t1",
        type="text",
        name="MY NOTE",
        config={
            "content_doc": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "hello"}],
                    }
                ],
            },
            "show_header": False,
        },
    )
    assert main_module.execute_module(module)
    assert not [entry for entry in printer.lines if entry[0] == "header"]
    assert any("hello" in text for kind, text in printer.lines)
