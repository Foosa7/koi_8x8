#!/usr/bin/env python3
"""
inl_random.py — current-source INL with the load's thermal state decoupled
from the code sequence. 2100 only; the on-board ADC is not used.

Why this exists: a monotonic 0 -> full-scale ramp measures the load heating up
as much as it measures the converter (dissipation rises 0 -> ~40 mW into 1k, and
a 50 ppm/degC load warming a few degrees drifts ~250 ppm -- several times the
~0.006 % FS an INL measurement is trying to resolve). See docs/characterization.md.

Two defences here:

  1. RANDOMIZED CODE ORDER, so any slow drift is uncorrelated with code and
     shows up as scatter rather than as a smooth INL curve.
  2. AN INTERLEAVED REFERENCE POINT re-measured every `--ref-every` points. Its
     reading over time IS the drift, measured directly; each data point is
     corrected by the reference interpolated to its timestamp.

Residual limitation, stated plainly: the load's resistance depends on its own
dissipation, which is a deterministic function of the commanded code. That term
is NOT removed by randomization or by reference correction -- it is
indistinguishable from converter INL when the only instrument is a voltmeter
across the self-heating load. To bound it, `--max-ma` restricts the sweep to a
low-dissipation span; comparing the INL from a restricted span against the full
span shows how much of the "INL" is really load tempco.

Usage:
    python3 inl_random.py --channel 0 --rload 1005.68
    python3 inl_random.py --channel 0 --rload 1005.68 --max-ma 1.6 --passes 2
"""

import argparse
import csv
import os
import random
import statistics
import time

from koi_bench import bench_outdir
from koi_gui import KoiLink, autodetect_port, DEFAULT_BAUD, MAX_CURRENT_MA
from keithley2100 import Keithley2100

CAL_SLOPE = 0.47
VREF = 3.0
DAC_FULL = 65535
DIVIDER_TOTAL = 120000.0
MA_PER_CODE = VREF / (CAL_SLOPE * DAC_FULL)


