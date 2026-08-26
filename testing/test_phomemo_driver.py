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
