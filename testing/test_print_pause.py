"""Tests for the global printing on/off switch."""

import asyncio
from datetime import datetime

import pytest
import pytz

import app.config as config_module
import app.hardware as hardware
import app.main as main_module
import app.schedule_state as schedule_state
from app.config import ChannelConfig, ScheduleRule

TZ = pytz.timezone("America/New_York")


def _at(hour, minute=0, day=31):
    return TZ.localize(datetime(2026, 8, day, hour, minute))


@pytest.fixture(autouse=True)
def _shared_settings(monkeypatch):
    """Earlier tests may swap main's settings out; the gate reads config's."""
    monkeypatch.setattr(main_module, "settings", config_module.settings)


@pytest.fixture(autouse=True)
def _clean_reservations():
    hardware.clear_print_reservation()
    hardware.last_print_time = 0.0
    yield
    hardware.clear_print_reservation()
    hardware.last_print_time = 0.0


@pytest.fixture
def paused(monkeypatch):
    monkeypatch.setattr(config_module.settings, "printing_enabled", False)


@pytest.fixture
def resumed(monkeypatch):
    monkeypatch.setattr(config_module.settings, "printing_enabled", True)


class TestSettingDefault:
    def test_printing_is_enabled_by_default(self):
        assert config_module.Settings().printing_enabled is True


class TestReservationGate:
    def test_automatic_job_is_refused_while_paused(self, paused):
        assert hardware.try_begin_print_job(debounce=False) is False

    def test_refusal_leaves_the_printer_unreserved(self, paused):
        hardware.try_begin_print_job(debounce=False)
        with hardware.print_lock:
            assert hardware.printer_reserved_locked() is False

    def test_manual_job_still_prints_while_paused(self, paused):
        assert hardware.try_begin_print_job(debounce=False, manual=True) is True

    def test_manual_job_still_respects_an_active_reservation(self, paused):
        assert hardware.try_begin_print_job(debounce=False, manual=True) is True
        assert hardware.try_begin_print_job(debounce=False, manual=True) is False

    def test_automatic_job_runs_once_resumed(self, resumed):
        assert hardware.try_begin_print_job(debounce=False) is True

    def test_missing_setting_is_treated_as_enabled(self, monkeypatch):
        monkeypatch.delattr(config_module.settings, "printing_enabled", raising=False)
        assert hardware.try_begin_print_job(debounce=False) is True

    def test_printing_paused_reports_the_current_setting(self, paused):
        assert hardware.printing_paused() is True


class TestMissedSlotsCatchUpOnResume:
    @pytest.fixture(autouse=True)
    def _one_scheduled_channel(self, monkeypatch):
        channel = ChannelConfig(
            name="Morning",
            modules=[],
            schedule_rules=[ScheduleRule(expression="0 8 * * *", enabled=True)],
        )
        monkeypatch.setattr(main_module.settings, "channels", {1: channel})

    def test_slot_passed_while_paused_is_pending_on_resume(self):
        assert main_module._pending_schedule_catchups(_at(9, 0)) == [(1, _at(8, 0))]

    def test_slot_already_printed_is_not_pending(self):
        schedule_state.record_fired(1, _at(8, 0))
        assert main_module._pending_schedule_catchups(_at(9, 0)) == []

    def test_catch_up_does_not_run_while_still_paused(self, paused, monkeypatch):
        triggered = []

        async def _fake_trigger(pos, **kwargs):
            triggered.append(pos)

        monkeypatch.setattr(main_module, "trigger_channel", _fake_trigger)
        asyncio.run(main_module._run_schedule_catchup())
        assert triggered == []

    def test_catch_up_prints_the_missed_slot_once_resumed(self, resumed, monkeypatch):
        triggered = []

        async def _fake_trigger(pos, **kwargs):
            triggered.append((pos, kwargs.get("catchup_for")))

        monkeypatch.setattr(main_module, "trigger_channel", _fake_trigger)
        monkeypatch.setattr(main_module, "current_datetime", lambda: _at(9, 0))
        asyncio.run(main_module._run_schedule_catchup())
        assert triggered == [(1, _at(8, 0))]
        assert schedule_state.load_last_fired() == {1: _at(8, 0)}


class TestPrintingApi:
    def test_get_reports_the_current_state(self, paused):
        assert asyncio.run(main_module.get_printing_enabled()) == {"enabled": False}

    def test_post_pauses_printing(self, resumed, monkeypatch):
        monkeypatch.setattr(main_module, "save_config", lambda snapshot: None)
        result = asyncio.run(
            main_module.set_printing_enabled(
                main_module.PrintingEnabledRequest(enabled=False)
            )
        )
        assert result == {"enabled": False}
        assert main_module.settings.printing_enabled is False
        assert hardware.try_begin_print_job(debounce=False) is False

    def test_resuming_schedules_a_catch_up(self, paused, monkeypatch):
        monkeypatch.setattr(main_module, "save_config", lambda snapshot: None)
        catch_ups = []

        async def _fake_catch_up():
            catch_ups.append(True)

        monkeypatch.setattr(main_module, "_run_schedule_catchup", _fake_catch_up)

        async def _run():
            result = await main_module.set_printing_enabled(
                main_module.PrintingEnabledRequest(enabled=True)
            )
            await asyncio.sleep(0)
            return result

        assert asyncio.run(_run()) == {"enabled": True}
        assert catch_ups == [True]

    def test_pausing_does_not_schedule_a_catch_up(self, resumed, monkeypatch):
        monkeypatch.setattr(main_module, "save_config", lambda snapshot: None)
        catch_ups = []

        async def _fake_catch_up():
            catch_ups.append(True)

        monkeypatch.setattr(main_module, "_run_schedule_catchup", _fake_catch_up)

        async def _run():
            await main_module.set_printing_enabled(
                main_module.PrintingEnabledRequest(enabled=False)
            )
            await asyncio.sleep(0)

        asyncio.run(_run())
        assert catch_ups == []
