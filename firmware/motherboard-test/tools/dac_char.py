#!/usr/bin/env python3
"""
dac_char.py — current-source characterization using ONLY the Keithley 2100.

The on-board ADC is not used at all here. Current is derived from the 2100's
voltage across a known four-wire-verified load:

    I_load   = V / R
    I_source = V / R + V / 120k        (the sense divider's share)

Two measurements:

  A. FULL RANGE  — accuracy, gain/offset fit, and integral nonlinearity (INL)
     across 0..I_max.
  B. SINGLE CODE — steps the DAC one LSB at a time around chosen centres to
     measure the actual step size, differential nonlinearity (DNL), and
     monotonicity, and to establish how many levels are genuinely resolvable.

The firmware maps current to code as  code = round(mA * calSlope/VREF * 65535),
so an exact code c is commanded with mA = c * VREF/(calSlope*65535). That factor
is derived below rather than assumed, so it follows the firmware's constants.

Run with the GUI CLOSED.

!! THE INL FROM PART A IS NOT TRUSTWORTHY AS WRITTEN.
   Part A ramps monotonically 0 -> full scale, so the load's dissipation rises
   through the sweep (0 -> ~40 mW into 1 kOhm) and the load heats as it goes. A
   metal-film resistor at ~50 ppm/degC warming a few degrees drifts ~250 ppm --
   several times the ~0.006 % FS the INL is trying to resolve. Demonstrated on
   the bench: the mean single-code step at the 5 mA centre measures 94.84 nA when
   reached by ramping up from 3 mA, but 97.9 nA after a 60 s soak at 5 mA.
   To get a real INL the load's thermal state must be decoupled from the code
   sequence: randomize the code order, soak at each point, or use a low-tempco
   load at reduced dissipation.
   Part B (DNL, monotonicity) is fine -- adjacent-code differences taken seconds
   apart, so slow thermal drift largely cancels.

Settling and speed (both bench-measured, see docs/characterization.md):
  * --settle 0.8 is converged; 0.8 s and 1.5 s agree to 0.12 nA, while 0.3 s
    biases the mean step ~1.5 % low.
  * --nplc 1 with autozero off is the 2100's optimum: one reading already gives
    0.1 LSB, ~80x faster than the NPLC 10 default for the same precision.
    NPLC 10 is 10x slower for identical noise. Note the 2100 SILENTLY COERCES
    NPLC (0.02->1, 0.2->10, 100->10, no SCPI error).

Usage:
    python3 dac_char.py --channel 0 --rload 1005.68
    python3 dac_char.py --channel 0 --rload 1005.68 --centers 1.0,3.0,5.0 --ncodes 33
    # fast, same precision:
    python3 dac_char.py --channel 0 --rload 1005.68 --nplc 1 --nreads 4 --settle 0.8
"""

import argparse
import csv
import os
import statistics
import time

from koi_bench import bench_outdir
from koi_gui import KoiLink, autodetect_port, DEFAULT_BAUD, MAX_CURRENT_MA
from keithley2100 import Keithley2100

CAL_SLOPE = 0.47        # firmware CAL_SLOPE_DEFAULT, V per mA
VREF = 3.0
DAC_FULL = 65535
DIVIDER_TOTAL = 120000.0

# mA per DAC code: invert  v = calSlope*mA ; code = v/VREF*DAC_FULL
MA_PER_CODE = VREF / (CAL_SLOPE * DAC_FULL)


def code_to_ma(c):
    return c * MA_PER_CODE


def ma_to_code(ma):
    return int(round(ma / MA_PER_CODE))


