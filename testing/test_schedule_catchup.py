"""Tests for catching up on scheduled prints missed while the device was off."""

import asyncio
from datetime import datetime

import pytest
import pytz

import app.main as main_module
import app.schedule_state as schedule_state
from app.config import ChannelConfig, ScheduleRule
from app.schedule_utils import latest_fire_time_today

TZ = pytz.timezone("America/New_York")


def _at(hour, minute=0, day=31):
    return TZ.localize(datetime(2026, 8, day, hour, minute))


class TestLatestFireTimeToday:
    def test_returns_most_recent_past_fire(self):
        assert latest_fire_time_today(["0 8 * * *"], _at(14, 30)) == _at(8, 0)

    def test_returns_none_before_first_fire_of_day(self):
        assert latest_fire_time_today(["0 8 * * *"], _at(6, 15)) is None

    def test_ignores_fires_from_previous_day(self):
        assert latest_fire_time_today(["0 22 * * *"], _at(6, 15)) is None

    def test_picks_latest_across_expressions(self):
        expressions = ["0 8 * * *", "30 11 * * *", "0 18 * * *"]
        assert latest_fire_time_today(expressions, _at(14, 30)) == _at(11, 30)

    def test_includes_fire_in_the_current_minute(self):
        assert latest_fire_time_today(["0 8 * * *"], _at(8, 0)) == _at(8, 0)

    def test_skips_invalid_expressions(self):
        assert latest_fire_time_today(["not a cron", "0 8 * * *"], _at(14, 0)) == _at(8, 0)

    def test_no_expressions_yields_none(self):
        assert latest_fire_time_today([], _at(14, 0)) is None

    def test_weekday_rule_that_does_not_match_today_yields_none(self):
        # 2026-08-31 is a Monday; a Saturday-only rule has no fire today.
        assert latest_fire_time_today(["0 8 * * 6"], _at(14, 0)) is None


class TestScheduleState:
    @pytest.fixture(autouse=True)
    def _isolate_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            schedule_state, "_state_path", lambda: str(tmp_path / "schedule_state.json")
        )

    def test_roundtrips_a_recorded_fire(self):
        schedule_state.record_fired(3, _at(8, 0))
        assert schedule_state.load_last_fired() == {3: _at(8, 0)}

    def test_missing_file_reads_as_empty(self):
        assert schedule_state.load_last_fired() == {}

    def test_corrupt_file_reads_as_empty(self, tmp_path):
        (tmp_path / "schedule_state.json").write_text("{not json")
        assert schedule_state.load_last_fired() == {}

    def test_later_fire_replaces_earlier_one(self):
        schedule_state.record_fired(3, _at(8, 0))
        schedule_state.record_fired(3, _at(18, 0))
        assert schedule_state.load_last_fired()[3] == _at(18, 0)

    def test_channels_are_tracked_independently(self):
        schedule_state.record_fired(1, _at(8, 0))
        schedule_state.record_fired(2, _at(9, 0))
        assert schedule_state.load_last_fired() == {1: _at(8, 0), 2: _at(9, 0)}


def _run_catchup(monkeypatch, now, channels, *, busy_until_attempt=0):
    """Run the startup catch-up once, returning the trigger_channel calls."""
    triggered = []
    attempts = {"count": 0}

    async def fake_sleep(_seconds):
        return None

    async def fake_trigger_channel(position, *, scheduled=False, catchup_for=None):
        triggered.append((position, scheduled, catchup_for))

    def fake_try_begin_print_job(debounce=False):
        attempts["count"] += 1
        return attempts["count"] > busy_until_attempt

    monkeypatch.setattr(main_module, "current_datetime", lambda: now)
    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)
    monkeypatch.setattr(main_module, "try_begin_print_job", fake_try_begin_print_job)
    monkeypatch.setattr(main_module.settings, "channels", channels)

    asyncio.run(main_module.run_startup_schedule_catchup())
    return triggered


