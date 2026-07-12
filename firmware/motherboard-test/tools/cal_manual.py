#!/usr/bin/env python3
"""
cal_manual.py — guided manual DAC calibration against a source meter.

For each channel and a fixed set of commanded currents, this sends a single
`ISET g mA`, waits for the DAC to settle, and prompts you to type the current
your source meter reads. It logs everything to a CSV ready for the per-channel
slope/offset fit.

Unlike the GUI, this does NO continuous MEASA? polling, so the only traffic on
the serial line is your setpoint — the DAC updates immediately (no ~2 s queue
wait from the poller). Reading the current is the Keithley's job here, so the
ADC isn't used at all.

Run with the GUI CLOSED (the serial port is exclusive).

At each prompt:
    <number>  record the measured current (mA) and advance
    r         redo the previous point
    s         skip the rest of this channel
    q         quit now (saves what's been collected so far)

Usage:
    python3 cal_manual.py --channels 0-7
    python3 cal_manual.py --channels 0,1,2 --points 0.5,2.5,5.0
    python3 cal_manual.py --channels 0-7 --settle 0.6 --out board0_cal.csv
"""

import argparse
import csv
import sys
import time

from koi_gui import KoiLink, autodetect_port, DEFAULT_BAUD, MAX_CURRENT_MA


def parse_channels(spec):
    """'0-7', '0,1,5', '0-3,8,10-11' -> sorted unique list of ints."""
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return sorted(g for g in out if 0 <= g < 64)


def parse_points(spec):
    return [float(x) for x in spec.split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser(description="Guided manual DAC calibration")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--channels", required=True,
                    help="channels to calibrate, e.g. '0-7' or '0,1,5'")
    ap.add_argument("--points", default="0.5,2.5,5.0",
                    help="commanded currents in mA (comma-separated)")
    ap.add_argument("--settle", type=float, default=0.5,
                    help="seconds to wait after ISET before prompting")
    ap.add_argument("--out", default=None, help="CSV path (default: timestamped)")
    args = ap.parse_args()

    channels = parse_channels(args.channels)
    points = parse_points(args.points)
    if not channels:
        raise SystemExit("no valid channels parsed from --channels")
    over = [p for p in points if p > MAX_CURRENT_MA]
    if over:
        raise SystemExit(f"points {over} exceed the {MAX_CURRENT_MA:.3f} mA max")

    port = args.port or autodetect_port()
    if not port:
        raise SystemExit("No serial port found. Pass --port /dev/ttyACMx")
    print(f"Connecting to {port} @ {args.baud} …")
    link = KoiLink(port, args.baud)

    # Opening the port may reset the Pico; setup() runs a multi-second cal and
    # latches the front-ends ON at the end. Wait for READY before driving.
    print("waiting for board to be ready …")
    deadline = time.time() + 12.0
    while time.time() < deadline:
        ln = link.ser.readline().decode(errors="replace").strip()
        if ln:
            print("  ", ln)
        if ln == "READY":
            break
    time.sleep(0.3)
    link.ser.reset_input_buffer()
    print(link.command("*IDN?") or "(no IDN reply)")
    link.command("XTR 255")            # all front-ends enabled

    out_path = args.out or f"cal_manual_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    rows = []  # (channel, cmd_mA, meas_mA)

    print(f"\nchannels: {channels}")
    print(f"points (mA): {points}")
    print("enter measured mA, or  r=redo  s=skip channel  q=quit\n")

    quit_all = False
    try:
        for g in channels:
            if quit_all:
                break
            print(f"── channel {g} ──")
            i = 0
            while i < len(points):
                cmd = points[i]
                link.command(f"ISET {g} {cmd:.6f}")
                time.sleep(args.settle)
                resp = input(f"  ch{g}  cmd {cmd:7.3f} mA  → measured mA: ").strip().lower()
                if resp == "q":
                    quit_all = True
                    break
                if resp == "s":
                    break
                if resp == "r":
                    i = max(0, i - 1)
                    continue
                try:
                    meas = float(resp)
                except ValueError:
                    print("    (not a number — type a value, or r/s/q)")
                    continue
                rows.append((g, cmd, meas))
                i += 1
            link.command(f"ISET {g} 0")     # leave this channel at 0 before moving on
    finally:
        # Zero every channel we touched and drop current everywhere we can.
        for g in channels:
            link.command(f"ISET {g} 0")
        link.close()

    if not rows:
        print("\nno data collected.")
        return

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["channel", "cmd_mA", "meas_mA"])
        for g, cmd, meas in rows:
            w.writerow([g, f"{cmd:.6f}", f"{meas:.6f}"])
    print(f"\nsaved {len(rows)} rows to {out_path}")
    print("paste that CSV back and I'll fit calSlope/calOffset per channel.")


if __name__ == "__main__":
    main()
