#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Diagnose the ~2-page truncation on long M220 prints.

Sends a tall ruler pattern over Bluetooth in small chunks and logs the
pacing of each write. Interpretation:

- If the whole job is accepted in a few seconds while the printer needs
  minutes to physically print it, there is no end-to-end back-pressure:
  the printer is buffering blindly and drops everything past its buffer
  (the ~2-page truncation).
- If writes slow down to roughly print speed partway through, RFCOMM
  flow control works and the truncation has another cause.

The ruler encodes position: a tick every 100 rows whose width grows with
the row number, and a full-width bar at each 1200-row block boundary.
Read the last printed tick to see exactly where output stopped.

Usage (stop the faxomemo daemon first -- it may hold the RFCOMM link):
    ./diag_long_print_timing.py --lines 3600
    ./diag_long_print_timing.py --lines 3600 --paced   # throttled variant
"""

import argparse
import os
import sys
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.drivers.phomemo_transport import BluetoothTransport  # noqa: E402
from app.drivers.printer_phomemo import PrinterDriver  # noqa: E402

CHUNK = 4096
PRINT_SPEED_LPS = 75  # M220 spec ~50mm/s at 8 dots/mm is optimistic; assume less


def build_ruler(width: int, height: int, dense: bool = False) -> Image.Image:
    img = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(img)
    if dense:
        # ~50% coverage everywhere: slows the physical print the way real
        # content (graphics, grids) does, to stress write stalls.
        for x in range(0, width, 16):
            draw.rectangle([x, 0, x + 7, height - 1], fill=0)
    for row in range(0, height, 100):
        tick_w = min(width, 20 + (row // 100) * 8)
        draw.rectangle([0, row, tick_w, row + 2], fill=0)
    for row in range(0, height, 1200):
        draw.rectangle([0, row, width - 1, row + 4], fill=0)
    draw.rectangle([0, height - 5, width - 1, height - 1], fill=0)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=3600)
    ap.add_argument("--mac", default=os.environ.get("PC1_PHOMEMO_BT_ADDRESS", ""))
    ap.add_argument("--paced", action="store_true",
                    help="throttle writes to ~print speed instead of blasting")
    ap.add_argument("--dense", action="store_true",
                    help="50%% ink coverage to slow the printer like real content")
    args = ap.parse_args()

    if not args.mac:
        for envfile in (".faxomemo.env", os.path.expanduser("~/.faxomemo.env")):
            if os.path.exists(envfile):
                for line in open(envfile):
                    if line.startswith("PC1_PHOMEMO_BT_ADDRESS="):
                        args.mac = line.split("=", 1)[1].strip()
    if not args.mac:
        sys.exit("no MAC: pass --mac or set PC1_PHOMEMO_BT_ADDRESS")

    driver = PrinterDriver(transport=BluetoothTransport(args.mac))
    img = build_ruler(driver.PRINTER_WIDTH_DOTS, args.lines, dense=args.dense)
    data = driver.encode_bitmap(img)

    bytes_per_line = driver.PRINTER_WIDTH_DOTS // 8
    paced_bps = PRINT_SPEED_LPS * bytes_per_line
    est_print_s = args.lines / PRINT_SPEED_LPS
    print(f"job: {args.lines} lines, {len(data)} bytes; "
          f"physical print time ~{est_print_s:.0f}s")

    t = driver.transport
    t.open()
    start = time.monotonic()
    sent = 0
    failed = None
    try:
        for off in range(0, len(data), CHUNK):
            chunk = data[off:off + CHUNK]
            t0 = time.monotonic()
            try:
                t.write(chunk)
            except Exception as exc:
                failed = exc
                print(f"{time.monotonic() - start:8.2f}s  WRITE FAILED at "
                      f"{sent}/{len(data)} bytes (~line {sent // bytes_per_line}): {exc}")
                break
            t1 = time.monotonic()
            sent += len(chunk)
            print(f"{t1 - start:8.2f}s  sent {sent:7d}/{len(data)}  "
                  f"chunk took {t1 - t0:6.3f}s")
            if args.paced:
                target = start + sent / paced_bps
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
    finally:
        elapsed = time.monotonic() - start
        print(f"\ntransfer done in {elapsed:.1f}s "
              f"(physical print needs ~{est_print_s:.0f}s)")
        if failed is not None:
            print("=> write failed mid-job: this is the truncation mechanism"
                  " (socket timeout / link error), not a printer buffer drop.")
        elif not args.paced and elapsed < est_print_s * 0.5:
            print("=> link accepted data far faster than the printer can print:"
                  " no end-to-end back-pressure; buffer overflow is plausible.")
        elif not args.paced:
            print("=> writes throttled to ~print speed: flow control works;"
                  " look elsewhere for the truncation cause.")
        print("waiting for printing to finish before closing the socket...")
        time.sleep(max(10.0, est_print_s - elapsed + 15.0))
        t.close()


if __name__ == "__main__":
    main()
