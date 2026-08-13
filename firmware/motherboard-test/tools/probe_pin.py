#!/usr/bin/env python3
"""Watch one board's 8 raw ADC-pin voltages while you force a known voltage.

Debug aid for "I inject V at an AD7193 input pin and the GUI cell is nowhere
close". Prints all 8 channels of a board side by side so you can see *which*
cell (if any) tracks the forced voltage — that separates an input-mapping
problem from a scaling/config one in a single glance.

Close the GUI first: the Koi serial port is exclusive.

    source /home/foosa/jax-env/jax_env/bin/activate
    python3 probe_pin.py --board 0

Front-ends are disabled and all setpoints zeroed at start (*RST) so nothing on
the board fights the source. Ctrl-C to stop.
"""

import argparse
import math
import sys
import time

from koi_gui import KoiLink, autodetect_port

# Reads to throw away after a RATE/AVG change — matches the bench runs.
DISCARD_AFTER_SETTING_CHANGE = 5


def stats(link, n, scan_all, board, interval):
    """Take `n` scans and report mean / 1-sigma / peak-to-peak per channel.

    This is the number that answers "is it noise or is it broken": compare the
    1-sigma column against the ~11 uV expected at RATE 16 / AVG 4. Orders of
    magnitude above that is a connection problem, not something AVG will fix.
    """
    print(f"# collecting {n} scans...")
    acc = {}
    for i in range(n):
        vals = link.measure_all() if scan_all else link.measure_mask(1 << board)
        if vals is None:
            print(f"#   scan {i}: no reply")
            continue
        rng = range(64) if scan_all else range(board * 8, board * 8 + 8)
        for g in rng:
            v = vals[g]
            if v == v:
                acc.setdefault(g, []).append(v)
        if interval:
            time.sleep(interval)

    print()
    print(f"{'ch':>5}{'mean mV':>13}{'1-sigma uV':>13}{'p-p uV':>11}{'n':>5}")
    print("-" * 47)
    for g in sorted(acc):
        s = acc[g]
        mean = sum(s) / len(s)
        if len(s) > 1:
            var = sum((x - mean) ** 2 for x in s) / (len(s) - 1)
            sigma = math.sqrt(var) * 1e6
        else:
            sigma = float("nan")
        pp = (max(s) - min(s)) * 1e6
        print(f"{g:>5}{mean * 1e3:13.4f}{sigma:13.2f}{pp:11.2f}{len(s):5d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--board", default="0",
                    help="daughterboard index 0..7, or 'all' for the whole 8x8 grid")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between scans")
    ap.add_argument("--no-rst", action="store_true",
                    help="skip the *RST (leave front-ends/setpoints as they are)")
    ap.add_argument("--rate", type=int, help="AD7193 filter word 1..1023 (default keeps current)")
    ap.add_argument("--avg", type=int, help="on-micro averages 1..64 (default keeps current)")
    ap.add_argument("--rej60", choices=("ON", "OFF"),
                    help="simultaneous 50/60 Hz notch rejection — the mains-hum test")
    ap.add_argument("--chop", choices=("ON", "OFF"),
                    help="chopper offset/drift cancellation (halves throughput)")
    ap.add_argument("--buf", choices=("ON", "OFF"),
                    help="AD7193 input buffer (fw1.3). OFF is only valid with a "
                         "low-impedance source on the pin — not for normal use")
    ap.add_argument("--stats", type=int, metavar="N",
                    help="instead of streaming, take N scans and print mean/1-sigma per channel")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        raise SystemExit("No serial port found. Pass --port /dev/ttyACMx")

    link = KoiLink(port)
    print(f"# port   {port}")
    print(f"# idn    {link.command('*IDN?')}")

    if not args.no_rst:
        print(f"# rst    {link.command('*RST')}")
    if args.rate:
        print(f"# rate   {link.command(f'RATE {args.rate}')}")
    if args.avg:
        print(f"# avg    {link.command(f'AVG {args.avg}')}")
    if args.rej60:
        print(f"# rej60  {link.command(f'REJ60 {args.rej60}')}")
    if args.chop:
        print(f"# chop   {link.command(f'CHOP {args.chop}')}")
    if args.buf:
        r = link.command(f"BUF {args.buf}")
        print(f"# buf    {r}")
        if r and r.startswith("ERR"):
            print("#          (firmware predates fw1.3 — BUF is not tunable there)")
    print(f"# adc    {link.command('ADC?')}")

    # The AD7193's first conversions after a RATE/GAIN write are invalid; the
    # bench runs discard 5. Do the same so the first row printed is trustworthy.
    if args.rate or args.avg or args.rej60 or args.chop or args.buf:
        for _ in range(DISCARD_AFTER_SETTING_CHANGE):
            link.measure_all()
        print(f"# (discarded {DISCARD_AFTER_SETTING_CHANGE} settling scans)")

    scan_all = str(args.board).lower() == "all"
    if scan_all:
        print("# board  all (full 8x8 grid — one scan is ~0.9 s at RATE 16/AVG 4)")
    else:
        b = int(args.board)
        g0 = b * 8
        print(f"# board  {b}  (global channels {g0}..{g0 + 7})")
    print("#")
    print("# raw ADC-pin voltage, mV.  Force a known V on one pin and watch which")
    print("# cell follows it.  Discard the first few scans after any ADC setting")
    print("# change — the AD7193's first conversions after a RATE/GAIN write are")
    print("# invalid.")
    print()

    def fmt(v):
        return f"{'nan':>11}" if v != v else f"{v * 1e3:11.3f}"

    if args.stats:
        stats(link, args.stats, scan_all, None if scan_all else b, args.interval)
        link.close()
        return

    if not scan_all:
        hdr = "    time  " + "".join(f"{f'ch{i}':>11}" for i in range(8))
        print(hdr)
        print("-" * len(hdr))

    t0 = time.time()
    try:
        while True:
            vals = link.measure_all() if scan_all else link.measure_mask(1 << b)
            if vals is None:
                print("  (no reply)")
            elif scan_all:
                print(f"t = {time.time() - t0:.1f} s")
                print("        " + "".join(f"{f'ch{i}':>11}" for i in range(8)))
                for bb in range(8):
                    row = vals[bb * 8:bb * 8 + 8]
                    print(f"  brd{bb} " + "".join(fmt(v) for v in row))
                print()
            else:
                row = vals[g0:g0 + 8]
                print(f"{time.time() - t0:8.1f}  " + "".join(fmt(v) for v in row))
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n# stopped")
    finally:
        link.close()


if __name__ == "__main__":
    main()
