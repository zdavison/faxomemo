"""Berlin transit departures via the BVG REST API (v6.bvg.transport.rest)."""

import logging
import threading
import time
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

import app.config
from app.config import current_datetime, format_print_datetime, format_time
from app.module_registry import register_module

logger = logging.getLogger(__name__)

TRANSIT_API_BASE = "https://v6.bvg.transport.rest"
TRANSIT_USER_AGENT = "faxomemo-transit (https://github.com/zdavison/faxomemo)"
TRANSIT_REQUEST_TIMEOUT = 10
TRANSIT_REQUEST_ATTEMPTS = 2
TRANSIT_RETRY_BACKOFF_SECONDS = 1
TRANSIT_LINE_WIDTH = 32
NEARBY_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60

PRODUCT_KEYS = ("suburban", "subway", "tram", "bus", "ferry", "regional")

_NEARBY_CACHE_LOCK = threading.Lock()
_NEARBY_CACHE: Dict[Tuple[float, float, int, int], Dict[str, Any]] = {}


class TransitFetchError(Exception):
    """Raised when the BVG API cannot be reached or returns bad data."""


def clear_nearby_cache() -> None:
    with _NEARBY_CACHE_LOCK:
        _NEARBY_CACHE.clear()


def _request_json(path: str, params: Dict[str, Any]) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, TRANSIT_REQUEST_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"{TRANSIT_API_BASE}{path}",
                params=params,
                timeout=TRANSIT_REQUEST_TIMEOUT,
                headers={"User-Agent": TRANSIT_USER_AGENT},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Transit request failed: path=%s attempt=%s error=%s",
                path,
                attempt,
                exc,
            )
            if attempt < TRANSIT_REQUEST_ATTEMPTS:
                time.sleep(TRANSIT_RETRY_BACKOFF_SECONDS)
    raise TransitFetchError(str(last_error) if last_error else "Transit request failed")


def get_nearby_stops(
    latitude: float, longitude: float, max_distance: int, results: int
) -> List[Dict[str, Any]]:
    """Return the nearest stops, cached in process for 24 hours."""
    cache_key = (latitude, longitude, max_distance, results)
    now_monotonic = time.monotonic()

    with _NEARBY_CACHE_LOCK:
        entry = _NEARBY_CACHE.get(cache_key)
        if entry and (now_monotonic - entry["stored_at"]) <= NEARBY_CACHE_MAX_AGE_SECONDS:
            return list(entry["stops"])

    payload = _request_json(
        "/locations/nearby",
        {
            "latitude": latitude,
            "longitude": longitude,
            "distance": max_distance,
            "results": results,
        },
    )
    if not isinstance(payload, list):
        raise TransitFetchError("Unexpected nearby-stops response")

    stops = [item for item in payload if isinstance(item, dict) and item.get("type") == "stop"]
    with _NEARBY_CACHE_LOCK:
        _NEARBY_CACHE[cache_key] = {"stops": list(stops), "stored_at": time.monotonic()}
    return stops


def get_departures(
    stop_id: str, duration: int, products: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    products = products or {}
    params: Dict[str, Any] = {"duration": duration}
    for key in PRODUCT_KEYS:
        params[key] = "true" if products.get(key, True) else "false"
    # The API also has an "express" product (IC/ICE); tie it to the regional toggle.
    params["express"] = params["regional"]

    payload = _request_json(f"/stops/{stop_id}/departures", params)
    if not isinstance(payload, dict):
        raise TransitFetchError("Unexpected departures response")
    departures = payload.get("departures")
    if not isinstance(departures, list):
        raise TransitFetchError("Unexpected departures response")
    return departures


# The serial driver strips non-ASCII before printing, so emit ASCII here or the
# column padding computed below drifts on paper.
_GERMAN_TRANSLITERATIONS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def _ascii(text: str) -> str:
    text = str(text).translate(_GERMAN_TRANSLITERATIONS)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if 0x20 <= ord(c) <= 0x7E)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _departure_time(dep: Dict[str, Any]) -> Optional[datetime]:
    """Effective departure time: realtime `when`, falling back to `plannedWhen`."""
    return _parse_iso(dep.get("when")) or _parse_iso(dep.get("plannedWhen"))