def wait_ready(link, timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ln = link.ser.readline().decode(errors="replace").strip()
        if ln and ln.lstrip("# ").strip() == "READY":
            return True
    return False


def linfit(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    sxx = sum((x-mx)**2 for x in xs); sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    m = sxy/sxx
    return m, my - m*mx


def interp(ts, vs, t):
    """Linear interpolation of the reference track at time t."""
    if t <= ts[0]:
        return vs[0]
    if t >= ts[-1]:
        return vs[-1]
    for i in range(1, len(ts)):
        if t <= ts[i]:
            f = (t - ts[i-1]) / (ts[i] - ts[i-1])
            return vs[i-1] + f * (vs[i] - vs[i-1])
    return vs[-1]


def main():
    ap = argparse.ArgumentParser(description="Thermally-decoupled INL")
    ap.add_argument("--port"); ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--usbtmc")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--rload", type=float, required=True)
    ap.add_argument("--npoints", type=int, default=48)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--min-ma", type=float, default=0.2)
    ap.add_argument("--max-ma", type=float, default=MAX_CURRENT_MA)
    ap.add_argument("--ref-every", type=int, default=4)
    ap.add_argument("--nreads", type=int, default=3)
    ap.add_argument("--nplc", type=float, default=1)
    ap.add_argument("--settle", type=float, default=0.6)
    ap.add_argument("--soak", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    g = args.channel; board = g // 8; R = args.rload
    random.seed(args.seed)

    dmm = Keithley2100(args.usbtmc)
    dmm.reset()
    dmm.config_vdc(nplc=args.nplc, rng=10, autozero=False, high_z=True)
    stale = dmm.errors()
    print(f"DMM : {dmm.idn()}")
    print(f"      SCPI queue after config: {stale or 'clean'}")

    link = KoiLink(args.port or autodetect_port(), args.baud)
    wait_ready(link); time.sleep(0.3); link.ser.reset_input_buffer()
    print(link.command("*IDN?"))
    link.command(f"XTR {1 << board}")

    lo, hi = int(args.min_ma/MA_PER_CODE), int(args.max_ma/MA_PER_CODE)
    codes = [lo + round(i*(hi-lo)/(args.npoints-1)) for i in range(args.npoints)]
    ref_code = (lo + hi)//2
    ref_ma = ref_code * MA_PER_CODE

    print(f"\nspan {args.min_ma:.3f}..{args.max_ma:.3f} mA, {len(codes)} codes, "
          f"{args.passes} pass(es), reference at {ref_ma:.3f} mA every "
          f"{args.ref_every} points")
    print(f"soaking at the reference for {args.soak:.0f} s ...")
    link.command(f"ISET {g} {ref_ma:.9f}")
    time.sleep(args.soak)

    t_start = time.time()

    # Any SCPI error during acquisition invalidates the points around it, so
    # they are collected and reported rather than left in the queue to be
    # discovered (or silently inherited) by a later session.
    scpi_errors = []

    def meas(ma):
        link.command(f"ISET {g} {ma:.9f}")
        time.sleep(args.settle)
        raw = dmm.read_burst(args.nreads, nplc=args.nplc)
        if len(raw) < args.nreads:
            scpi_errors.append(f"short burst at {ma:.4f} mA: "
                               f"{len(raw)}/{args.nreads} readings")
        over = [x for x in raw if abs(x) > 1e30]
        if over:
            scpi_errors.append(f"OVERLOAD at {ma:.4f} mA")
        e = dmm.errors()
        if e:
            scpi_errors.append(f"at {ma:.4f} mA: {e}")
        v = statistics.fmean(raw)
        return v/R*1e3 + v/DIVIDER_TOTAL*1e3, time.time() - t_start

    rows = []
    for p in range(args.passes):
        order = codes[:]; random.shuffle(order)
        rts, rvs = [], []
        i0, t0 = meas(ref_ma); rts.append(t0); rvs.append(i0)
        for i, c in enumerate(order):
            if i and i % args.ref_every == 0:
                iv, tv = meas(ref_ma); rts.append(tv); rvs.append(iv)
            iv, tv = meas(c * MA_PER_CODE)
            rows.append((p, c, c*MA_PER_CODE, iv, tv))
        iv, tv = meas(ref_ma); rts.append(tv); rvs.append(iv)
        drift = (max(rvs)-min(rvs))/statistics.fmean(rvs)*1e6
        print(f"  pass {p}: reference drifted {drift:.1f} ppm over "
              f"{rts[-1]-rts[0]:.0f} s  ({len(rvs)} ref points)")
        # correct this pass's points by the interpolated reference
        rmean = statistics.fmean(rvs)
        for k in range(len(rows)-len(order), len(rows)):
            pp, c, ma, iv, tv = rows[k]
            rows[k] = (pp, c, ma, iv * rmean / interp(rts, rvs, tv), tv)

    link.command(f"ISET {g} 0"); link.command("XTR 0")
    link.close(); dmm.close()

    if scpi_errors:
        print(f"\n  !! {len(scpi_errors)} DMM problem(s) during acquisition — "
              f"the fit below may be corrupted:")
        for e in scpi_errors[:10]:
            print(f"       {e}")
    else:
        print("\n  DMM error queue stayed clean for the whole acquisition.")

    out_path = os.path.join(bench_outdir(), f"inl_random_ch{g}.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["pass", "code", "cmd_mA", "I_corr_mA", "t_s"])
        w.writerows(rows)

    print()
    for p in range(args.passes):
        sub = [r for r in rows if r[0] == p]
        m, b = linfit([r[2] for r in sub], [r[3] for r in sub])
        inl = [(r[3]-(m*r[2]+b))/MA_PER_CODE for r in sub]
        print(f"  pass {p}: gain {(m-1)*100:+.3f} %  INL max {max(inl,key=abs):+.2f} LSB  "
              f"rms {(sum(i*i for i in inl)/len(inl))**0.5:.2f} LSB")

    # combined: average the passes per code, then fit
    bycode = {}
    for _, c, ma, iv, _ in rows:
        bycode.setdefault((c, ma), []).append(iv)
    xs = [ma for (c, ma) in sorted(bycode, key=lambda k: k[0])]
    ys = [statistics.fmean(bycode[k]) for k in sorted(bycode, key=lambda k: k[0])]
    m, b = linfit(xs, ys)
    inl = [(y-(m*x+b))/MA_PER_CODE for x, y in zip(xs, ys)]
    worst = max(inl, key=abs)
    print(f"\n  COMBINED ({args.passes} passes averaged):")
    print(f"    gain {(m-1)*100:+.3f} %   offset {b*1e3:+.3f} uA")
    print(f"    INL max {worst:+.2f} LSB = {abs(worst)*MA_PER_CODE/MAX_CURRENT_MA*100:.4f} % FS"
          f"   rms {(sum(i*i for i in inl)/len(inl))**0.5:.2f} LSB")
    if args.passes > 1:
        rep = [statistics.stdev(v)/MA_PER_CODE for v in bycode.values() if len(v) > 1]
        print(f"    pass-to-pass repeatability: {statistics.fmean(rep):.2f} LSB "
              f"(1sigma per code) -> this is the floor on any INL claim")


if __name__ == "__main__":
    main()
