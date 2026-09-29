import asyncio

import app.config as config
import app.main as main


class SpyPrinter:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record

    def method_names(self):
        return [name for name, _, _ in self.calls]


def test_env_flag_parsing(monkeypatch):
    monkeypatch.setenv("PC1_TEST_FLAG", "1")
    assert config.env_flag("PC1_TEST_FLAG") is True
    monkeypatch.setenv("PC1_TEST_FLAG", "true")
    assert config.env_flag("PC1_TEST_FLAG") is True
    monkeypatch.setenv("PC1_TEST_FLAG", "0")
    assert config.env_flag("PC1_TEST_FLAG") is False
    monkeypatch.delenv("PC1_TEST_FLAG")
    assert config.env_flag("PC1_TEST_FLAG") is False


def _run_check_first_boot(monkeypatch, tmp_path, skip: bool) -> SpyPrinter:
    marker = tmp_path / ".welcome_printed"
    marker.write_text("")
    spy = SpyPrinter()
    monkeypatch.setattr(main, "printer", spy)
    monkeypatch.setattr(main, "_get_welcome_marker_path", lambda: str(marker))
    monkeypatch.setattr(config, "SKIP_READY_PRINT", skip)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    asyncio.run(main.check_first_boot())
    return spy


def test_ready_receipt_skipped_with_flag(monkeypatch, tmp_path):
    spy = _run_check_first_boot(monkeypatch, tmp_path, skip=True)
    assert spy.calls == []


def test_ready_receipt_prints_without_flag(monkeypatch, tmp_path):
    spy = _run_check_first_boot(monkeypatch, tmp_path, skip=False)
    assert ("print_header", ("SYSTEM READY",), {"icon": "check", "icon_size": 28}) in spy.calls