def wait_ready(link, timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ln = link.ser.readline().decode(errors="replace").strip()
        if ln and ln.lstrip("# ").strip() == "READY":
            return True
    return False


def mean_sd(v):
    return statistics.fmean(v), (statistics.stdev(v) if len(v) > 1 else 0.0)


def linfit(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    sxx = sum((x-mx)**2 for x in xs); sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    m = sxy/sxx
    return m, my - m*mx


def main():
    ap = argparse.ArgumentParser(description="2100-only current-source characterization")
    ap.add_argument("--port"); ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--usbtmc")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--rload", type=float, required=True)
    ap.add_argument("--npoints", type=int, default=128, help="full-range points")
    ap.add_argument("--centers", default="1.0,3.0,5.0",
                    help="mA centres for the single-code sweeps")
    ap.add_argument("--ncodes", type=int, default=33, help="codes per centre (odd)")
    ap.add_argument("--nreads", type=int, default=10)
    ap.add_argument("--nplc", type=float, default=10)
    ap.add_argument("--settle", type=float, default=0.8)
    args = ap.parse_args()

    g = args.channel
    board = g // 8
    R = args.rload

    dmm = Keithley2100(args.usbtmc)
    dmm.reset()
    dmm.config_vdc(nplc=args.nplc, rng=10, high_z=True)
    print(f"DMM : {dmm.idn()}")

    link = KoiLink(args.port or autodetect_port(), args.baud)
    wait_ready(link); time.sleep(0.3); link.ser.reset_input_buffer()
    print(f"Koi : {link.command('*IDN?')}")
    link.command(f"XTR {1 << board}")

    print(f"\n1 DAC code = {MA_PER_CODE*1e6:.3f} nA = "
          f"{MA_PER_CODE*1e-3*R*1e6:.2f} uV across {R} ohm")
    print(f"full scale  = {MAX_CURRENT_MA:.4f} mA over {DAC_FULL+1} codes\n")

    def measure(ma):
        link.command(f"ISET {g} {ma:.9f}")
        time.sleep(args.settle)
        v, sd = mean_sd(dmm.read_n(args.nreads))
        return v, sd, v/R*1e3 + v/DIVIDER_TOTAL*1e3      # mA sourced

    # ---------------- A. full range ----------------
    print("=== A. FULL RANGE ===")
    rows = []
    step = MAX_CURRENT_MA / (args.npoints - 1)
    for i in range(args.npoints):
        ma = i * step
        v, sd, isrc = measure(ma)
        rows.append((ma, v, sd, isrc))
        if i % 16 == 0:
            print(f"  {ma:6.4f} mA -> {v:9.6f} V  ({isrc:6.4f} mA sourced)")

    use = [r for r in rows if r[0] > 0.05]
    m, b = linfit([r[0] for r in use], [r[3] for r in use])
    inl = [r[3] - (m*r[0] + b) for r in use]
    worst = max(inl, key=abs)
    print(f"\n  I_actual = {m:.6f} x I_cmd + {b*1e3:+.4f} uA")
    print(f"  gain error {(m-1)*100:+.3f} %   offset (I_OS) {b*1e3:+.3f} uA")
    print(f"  INL: max {worst*1e3:+.3f} uA = {abs(worst)/MAX_CURRENT_MA*100:.4f} % FS "
          f"= {abs(worst)/MA_PER_CODE:.2f} LSB")
    nsd = statistics.fmean([r[2] for r in use])
    print(f"  2100 noise: {nsd*1e6:.2f} uV -> {nsd/R*1e9:.2f} nA "
          f"({nsd/R/MA_PER_CODE*1e3:.3f} LSB)")

    outdir = bench_outdir()
    with open(os.path.join(outdir, "dac_fullrange_ch%d.csv" % g), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["cmd_mA", "dmm_V", "dmm_sd_V", "I_source_mA"])
        w.writerows(rows)

    # ---------------- B. single code ----------------
    print("\n=== B. SINGLE-CODE STEPS ===")
    allsteps = []
    for centre in [float(x) for x in args.centers.split(",")]:
        c0 = ma_to_code(centre)
        half = args.ncodes // 2
        codes = list(range(c0 - half, c0 + half + 1))
        pts = []
        for c in codes:
            v, sd, isrc = measure(code_to_ma(c))
            pts.append((c, v, sd, isrc))
        steps = [(pts[i][3] - pts[i-1][3]) for i in range(1, len(pts))]
        sm, ssd = mean_sd(steps)
        nonmono = sum(1 for s in steps if s <= 0)
        dnl = [(s - MA_PER_CODE)/MA_PER_CODE for s in steps]
        print(f"\n  centre {centre} mA (code {c0}), {len(codes)} codes:")
        print(f"    mean step {sm*1e6:7.2f} nA   (ideal {MA_PER_CODE*1e6:.2f} nA, "
              f"{(sm/MA_PER_CODE-1)*100:+.2f} %)")
        print(f"    step 1sigma {ssd*1e6:6.2f} nA = {ssd/MA_PER_CODE:.3f} LSB")
        print(f"    DNL max {max(dnl, key=abs):+.3f} LSB    non-monotonic steps: {nonmono}")
        allsteps += steps
        with open(os.path.join(outdir, f"dac_codes_ch{g}_{centre:g}mA.csv"),
                  "w", newline="") as f:
            w = csv.writer(f); w.writerow(["code", "dmm_V", "dmm_sd_V", "I_source_mA"])
            w.writerows(pts)

    sm, ssd = mean_sd(allsteps)
    print(f"\n  ALL centres: mean step {sm*1e6:.2f} nA, 1sigma {ssd*1e6:.2f} nA, "
          f"n={len(allsteps)} steps")
    print(f"  resolvable levels over {MAX_CURRENT_MA:.4f} mA at 1 LSB: {DAC_FULL+1}")
    print(f"  measurement-limited levels (2100 1sigma): "
          f"{int(MAX_CURRENT_MA/(nsd/R*1e3)):d}")

    link.command(f"ISET {g} 0"); link.command("XTR 0")
    link.close(); dmm.close()


if __name__ == "__main__":
    main()
