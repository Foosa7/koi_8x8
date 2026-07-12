#!/usr/bin/env python3
"""
cal_sweep.py — characterise one channel's ADC voltage path against a known load.

Drives a single channel over a range of commanded currents (ISET), reads the raw
ADC-pin voltage back (MEAS? g), and compares it against the expected voltage for
a known load resistor. Fits raw_V vs I to separate a *gain* error (wrong
reference / divider — a pure slope error) from an *offset* error (bad zero-scale
cal / AINCOM not at ground — a nonzero intercept).

For each commanded current I (A) into a load R (Ω) behind a divider D:
    expected raw_V = I × R / D
A least-squares fit of measured raw_V vs I gives:
    slope     → effective R/D   (→ implied divider D = R / slope)
    intercept → ADC offset at the pin (volts)

Run with the GUI CLOSED (the serial port is exclusive).

Usage:
    python3 cal_sweep.py --channel 0 --rload 810
    python3 cal_sweep.py --channel 0 --rload 810 --imax 3 --step 0.25 --divider 6
"""

import argparse
import time

from koi_gui import KoiLink, autodetect_port, DEFAULT_BAUD, DEFAULT_DIVIDER


def linfit(xs, ys):
    """Ordinary least squares y = m*x + b; returns (m, b, r2)."""
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    m = (n * sxy - sx * sy) / denom
    b = (sy - m * sx) / n
    ybar = sy / n
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((y - (m * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return m, b, r2


def main():
    ap = argparse.ArgumentParser(description="ADC voltage-path calibration sweep")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--channel", type=int, required=True, help="global channel g (0..63)")
    ap.add_argument("--rload", type=float, required=True, help="known load resistance (ohms)")
    ap.add_argument("--divider", type=float, default=DEFAULT_DIVIDER,
                    help="nominal input divider (expected raw_V = I*R/divider)")
    ap.add_argument("--imax", type=float, default=3.0, help="max sweep current (mA)")
    ap.add_argument("--step", type=float, default=0.25, help="current step (mA)")
    ap.add_argument("--settle", type=float, default=0.4, help="settle time per point (s)")
    ap.add_argument("--xtr", action="store_true",
                    help="force an XTR 255 enable (racy; prefer a hardware reset)")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        raise SystemExit("No serial port found. Pass --port /dev/ttyACMx")
    print(f"Connecting to {port} @ {args.baud} …")
    link = KoiLink(port, args.baud)
    # Opening the port sometimes resets the Pico; if so, setup() runs a
    # multi-second DAC/ADC calibration and latches the front-ends ON at the end.
    # Wait for the "READY" banner (or settle quietly) before sending commands,
    # otherwise we race the boot sequence and read noise.
    print("waiting for board to be ready …")
    deadline = time.time() + 12.0
    while time.time() < deadline:
        ln = link.ser.readline().decode(errors="replace").strip()
        if ln:
            print("  ", ln)
        if ln == "READY":
            break
    time.sleep(0.5)
    link.ser.reset_input_buffer()
    print(link.command("*IDN?") or "(no IDN reply)")

    g = args.channel
    R, D = args.rload, args.divider
    # Enable all front-ends (XTR 255 → uniform all-low 0x00, the robust pattern)
    # and verify with an open "sentinel" channel on the same board: with the
    # front-ends on, an open channel driven at 1 mA rails to ~2 V; with them off
    # it reads ~0. Retry until the sentinel confirms the front-ends are live.
    board = g // 8
    sentinel = board * 8 + (1 if (g % 8) == 0 else 0)
    for attempt in range(5):
        link.command("XTR 255")
        link.command(f"ISET {sentinel} 1.0")
        time.sleep(0.5)
        allv = link.measure_all()          # MEASA? (scan path), like the GUI
        sv = allv[sentinel] if allv else 0.0
        if sv != sv:
            sv = 0.0
        if sv > 0.5:
            print(f"front-ends ON (sentinel g{sentinel} = {sv:.3f} V)")
            break
        print(f"  front-end enable attempt {attempt}: sentinel g{sentinel} = {sv:.3f} V")
    else:
        link.close()
        raise SystemExit("Could not enable front-ends (sentinel never railed). "
                         "Tap RESET on the Pico and retry.")
    link.command(f"ISET {sentinel} 0")
    currents = []
    i = 0.0
    while i <= args.imax + 1e-9:
        currents.append(round(i, 4))
        i += args.step

    print(f"\nchannel g={g}, load R={R} Ω, nominal divider D={D}")
    print(f"{'I_cmd(mA)':>9} {'raw(mV)':>9} {'exp(mV)':>9} {'err(%)':>8} "
          f"{'impl_R/D':>9} {'impl_D':>8}")
    xs, ys = [], []
    try:
        for ma in currents:
            link.command(f"ISET {g} {ma:.6f}")
            time.sleep(args.settle)
            # Use MEASA? (the scan path the GUI uses) and pick channel g — the
            # single-channel MEAS? g (singleConversion) path returns stale values.
            allv = link.measure_all()
            raw = allv[g] if allv else float("nan")
            if raw != raw:
                print(f"{ma:9.4f}  bad reply (nan) for g={g}")
                continue
            i_a = ma * 1e-3
            exp = i_a * R / D                     # expected raw_V (volts)
            err = (raw - exp) / exp * 100 if exp != 0 else float("nan")
            impl_rd = raw / i_a if i_a > 0 else float("nan")   # effective R/D
            impl_d = R / impl_rd if impl_rd == impl_rd and impl_rd != 0 else float("nan")
            print(f"{ma:9.4f} {raw*1e3:9.3f} {exp*1e3:9.3f} {err:8.2f} "
                  f"{impl_rd:9.2f} {impl_d:8.3f}")
            xs.append(i_a)
            ys.append(raw)
    finally:
        link.command(f"ISET {g} 0")              # leave the channel at 0 mA
        link.close()

    if len(xs) >= 2:
        m, b, r2 = linfit(xs, ys)                # raw_V = m*I + b  (I in amps)
        print("\n── fit: raw_V = slope·I + offset ─────────────────────────")
        print(f"  slope     = {m:.4f} V/A   (= effective R/D)")
        print(f"  offset    = {b*1e3:.4f} mV  (ADC zero-offset at the pin)")
        print(f"  r²        = {r2:.6f}")
        print(f"  implied divider D = R/slope = {R/m:.4f}  (nominal {D})")
        print(f"  gain error vs nominal       = {(m/(R/D)-1)*100:+.2f}%")
        print("\n  Interpretation:")
        print("   • large offset, small slope error → ADC zero-scale / AINCOM issue")
        print("   • clean slope error, ~0 offset    → reference or divider scale")
        print(f"   • to make readings match: set ADC_VREF or divider so D→{R/m:.4f}")


if __name__ == "__main__":
    main()
