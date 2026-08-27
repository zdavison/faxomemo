"""Tests for the Berlin transit departures module (BVG)."""

from datetime import datetime, timedelta, timezone

import pytest
import requests

from app.module_registry import get_module, validate_module_config
from app.modules import transit


BERLIN_TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=BERLIN_TZ)


def _iso(minutes_from_now: int) -> str:
    return (NOW + timedelta(minutes=minutes_from_now)).isoformat()


def _dep(
    line: str = "U8",
    direction: str = "Hermannstr.",
    when_min=None,
    planned_min=None,
    delay=None,
    cancelled: bool = False,
    product: str = "subway",
):
    return {
        "tripId": f"trip-{line}-{direction}-{when_min}-{planned_min}",
        "direction": direction,
        "line": {"name": line, "product": product},
        "when": _iso(when_min) if when_min is not None else None,
        "plannedWhen": _iso(planned_min) if planned_min is not None else None,
        "delay": delay,
        "cancelled": cancelled,
    }


def _stop(stop_id="900100001", name="Rosenthaler Platz", distance=180):
    return {
        "type": "stop",
        "id": stop_id,
        "name": name,
        "location": {"latitude": 52.53, "longitude": 13.40},
        "products": {
            "suburban": True,
            "subway": True,
            "tram": True,
            "bus": True,
            "ferry": False,
            "express": False,
            "regional": False,
        },
        "distance": distance,
    }


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _Printer:
    def __init__(self):
        self.entries = []

    def print_header(self, text, *args, **kwargs):
        self.entries.append(("header", str(text)))

    def print_subheader(self, text):
        self.entries.append(("subheader", str(text)))

    def print_body(self, text):
        self.entries.append(("body", str(text)))

    def print_caption(self, text):
        self.entries.append(("caption", str(text)))

    def print_line(self):
        self.entries.append(("line", ""))

    def print_thick_line(self):
        self.entries.append(("thick_line", ""))

    def feed(self, n=1):
        self.entries.append(("feed", str(n)))

    def of(self, kind):
        return [text for k, text in self.entries if k == kind]


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    transit.clear_nearby_cache()
    monkeypatch.setattr(transit, "current_datetime", lambda: NOW)
    monkeypatch.setattr(transit.time, "sleep", lambda _s: None)
    yield
    transit.clear_nearby_cache()


def _mock_api(monkeypatch, nearby=None, departures=None, calls=None):
    """Route transit.requests.get by URL to canned payloads (or callables)."""

    def fake_get(url, params=None, timeout=None, headers=None, **kwargs):
        record = {
            "url": url,
            "params": dict(params or {}),
            "timeout": timeout,
            "headers": dict(headers or {}),
        }
        if calls is not None:
            calls.append(record)
        if "/locations/nearby" in url:
            source = nearby
        elif "/departures" in url:
            source = departures
        else:
            raise AssertionError(f"Unexpected URL: {url}")
        if callable(source):
            return source(record)
        return _Resp(source)

    monkeypatch.setattr(transit.requests, "get", fake_get)


# --- Departure line formatting ---


def test_departure_line_realtime_with_delay():
    line = transit._format_departure_line(
        _dep(when_min=10, planned_min=8, delay=120), NOW
    )
    assert "U8" in line
    assert "Hermannstr." in line
    assert line.rstrip().endswith("10 min +2")


def test_departure_line_planned_fallback_without_realtime():
    line = transit._format_departure_line(
        _dep(when_min=None, planned_min=5, delay=None), NOW
    )
    assert "5 min" in line
    assert "+" not in line


def test_departure_line_zero_delay_shows_plus_zero():
    line = transit._format_departure_line(
        _dep(when_min=3, planned_min=3, delay=0), NOW
    )
    assert line.rstrip().endswith("3 min +0")


def test_departure_line_cancelled_shows_planned_time():
    line = transit._format_departure_line(
        _dep(when_min=None, planned_min=5, delay=None, cancelled=True), NOW
    )
    assert "cancelled" in line
    assert "12:05" in line
    assert "min" not in line


def test_departure_line_past_departure_clamps_to_zero_minutes():
    line = transit._format_departure_line(
        _dep(when_min=-2, planned_min=-2, delay=None), NOW
    )
    assert "0 min" in line


def test_departure_line_fits_receipt_width():
    line = transit._format_departure_line(
        _dep(
            line="M41",
            direction="Sonnenallee/Baumschulenstr. via Hermannplatz",
            when_min=7,
            delay=300,
        ),
        NOW,
    )
    assert len(line) <= transit.TRANSIT_LINE_WIDTH
    assert line.rstrip().endswith("7 min +5")


