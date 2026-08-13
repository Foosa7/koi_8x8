#!/usr/bin/env python3
"""
vsweep_dmm.py — current sweep on one channel, voltage measured BOTH ways.

At each commanded current the Koi reports its raw AD7193 pin voltage while a
Keithley 2100 sitting across the load reports the true node voltage. Fitting
one against the other separates the two terms in the measure path:

    koi_raw = V_node / ratio + offset

`ratio` is the sense divider (nominal 6.0; docs/measure-path-offset.md pins it
at 6.0017 by a desoldered-XTR200 Keithley 2410 force) and `offset` is the open
~-3.4 mV additive measure-side term. This script re-runs that experiment
in-situ, through a live XTR200, on any channel.

Wiring: 2100 HI/LO across the load resistor (the heater node and its ground
return). Leave it on DCV with high-Z input — the script configures that.

Run with the GUI CLOSED (the serial port is exclusive).

Usage:
    python3 vsweep_dmm.py --channel 0 --rload 1005.68
    python3 vsweep_dmm.py --channel 0 --rload 1005.68 --points 0,0.5,1,2,3,4,5,6
    python3 vsweep_dmm.py --channel 0 --rload 1005.68 --nplc 100 --nreads 10
"""

import argparse
import csv
import os
import statistics
import sys
import time

from koi_bench import bench_outdir
from koi_gui import KoiLink, autodetect_port, DEFAULT_BAUD, MAX_CURRENT_MA
from keithley2100 import Keithley2100, autodetect_usbtmc

# The 100k+20k sense divider hangs off the node and steals V_node/120k from the
# load. Known a-priori, not fitted — see CLAUDE.md.
DIVIDER_TOTAL_OHMS = 120000.0

# Reference values from docs/measure-path-offset.md, printed alongside the fit
# so a run is immediately comparable to the desoldered-XTR200 bench result.
REF_RATIO = 6.0017
REF_OFFSET_V = -3.372e-3


def parse_points(spec, imax):
    """'0,0.5,1' -> list of floats; 'start:stop:step' -> an inclusive ramp."""
    if ":" in spec:
        start, stop, step = (float(x) for x in spec.split(":"))
        # Build by index so float accumulation doesn't drift the last point.
        n = int(round((stop - start) / step))
        pts = [start + i * step for i in range(n + 1)]
    else:
        pts = [float(x) for x in spec.split(",") if x.strip()]
    over = [p for p in pts if p > imax]
    if over:
        raise SystemExit(f"points {over} exceed the {imax:.3f} mA max")
    return pts


def linfit(xs, ys):
    """Least-squares y = m*x + b. Returns (m, b, r2)."""
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return float("nan"), float("nan"), float("nan")
    m = sxy / sxx
    b = my - m * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy * sxy) / (sxx * syy) if syy else float("nan")
    return m, b, r2


def mean_sd(vals):
    m = statistics.fmean(vals)
    return m, (statistics.stdev(vals) if len(vals) > 1 else 0.0)


