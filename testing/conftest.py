import pytest

import app.schedule_state as schedule_state


@pytest.fixture(autouse=True)
def isolate_schedule_state(monkeypatch, tmp_path):
    """Keep scheduler tests from writing device state into the repo."""
    monkeypatch.setattr(
        schedule_state, "_state_path", lambda: str(tmp_path / "schedule_state.json")
    )