def test_departure_line_is_ascii_with_german_transliteration():
    # The serial driver strips non-ASCII, so the module must emit ASCII itself
    # or padding math breaks on paper.
    line = transit._format_departure_line(
        _dep(line="M8", direction="Herzbergstraße/Süd", when_min=4, delay=0), NOW
    )
    assert line.isascii()
    assert "->" in line
    assert "Herzbergstrasse" in line


def test_subheader_text_is_ascii():
    text = transit._format_stop_subheader(_stop(name="Schönhauser Allee"))
    assert text.isascii()
    assert text == "SCHOENHAUSER ALLEE - 180M"


# --- Nearby-stop cache ---


def test_nearby_stops_cached_for_repeat_queries(monkeypatch):
    calls = []
    _mock_api(monkeypatch, nearby=[_stop()], calls=calls)

    first = transit.get_nearby_stops(52.53, 13.40, 500, 3)
    second = transit.get_nearby_stops(52.53, 13.40, 500, 3)

    assert first == second
    assert len(calls) == 1


def test_nearby_stops_cache_key_includes_query(monkeypatch):
    calls = []
    _mock_api(monkeypatch, nearby=[_stop()], calls=calls)

    transit.get_nearby_stops(52.53, 13.40, 500, 3)
    transit.get_nearby_stops(52.53, 13.40, 800, 3)

    assert len(calls) == 2


def test_nearby_stops_cache_expires(monkeypatch):
    calls = []
    _mock_api(monkeypatch, nearby=[_stop()], calls=calls)
    fake_now = [1000.0]
    monkeypatch.setattr(transit.time, "monotonic", lambda: fake_now[0])

    transit.get_nearby_stops(52.53, 13.40, 500, 3)
    fake_now[0] += transit.NEARBY_CACHE_MAX_AGE_SECONDS + 1
    transit.get_nearby_stops(52.53, 13.40, 500, 3)

    assert len(calls) == 2


# --- HTTP behavior ---


def test_requests_send_user_agent_and_timeout(monkeypatch):
    calls = []
    _mock_api(monkeypatch, nearby=[_stop()], calls=calls)

    transit.get_nearby_stops(52.53, 13.40, 500, 3)

    call = calls[0]
    assert "faxomemo" in call["headers"].get("User-Agent", "")
    assert call["timeout"] == transit.TRANSIT_REQUEST_TIMEOUT
    assert call["params"]["latitude"] == 52.53
    assert call["params"]["longitude"] == 13.40
    assert call["params"]["distance"] == 500
    assert call["params"]["results"] == 3


def test_request_retries_once_then_succeeds(monkeypatch):
    calls = []

    def flaky(record):
        if len(calls) == 1:
            raise requests.ConnectionError("boom")
        return _Resp([_stop()])

    _mock_api(monkeypatch, nearby=flaky, calls=calls)

    stops = transit.get_nearby_stops(52.53, 13.40, 500, 3)

    assert len(calls) == 2
    assert stops[0]["name"] == "Rosenthaler Platz"


def test_request_failure_after_retries_raises(monkeypatch):
    def always_down(record):
        raise requests.ConnectionError("connection refused")

    _mock_api(monkeypatch, nearby=always_down)

    with pytest.raises(transit.TransitFetchError):
        transit.get_nearby_stops(52.53, 13.40, 500, 3)


def test_departures_product_filters_map_to_query_params(monkeypatch):
    calls = []
    _mock_api(monkeypatch, departures={"departures": []}, calls=calls)

    transit.get_departures(
        "900100001",
        duration=30,
        products={"suburban": True, "subway": True, "tram": False,
                  "bus": False, "ferry": True, "regional": False},
    )

    params = calls[0]["params"]
    assert params["duration"] == 30
    assert params["suburban"] == "true"
    assert params["subway"] == "true"
    assert params["tram"] == "false"
    assert params["bus"] == "false"
    assert params["ferry"] == "true"
    assert params["regional"] == "false"
    assert params["express"] == "false"


# --- Receipt rendering ---


def _receipt_config(**overrides):
    config = {
        "address": {
            "city_name": "Weinbergsweg 1, Berlin",
            "latitude": 52.532,
            "longitude": 13.401,
        },
    }
    config.update(overrides)
    return config