class TestStartupCatchup:
    @pytest.fixture(autouse=True)
    def _isolate_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            schedule_state, "_state_path", lambda: str(tmp_path / "schedule_state.json")
        )

    def test_prints_a_slot_missed_earlier_today(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == [(3, False, _at(8, 0))]

    def test_prints_only_once_for_several_missed_slots(self, monkeypatch):
        channels = {
            3: ChannelConfig(
                schedule_rules=[
                    ScheduleRule(expression="0 8 * * *"),
                    ScheduleRule(expression="0 10 * * *"),
                    ScheduleRule(expression="0 12 * * *"),
                ]
            )
        }

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == [(3, False, _at(12, 0))]

    def test_skips_a_slot_that_already_printed(self, monkeypatch):
        schedule_state.record_fired(3, _at(8, 0))
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == []

    def test_second_boot_the_same_day_does_not_reprint(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(monkeypatch, _at(14, 0), channels) != []
        assert _run_catchup(monkeypatch, _at(15, 0), channels) == []

    def test_yesterdays_fire_does_not_suppress_todays_catchup(self, monkeypatch):
        schedule_state.record_fired(3, _at(8, 0, day=30))
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == [(3, False, _at(8, 0))]

    def test_nothing_to_catch_up_before_the_first_slot(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(monkeypatch, _at(6, 0), channels) == []

    def test_disabled_rules_are_ignored(self, monkeypatch):
        channels = {
            3: ChannelConfig(
                schedule_rules=[ScheduleRule(expression="0 8 * * *", enabled=False)]
            )
        }

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == []

    def test_each_channel_catches_up_independently(self, monkeypatch):
        channels = {
            1: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")]),
            2: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 9 * * *")]),
        }

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == [
            (1, False, _at(8, 0)),
            (2, False, _at(9, 0)),
        ]

    def test_waits_for_a_busy_printer_then_prints(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(
            monkeypatch, _at(14, 0), channels, busy_until_attempt=3
        ) == [(3, False, _at(8, 0))]

    def test_gives_up_when_the_printer_never_frees_up(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        assert _run_catchup(monkeypatch, _at(14, 0), channels, busy_until_attempt=10**6) == []
        assert schedule_state.load_last_fired() == {}

    def test_catchup_does_not_use_the_prefetch_only_path(self, monkeypatch):
        # scheduled=True tells weather to use prefetched data only; a catch-up
        # print has no prefetch behind it, so it must fetch live instead.
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        (_, scheduled, _) = _run_catchup(monkeypatch, _at(14, 0), channels)[0]
        assert scheduled is False

    def test_legacy_hhmm_schedules_are_caught_up(self, monkeypatch):
        channels = {3: ChannelConfig(schedule=["08:00"])}

        assert _run_catchup(monkeypatch, _at(14, 0), channels) == [(3, False, _at(8, 0))]


class TestSchedulerRecordsFires:
    @pytest.fixture(autouse=True)
    def _isolate_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            schedule_state, "_state_path", lambda: str(tmp_path / "schedule_state.json")
        )

    def test_a_scheduled_print_is_recorded_so_catchup_skips_it(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}
        sleeps = {"count": 0}

        async def fake_sleep(_seconds):
            sleeps["count"] += 1
            if sleeps["count"] == 1:
                return None
            raise asyncio.CancelledError

        async def fake_trigger_channel(position, *, scheduled=False, catchup_for=None):
            return None

        monkeypatch.setattr(main_module, "current_datetime", lambda: _at(8, 0))
        monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)
        monkeypatch.setattr(main_module, "try_begin_print_job", lambda debounce=False: True)
        monkeypatch.setattr(main_module.settings, "channels", channels)
        monkeypatch.setattr(main_module, "_weather_prefetch_state", {})

        try:
            asyncio.run(main_module.scheduler_loop())
        except asyncio.CancelledError:
            pass

        assert schedule_state.load_last_fired() == {3: _at(8, 0)}
        assert _run_catchup(monkeypatch, _at(14, 0), channels) == []


def _run_scheduler(monkeypatch, channels, minutes, *, printer_up_from=None, fail_positions=()):
    """Drive scheduler_loop over ``minutes`` (datetimes), one tick each.

    The printer is unreachable until ``printer_up_from``; prints for
    ``fail_positions`` raise while it is down. Returns (triggered, probes).
    """
    from app.drivers.phomemo_transport import TransportError

    real_sleep = asyncio.sleep
    clock = {"now": minutes[0]}
    remaining = list(minutes)
    triggered = []
    probes = []

    def printer_up():
        return printer_up_from is not None and clock["now"] >= printer_up_from

    async def fake_sleep(_seconds):
        # Yield for real so catch-up tasks and probe threads get to run.
        await real_sleep(0.02)
        if not remaining:
            raise asyncio.CancelledError
        clock["now"] = remaining.pop(0)

    async def fake_trigger_channel(position, *, scheduled=False, catchup_for=None):
        if position in fail_positions and not printer_up():
            raise TransportError("bluetooth connect failed: [Errno 112] Host is down")
        triggered.append((position, scheduled, catchup_for))

    def fake_probe():
        probes.append(clock["now"])
        return printer_up()

    monkeypatch.setattr(main_module, "current_datetime", lambda: clock["now"])
    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main_module, "trigger_channel", fake_trigger_channel)
    monkeypatch.setattr(main_module, "try_begin_print_job", lambda debounce=False: True)
    monkeypatch.setattr(main_module, "_printer_is_available", fake_probe)
    monkeypatch.setattr(main_module.settings, "channels", channels)
    monkeypatch.setattr(main_module, "_weather_prefetch_state", {})

    try:
        asyncio.run(main_module.scheduler_loop())
    except asyncio.CancelledError:
        pass
    return triggered, probes


def _minutes(start, count):
    return [_at(start.hour, start.minute + offset) for offset in range(count)]


class TestCatchupWhenPrinterReturns:
    def test_missed_slot_prints_once_the_printer_is_back(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        triggered, _ = _run_scheduler(
            monkeypatch,
            channels,
            _minutes(_at(8, 0), 12),
            printer_up_from=_at(8, 5),
            fail_positions={3},
        )

        assert triggered == [(3, False, _at(8, 0))]
        assert schedule_state.load_last_fired() == {3: _at(8, 0)}

    def test_printer_is_not_probed_when_nothing_is_pending(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        triggered, probes = _run_scheduler(
            monkeypatch, channels, _minutes(_at(8, 0), 8), printer_up_from=_at(8, 0)
        )

        assert triggered == [(3, True, None)]
        assert probes == []

    def test_printer_is_probed_at_most_every_couple_of_minutes(self, monkeypatch):
        channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}

        _, probes = _run_scheduler(
            monkeypatch, channels, _minutes(_at(8, 0), 10), fail_positions={3}
        )

        assert probes
        gaps = [(later - earlier).total_seconds() for earlier, later in zip(probes, probes[1:])]
        assert all(gap >= main_module.SCHEDULE_RECONNECT_CHECK_SECONDS for gap in gaps)

    def test_a_failing_channel_does_not_skip_others_due_the_same_minute(self, monkeypatch):
        channels = {
            1: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")]),
            2: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")]),
        }

        triggered, _ = _run_scheduler(
            monkeypatch, channels, _minutes(_at(8, 0), 1), fail_positions={1}
        )

        assert triggered == [(2, True, None)]


def test_overlapping_catchup_runs_print_a_slot_once(monkeypatch):
    real_sleep = asyncio.sleep
    triggered = []

    async def slow_trigger_channel(position, *, scheduled=False, catchup_for=None):
        await real_sleep(0.05)
        triggered.append((position, catchup_for))

    channels = {3: ChannelConfig(schedule_rules=[ScheduleRule(expression="0 8 * * *")])}
    monkeypatch.setattr(main_module, "current_datetime", lambda: _at(14, 0))
    monkeypatch.setattr(main_module, "trigger_channel", slow_trigger_channel)
    monkeypatch.setattr(main_module, "try_begin_print_job", lambda debounce=False: True)
    monkeypatch.setattr(main_module.settings, "channels", channels)

    async def both():
        await asyncio.gather(
            main_module._run_schedule_catchup(), main_module._run_schedule_catchup()
        )

    asyncio.run(both())
    assert triggered == [(3, _at(8, 0))]