def _minutes_until(dt: datetime, now: datetime) -> int:
    if now.tzinfo is None and dt.tzinfo is not None:
        now = now.replace(tzinfo=dt.tzinfo)
    elif now.tzinfo is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    return max(int((dt - now).total_seconds() // 60), 0)


def _format_departure_line(dep: Dict[str, Any], now: Optional[datetime] = None) -> str:
    now = now or current_datetime()
    line_name = (dep.get("line") or {}).get("name") or "?"
    direction = dep.get("direction") or ""

    if dep.get("cancelled"):
        planned = _parse_iso(dep.get("plannedWhen")) or _departure_time(dep)
        planned_label = format_time(planned) if planned else "--:--"
        right = f"{planned_label} cancelled"
    else:
        dt = _departure_time(dep)
        if dt is None:
            right = "--"
        else:
            right = f"{_minutes_until(dt, now)} min"
            delay = dep.get("delay")
            if delay is not None:
                right += f" {int(round(delay / 60)):+d}"

    left = _ascii(f"{line_name} -> {direction}").rstrip()
    available = TRANSIT_LINE_WIDTH - len(right) - 2
    if len(left) > available:
        left = left[: max(available, 1)].rstrip()
    return f"{left:<{available}}  {right}"


def _format_stop_subheader(stop: Dict[str, Any]) -> str:
    name = _ascii(str(stop.get("name") or "Stop")).upper()
    distance = stop.get("distance")
    if distance is None:
        return name
    return f"{name} - {int(distance)}M"


def _resolve_location(config: Dict[str, Any]) -> Tuple[float, float, bool]:
    """Return (latitude, longitude, from_address)."""
    address = config.get("address") or {}
    latitude = address.get("latitude")
    longitude = address.get("longitude")
    if latitude is not None and longitude is not None:
        return float(latitude), float(longitude), True
    return (
        float(app.config.settings.latitude),
        float(app.config.settings.longitude),
        False,
    )


def _int_config(config: Dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(value, hi))


_PRODUCT_SCHEMA = {
    "type": "object",
    "title": "Products",
    "properties": {
        "suburban": {"type": "boolean", "title": "S-Bahn", "default": True},
        "subway": {"type": "boolean", "title": "U-Bahn", "default": True},
        "tram": {"type": "boolean", "title": "Tram", "default": True},
        "bus": {"type": "boolean", "title": "Bus", "default": True},
        "ferry": {"type": "boolean", "title": "Ferry", "default": True},
        "regional": {"type": "boolean", "title": "Regional", "default": True},
    },
}


@register_module(
    type_id="transit",
    label="Berlin Transit",
    description="Next departures from the nearest BVG stops",
    icon="train",
    offline=False,
    category="content",
    config_schema={
        "type": "object",
        "properties": {
            "stop_count": {
                "type": "integer",
                "title": "Nearest Stops",
                "description": "How many nearby stops to print",
                "default": 3,
                "minimum": 1,
                "maximum": 5,
            },
            "departures_per_stop": {
                "type": "integer",
                "title": "Departures per Stop",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
            "duration": {
                "type": "integer",
                "title": "Look-ahead Window (minutes)",
                "default": 30,
                "minimum": 1,
                "maximum": 240,
            },
            "max_distance": {
                "type": "integer",
                "title": "Walking Distance (meters)",
                "default": 500,
                "minimum": 50,
                "maximum": 5000,
            },
            "products": _PRODUCT_SCHEMA,
            "address": {
                "type": "object",
                "title": "Address",
                "description": "Precise address for nearby-stop search (device location is city-level)",
                "properties": {
                    "city_name": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "timezone": {"type": "string"},
                    "state": {"type": "string"},
                    "zipcode": {"type": "string"},
                },
                "required": ["city_name", "latitude", "longitude"],
            },
        },
    },
    ui_schema={"address": {"ui:widget": "location-search"}},
)
def format_transit_receipt(
    printer, config: Dict[str, Any] = None, module_name: str = None
):
    """Prints upcoming departures for the stops nearest the configured address."""
    config = config or {}

    stop_count = _int_config(config, "stop_count", 3, 1, 5)
    departures_per_stop = _int_config(config, "departures_per_stop", 5, 1, 10)
    duration = _int_config(config, "duration", 30, 1, 240)
    max_distance = _int_config(config, "max_distance", 500, 50, 5000)
    products = config.get("products") or {}

    printer.print_header(module_name or "TRANSIT", icon="train")
    printer.print_caption(format_print_datetime())

    latitude, longitude, from_address = _resolve_location(config)
    if not from_address:
        printer.print_caption("Using device location. Set a precise address in module settings.")
    printer.print_line()

    try:
        stops = get_nearby_stops(latitude, longitude, max_distance, stop_count)
    except TransitFetchError as exc:
        printer.print_body("Transit data unavailable.")
        printer.print_caption(str(exc)[:96])
        printer.feed(1)
        return

    stops = stops[:stop_count]
    if not stops:
        printer.print_body(f"No stops found within {max_distance}m.")
        printer.feed(1)
        return

    now = current_datetime()
    for index, stop in enumerate(stops):
        printer.print_subheader(_format_stop_subheader(stop))

        try:
            departures = get_departures(str(stop.get("id")), duration, products)
        except TransitFetchError as exc:
            printer.print_body("Departures unavailable.")
            printer.print_caption(str(exc)[:96])
            continue

        timed = [d for d in departures if _departure_time(d) is not None]
        timed.sort(key=lambda d: _departure_time(d))
        timed = timed[:departures_per_stop]

        if not timed:
            printer.print_body(f"No departures in the next {duration} min.")
        for dep in timed:
            printer.print_body(_format_departure_line(dep, now))

        if index < len(stops) - 1:
            printer.print_line()

    printer.feed(1)