def test_receipt_happy_path(monkeypatch):
    departures = {
        "departures": [
            _dep("U8", "Hermannstr.", when_min=3, planned_min=3, delay=120),
            _dep("U8", "Wittenau", when_min=1, planned_min=1, delay=None),
        ]
    }
    _mock_api(monkeypatch, nearby=[_stop()], departures=departures)
    printer = _Printer()

    transit.format_transit_receipt(printer, _receipt_config(), "TRANSIT")

    assert printer.of("header") == ["TRANSIT"]
    assert any("ROSENTHALER PLATZ" in s and "180M" in s for s in printer.of("subheader"))
    bodies = printer.of("body")
    assert any("Wittenau" in b for b in bodies)
    assert any("Hermannstr." in b and "+2" in b for b in bodies)
    # Sorted by departure time: Wittenau (1 min) before Hermannstr. (3 min).
    assert bodies.index(next(b for b in bodies if "Wittenau" in b)) < bodies.index(
        next(b for b in bodies if "Hermannstr." in b)
    )


def test_receipt_sorts_and_limits_departures(monkeypatch):
    deps = [_dep("U8", f"Dest{i}", when_min=10 - i, planned_min=10 - i) for i in range(6)]
    _mock_api(monkeypatch, nearby=[_stop()], departures={"departures": deps})
    printer = _Printer()

    transit.format_transit_receipt(
        printer, _receipt_config(departures_per_stop=2), "TRANSIT"
    )

    dep_bodies = [b for b in printer.of("body") if "Dest" in b]
    assert len(dep_bodies) == 2
    assert "Dest5" in dep_bodies[0]
    assert "Dest4" in dep_bodies[1]


def test_receipt_total_failure(monkeypatch):
    def down(record):
        raise requests.ConnectionError("connection refused")

    _mock_api(monkeypatch, nearby=down)
    printer = _Printer()

    transit.format_transit_receipt(printer, _receipt_config(), "TRANSIT")

    assert "Transit data unavailable." in printer.of("body")
    assert printer.of("caption")  # reason caption present


def test_receipt_no_stops_in_range(monkeypatch):
    _mock_api(monkeypatch, nearby=[])
    printer = _Printer()

    transit.format_transit_receipt(printer, _receipt_config(max_distance=300), "TRANSIT")

    assert any("No stops found within 300m." in b for b in printer.of("body"))


def test_receipt_per_stop_failure_keeps_other_stops(monkeypatch):
    good = _stop("900100001", "Rosenthaler Platz", 180)
    bad = _stop("900100002", "Weinmeisterstr.", 320)

    def departures(record):
        if "900100002" in record["url"]:
            raise requests.ConnectionError("boom")
        return _Resp({"departures": [_dep(when_min=4, planned_min=4)]})

    _mock_api(monkeypatch, nearby=[good, bad], departures=departures)
    printer = _Printer()

    transit.format_transit_receipt(printer, _receipt_config(stop_count=2), "TRANSIT")

    subheaders = printer.of("subheader")
    assert len(subheaders) == 2
    bodies = printer.of("body")
    assert any("Hermannstr." in b for b in bodies)
    assert any("unavailable" in b.lower() for b in bodies)


def test_receipt_device_location_fallback_prints_hint(monkeypatch):
    _mock_api(monkeypatch, nearby=[_stop()], departures={"departures": []})
    monkeypatch.setattr(transit.app.config.settings, "latitude", 52.52, raising=False)
    monkeypatch.setattr(transit.app.config.settings, "longitude", 13.405, raising=False)
    printer = _Printer()

    transit.format_transit_receipt(printer, {}, "TRANSIT")

    assert any("address" in c.lower() for c in printer.of("caption"))


def test_receipt_with_address_prints_no_hint(monkeypatch):
    _mock_api(monkeypatch, nearby=[_stop()], departures={"departures": []})
    printer = _Printer()

    transit.format_transit_receipt(printer, _receipt_config(), "TRANSIT")

    assert not any("address" in c.lower() for c in printer.of("caption") if "," not in c)


# --- Registration and schema ---


def test_module_registered_with_expected_metadata():
    defn = get_module("transit")
    assert defn is not None
    assert defn.offline is False
    assert defn.config_schema is not None
    assert defn.ui_schema["address"]["ui:widget"] == "location-search"


def test_config_schema_accepts_defaults_and_full_config():
    validate_module_config("transit", {})
    validate_module_config(
        "transit",
        {
            "stop_count": 5,
            "departures_per_stop": 10,
            "duration": 60,
            "max_distance": 800,
            "products": {
                "suburban": True,
                "subway": False,
                "tram": True,
                "bus": True,
                "ferry": False,
                "regional": True,
            },
            "address": {
                "city_name": "Weinbergsweg 1, Berlin",
                "latitude": 52.532,
                "longitude": 13.401,
                "timezone": "Europe/Berlin",
            },
        },
    )


def test_config_schema_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        validate_module_config("transit", {"stop_count": 9})
    with pytest.raises(ValueError):
        validate_module_config("transit", {"departures_per_stop": 0})
