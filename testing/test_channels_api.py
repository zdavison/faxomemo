"""Tests for dynamic channel create/delete endpoints and their range guards."""

import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

import app.main as main_module
from app.config import ChannelConfig, ChannelModuleAssignment, ModuleInstance, Settings


def _settings_with_channels(channel_positions):
    settings = Settings(modules={}, channels={})
    settings.channels = {pos: ChannelConfig(modules=[]) for pos in channel_positions}
    return settings


def test_create_channel_appends_max_plus_one_respecting_gaps(monkeypatch):
    settings = _settings_with_channels([1, 2, 5])
    monkeypatch.setattr(main_module, "settings", settings)

    response = asyncio.run(main_module.create_channel(BackgroundTasks()))

    assert response["position"] == 6
    assert 6 in main_module.settings.channels
    assert main_module.settings.channels[6].modules == []


def test_create_channel_starts_at_one_when_empty(monkeypatch):
    settings = _settings_with_channels([])
    monkeypatch.setattr(main_module, "settings", settings)

    response = asyncio.run(main_module.create_channel(BackgroundTasks()))

    assert response["position"] == 1
    assert 1 in main_module.settings.channels


def test_delete_channel_removes_existing_key(monkeypatch):
    settings = _settings_with_channels([1, 2, 5])
    monkeypatch.setattr(main_module, "settings", settings)

    asyncio.run(main_module.delete_channel(2, BackgroundTasks()))

    assert 2 not in main_module.settings.channels
    assert sorted(main_module.settings.channels.keys()) == [1, 5]


def test_delete_channel_404_when_missing(monkeypatch):
    settings = _settings_with_channels([1, 2, 5])
    monkeypatch.setattr(main_module, "settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.delete_channel(3, BackgroundTasks()))

    assert exc_info.value.status_code == 404


def test_assign_module_to_channel_accepts_position_that_exists(monkeypatch):
    module_id = "mod-1"
    settings = _settings_with_channels([12])
    settings.modules = {module_id: ModuleInstance(id=module_id, type="text", name="Note", config={})}
    monkeypatch.setattr(main_module, "settings", settings)

    response = asyncio.run(
        main_module.assign_module_to_channel(12, module_id, BackgroundTasks())
    )

    assert response["message"] == "Module assigned to channel"
    assert main_module.settings.channels[12].modules[0].module_id == module_id


def test_assign_module_to_channel_rejects_position_zero(monkeypatch):
    module_id = "mod-1"
    settings = _settings_with_channels([])
    settings.modules = {module_id: ModuleInstance(id=module_id, type="text", name="Note", config={})}
    monkeypatch.setattr(main_module, "settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.assign_module_to_channel(0, module_id, BackgroundTasks()))

    assert exc_info.value.status_code == 400


def test_assign_module_to_channel_404_when_channel_does_not_exist(monkeypatch):
    module_id = "mod-1"
    settings = _settings_with_channels([])
    settings.modules = {module_id: ModuleInstance(id=module_id, type="text", name="Note", config={})}
    monkeypatch.setattr(main_module, "settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.assign_module_to_channel(12, module_id, BackgroundTasks()))

    assert exc_info.value.status_code == 404


def test_set_dial_accepts_position_beyond_eight(monkeypatch):
    settings = _settings_with_channels([12])
    monkeypatch.setattr(main_module, "settings", settings)

    response = asyncio.run(main_module.set_dial(12))

    assert response == {"message": "Dial turned to 12"}


def test_set_dial_rejects_position_zero(monkeypatch):
    settings = _settings_with_channels([])
    monkeypatch.setattr(main_module, "settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.set_dial(0))

    assert exc_info.value.status_code == 400


def test_print_channel_accepts_position_beyond_eight(monkeypatch):
    settings = _settings_with_channels([12])
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(main_module, "try_begin_print_job", lambda debounce=False: True)

    response = asyncio.run(main_module.print_channel(12, BackgroundTasks()))

    assert response == {"message": "Printing channel 12"}


def test_print_channel_rejects_position_zero(monkeypatch):
    settings = _settings_with_channels([])
    monkeypatch.setattr(main_module, "settings", settings)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.print_channel(0, BackgroundTasks()))

    assert exc_info.value.status_code == 400
