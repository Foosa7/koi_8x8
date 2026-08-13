#!/usr/bin/env python3
"""
resolution_test.py — measure the Koi's voltage-measurement resolution (noise
floor) across the AD7193 sampling knobs, with a Keithley 2100 on the same node
as an independent reference.

For each (RATE, AVG) pair this takes N repeated single-channel reads at a fixed
commanded current and reports the 1-sigma spread, referred three ways: at the
ADC pin, at the load (x6), and as an equivalent current into the known load.
The 2100's own spread over the same interval bounds how much of the observed
noise is the source/load rather than the ADC.

Run with the GUI CLOSED. Front-end state is set once at the start and never
toggled (toggling XTR mid-session desyncs the ADC).

Usage:
    python3 resolution_test.py --channel 0 --rload 1005.68 --current 1.0
    python3 resolution_test.py --channel 0 --rload 1005.68 --current 3.0 -n 40
"""

import argparse
import statistics
import sys
import time

from koi_gui import KoiLink, autodetect_port, DEFAULT_BAUD
from keithley2100 import Keithley2100

# (RATE, AVG) pairs spanning fast-monitoring to careful-low-noise.
DEFAULT_MATRIX = [(8, 1), (16, 4), (96, 1), (96, 4), (96, 16), (240, 4)]

# Raw code 0 reads as -VREF in bipolar and 0.0 in unipolar; both mean a wedged
# ADC rather than a measurement. Check for either.
RAIL = -3.0
SETTLE_READS = 5


def wait_ready(link, timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ln = link.ser.readline().decode(errors="replace").strip()
        if ln and ln.lstrip("# ").strip() == "READY":
            return True
    return False


def stats(vals):
    m = statistics.fmean(vals)
    return m, (statistics.stdev(vals) if len(vals) > 1 else 0.0)


def main():
    ap = argparse.ArgumentParser(description="Koi measurement resolution vs RATE/AVG")
    ap.add_argument("--port"); ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--usbtmc")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--rload", type=float, required=True)
    ap.add_argument("--current", type=float, default=1.0, help="fixed setpoint, mA")
    ap.add_argument("-n", "--nreads", type=int, default=30)
    ap.add_argument("--nplc", type=float, default=10)
    args = ap.parse_args()

    g = args.channel
    board = g // 8

    dmm = Keithley2100(args.usbtmc)
    dmm.reset()
    dmm.config_vdc(nplc=args.nplc, rng=10, high_z=True)

    port = args.port or autodetect_port()
    link = KoiLink(port, args.baud)
    wait_ready(link)
    time.sleep(0.3); link.ser.reset_input_buffer()
    print(link.command("*IDN?"))
    link.command(f"XTR {1 << board}")
    link.command(f"ISET {g} {args.current}")
    time.sleep(2.0)

    print(f"\nchannel {g}, {args.current} mA into {args.rload} ohm, "
          f"{args.nreads} reads per setting\n")
    hdr = (f"{'RATE':>5} {'AVG':>4} {'t/read':>8} {'mean pin V':>12} "
           f"{'1sig pin':>9} {'1sig load':>10} {'1sig I':>9} {'2100 1sig':>10}")
    print(hdr); print("-" * len(hdr))

    for rate, avg in DEFAULT_MATRIX:
        link.command(f"RATE {rate}")
        link.command(f"AVG {avg}")
        time.sleep(1.5)
        # The AD7193 reconfigures on a RATE change and the first conversions
        # after it are invalid (observed: all-zero codes, or a 0.13 V spread
        # while settling). Throw away a few reads before timing/measuring.
        for _ in range(SETTLE_READS):
            link.command(f"MEAS? {g}")
        vals, t0 = [], time.time()
        for _ in range(args.nreads):
            r = link.command(f"MEAS? {g}")
            if r:
                try:
                    vals.append(float(r))
                except ValueError:
                    pass
        dt = (time.time() - t0) / max(len(vals), 1)
        if not vals:
            print(f"{rate:5d} {avg:4d}   (no readings)"); continue
        if all(abs(v - RAIL) < 1e-6 for v in vals) or all(v == 0.0 for v in vals):
            print(f"{rate:5d} {avg:4d}   ADC WEDGED (raw code 0)"); continue
        m, sd = stats(vals)
        dmm_vals = dmm.read_n(min(args.nreads, 15))
        _, dsd = stats(dmm_vals)
        print(f"{rate:5d} {avg:4d} {dt*1e3:7.1f}ms {m:12.7f} "
              f"{sd*1e6:8.2f}u {sd*6e6:9.2f}u {sd*6/args.rload*1e9:8.1f}n "
              f"{dsd*1e6:9.2f}u")

    link.command(f"ISET {g} 0")
    link.command("XTR 0")
    link.close(); dmm.close()


if __name__ == "__main__":
    main()