def wait_ready(link, timeout=12.0):
    """Opening the port resets the Pico; its setup() runs a multi-second cal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ln = link.ser.readline().decode(errors="replace").strip()
        if ln:
            print("  ", ln)
        if ln.lstrip("# ").strip() == "READY":
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Koi-vs-Keithley voltage sweep")
    ap.add_argument("--port", help="Koi serial port (default: auto-detect)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--usbtmc", help="DMM device node (default: auto-detect)")
    ap.add_argument("--channel", type=int, default=0,
                    help="global channel index g = board*8 + ch (default 0)")
    ap.add_argument("--rload", type=float, required=True,
                    help="load resistance in ohms (4-wire verified)")
    ap.add_argument("--points", default="0:6:0.5",
                    help="mA list 'a,b,c' or ramp 'start:stop:step' (default 0:6:0.5)")
    ap.add_argument("--settle", type=float, default=1.0,
                    help="seconds after ISET before measuring (default 1.0)")
    ap.add_argument("--nreads", type=int, default=5,
                    help="readings per point on each instrument (default 5)")
    ap.add_argument("--nplc", type=float, default=10,
                    help="2100 integration time in power-line cycles (default 10)")
    ap.add_argument("--dmm-range", type=float, default=None,
                    help="fixed 2100 DCV range in volts (default: autorange)")
    ap.add_argument("--rate", type=int, default=96,
                    help="AD7193 filter word FS (default 96 = slow/quiet)")
    ap.add_argument("--avg", type=int, default=4,
                    help="firmware on-micro averages (default 4)")
    ap.add_argument("--bipolar", action="store_true",
                    help="BIPOLAR ON — lets a negative residual read as a signed "
                         "value instead of clamping at 0 (needed near zero drive)")
    ap.add_argument("--cal", action="store_true",
                    help="run CAL after the polarity/rate setup — the AD7193's "
                         "offset register is mode-dependent, so a boot-time "
                         "unipolar cal is stale once BIPOLAR is on")
    ap.add_argument("--chop", action="store_true",
                    help="CHOP ON — chopper offset/drift cancellation (2x slower)")
    ap.add_argument("--invert-dmm", action="store_true",
                    help="negate DMM readings (HI/LO leads swapped on the load)")
    ap.add_argument("--out", default=None, help="CSV path (default: timestamped)")
    args = ap.parse_args()
    dmm_sign = -1.0 if args.invert_dmm else 1.0

    g = args.channel
    board = g // 8
    points = parse_points(args.points, MAX_CURRENT_MA)

    # ── DMM ───────────────────────────────────────────────────────────────
    dmm_path = args.usbtmc or autodetect_usbtmc()
    if not dmm_path:
        raise SystemExit("no /dev/usbtmc* found — see tools/setup_usbtmc.md")
    print(f"DMM  : {dmm_path}")
    dmm = Keithley2100(dmm_path)
    print(f"       {dmm.idn()}")
    dmm.reset()
    dmm.config_vdc(nplc=args.nplc, rng=args.dmm_range, autozero=True, high_z=True)
    errs = dmm.errors()
    if errs:
        print(f"       SCPI errors after config: {errs}")

    # ── Koi ───────────────────────────────────────────────────────────────
    port = args.port or autodetect_port()
    if not port:
        dmm.close()
        raise SystemExit("No Koi serial port found. Pass --port /dev/ttyACMx")
    print(f"Koi  : {port} @ {args.baud}")
    link = KoiLink(port, args.baud)
    print("waiting for board to be ready …")
    wait_ready(link)
    time.sleep(0.3)
    link.ser.reset_input_buffer()
    print(f"       {link.command('*IDN?') or '(no IDN reply)'}")

    setup = [f"RATE {args.rate}", f"AVG {args.avg}",
             f"BIPOLAR {'ON' if args.bipolar else 'OFF'}",
             f"CHOP {'ON' if args.chop else 'OFF'}",
             f"XTR {1 << board}"]
    if args.cal:
        # Before XTR, so configureBoard's forced front-end-off cal isn't fighting
        # the enable we just set.
        setup.insert(-1, f"CAL {board}")
    for cmd in setup:
        print(f"       {cmd} -> {link.command(cmd)}")
    print(f"       {link.command('ADC?')}")

    out_path = args.out or os.path.join(
        bench_outdir(), f"vsweep_ch{g}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    rows = []

    hdr = (f"\n{'cmd mA':>8} {'DMM V':>12} {'sd uV':>8} "
           f"{'Koi raw V':>12} {'sd uV':>8} {'x6 V':>10} "
           f"{'resid mV':>9} {'R ohm':>9}")
    print(hdr)
    print("-" * len(hdr))

    try:
        for i_cmd in points:
            link.command(f"ISET {g} {i_cmd:.6f}")
            time.sleep(args.settle)

            dmm_vals = [dmm_sign * v for v in dmm.read_n(args.nreads)]
            dmm_v, dmm_sd = mean_sd(dmm_vals)

            koi_vals = []
            for _ in range(args.nreads):
                line = link.command(f"MEAS? {g}")
                if line is None:
                    continue
                try:
                    koi_vals.append(float(line))
                except ValueError:
                    pass
            if not koi_vals:
                print(f"{i_cmd:8.3f}  (no Koi reading — skipped)")
                continue
            koi_v, koi_sd = mean_sd(koi_vals)

            # Divider steals V_node/120k, so the load only sees the remainder.
            i_load_ma = (i_cmd - dmm_v / DIVIDER_TOTAL_OHMS * 1e3) if i_cmd else 0.0
            r_meas = (dmm_v / (i_load_ma * 1e-3)) if i_load_ma > 0 else float("nan")
            resid_mv = (koi_v - dmm_v / REF_RATIO) * 1e3

            print(f"{i_cmd:8.3f} {dmm_v:12.7f} {dmm_sd*1e6:8.2f} "
                  f"{koi_v:12.7f} {koi_sd*1e6:8.2f} {koi_v*6:10.6f} "
                  f"{resid_mv:9.3f} {r_meas:9.2f}")

            rows.append({
                "cmd_mA": i_cmd,
                "dmm_V": dmm_v, "dmm_sd_V": dmm_sd, "dmm_n": len(dmm_vals),
                "koi_raw_V": koi_v, "koi_sd_V": koi_sd, "koi_n": len(koi_vals),
                "i_load_mA": i_load_ma, "R_ohm": r_meas,
            })
    except KeyboardInterrupt:
        print("\ninterrupted — saving what we have")
    finally:
        link.command(f"ISET {g} 0")
        link.command("XTR 0")
        link.close()
        dmm.close()

    if not rows:
        print("\nno data collected.")
        return

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nsaved {len(rows)} points to {out_path}")

    # ── fit: koi_raw = dmm/ratio + offset ────────────────────────────────
    xs = [r["dmm_V"] for r in rows]
    ys = [r["koi_raw_V"] for r in rows]

    # A dead sense path (open divider leg, unpopulated load) still fits a line —
    # a garbage one. Catch it here rather than letting a nonsense ratio through.
    x_span, y_span = max(xs) - min(xs), max(ys) - min(ys)
    if x_span > 0.05 and y_span < x_span / 100:
        print(f"\n!! Koi barely moved: {y_span*1e3:.3f} mV span while the DMM "
              f"swept {x_span:.3f} V.")
        print("   Channel {} senses nothing. Check its 6:1 divider (100k top leg "
              "open pulls\n   the ADC pin to ground via the 20k) before trusting "
              "any fit below.".format(g))

    # Every reading pinned at -VREF is raw code 0 — a wedged ADC, not a signal.
    if all(abs(y + 3.0) < 1e-6 for y in ys):
        print("\n!! Every Koi reading is exactly -VREF (raw code 0x000000): the "
              "AD7193 is wedged,\n   not measuring. No fit is meaningful. "
              "Power-cycle or RESCAN before rerunning.")
        return

    m, b, r2 = linfit(xs, ys)
    if not m:
        print("\n!! Koi readings are constant — no slope to fit.")
        return
    print("\nfit  koi_raw = dmm_V / ratio + offset")
    print(f"  ratio  = {1/m:.4f}          (bench ref {REF_RATIO}, nominal 6.0)")
    print(f"  offset = {b*1e3:+.3f} mV     (bench ref {REF_OFFSET_V*1e3:+.3f} mV)")
    print(f"  r2     = {r2:.8f}   over {len(rows)} points")

    rs = [r["R_ohm"] for r in rows if r["R_ohm"] == r["R_ohm"]]
    if rs:
        rm, rsd = mean_sd(rs)
        print(f"\nR from DMM V / divider-corrected I: {rm:.2f} +/- {rsd:.2f} ohm"
              f"   (4-wire truth {args.rload:.2f}, "
              f"delta {rm - args.rload:+.2f} = {(rm/args.rload - 1)*100:+.3f} %)")


if __name__ == "__main__":
    main()
