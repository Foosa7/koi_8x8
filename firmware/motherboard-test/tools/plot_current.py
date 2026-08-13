#!/usr/bin/env python3
"""
plot_current.py — expected vs measured current, and the error, from the
Keithley 2100 alone (the on-board ADC takes no part).

Reads the randomized-order, drift-corrected sweep (inl_random_ch0.csv) written
by inl_random.py, and optionally the monotonic ramp (dac_fullrange_ch0.csv) for
comparison; both default to the archived 2026-07-29 run under bench/20260729/.
Measured current comes from the 2100's voltage across the four-wire load:
I = V/R + V/120k.

Three panels, because the obvious one is the least informative:
  1. measured vs expected -- as asked for, but a ~0.5 % error is 1/200th of the
     plot height, so this panel can only confirm there is no gross fault.
  2. error = measured - expected, in uA. This is where the offset (intercept)
     and the gain error (slope) are actually visible.
  3. residual after removing that straight line -- the nonlinearity, in LSB,
     with the monotonic ramp overlaid to show its thermal contamination.

Usage:
    python3 plot_current.py
    python3 plot_current.py --out ../paper/figures/current-accuracy.png
"""

import argparse
import csv
import os
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

R_LOAD = 1005.68
MAX_MA = 6.3830
MA_PER_CODE = 3.0 / (0.47 * 65535)          # 97.398 nA

# Categorical slots 1-3 of the validated reference palette (light mode). Only
# the first three clear the all-pairs CVD gate, which is the pairlist that
# applies to scatter marks -- so this figure never uses more than three.
C_BLUE, C_ORANGE, C_AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE = "#fcfcfb"
INK, INK_2, INK_3 = "#0b0b0b", "#52514e", "#8a8880"


def load_random(path):
    by = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            by.setdefault(float(r["cmd_mA"]), []).append(float(r["I_corr_mA"]))
    xs = sorted(by)
    return xs, [statistics.fmean(by[x]) for x in xs]


def load_ramp(path):
    xs, ys = [], []
    with open(path) as f:
        for r in csv.DictReader(f):
            x = float(r["cmd_mA"])
            if x > 0.05:
                xs.append(x); ys.append(float(r["I_source_mA"]))
    return xs, ys


def linfit(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    sxx = sum((a-mx)**2 for a in xs)
    sxy = sum((a-mx)*(b-my) for a, b in zip(xs, ys))
    m = sxy/sxx
    return m, my - m*mx


def style(ax):
    """Recessive axes: no top/right spine, muted grid behind the marks."""
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK_3); ax.spines[s].set_linewidth(0.8)
    ax.grid(True, color=INK_3, alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_2, labelsize=9, length=3, width=0.8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", default="bench/20260729/inl_random_ch0.csv")
    ap.add_argument("--ramp", default="bench/20260729/dac_fullrange_ch0.csv")
    ap.add_argument("--out", default="current-accuracy.png")
    args = ap.parse_args()

    xs, ys = load_random(args.random)
    m, b = linfit(xs, ys)
    err_ua = [(y - x) * 1e3 for x, y in zip(xs, ys)]            # measured - expected
    fit_ua = [((m*x + b) - x) * 1e3 for x in xs]
    inl = [(y - (m*x + b)) / MA_PER_CODE for x, y in zip(xs, ys)]

    have_ramp = os.path.exists(args.ramp)
    if have_ramp:
        rx, ry = load_ramp(args.ramp)
        rm, rb = linfit(rx, ry)
        rinl = [(y - (rm*x + rb)) / MA_PER_CODE for x, y in zip(rx, ry)]

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.1))
    fig.patch.set_facecolor(SURFACE)

    # -- 1. transfer --------------------------------------------------------
    ax = axes[0]; style(ax)
    ax.plot([0, MAX_MA], [0, MAX_MA], color=INK_3, lw=2, ls=(0, (5, 4)), zorder=1)
    ax.plot(xs, ys, "o", ms=4.5, color=C_BLUE, mew=0, zorder=3)
    ax.set_xlabel("Expected current (mA)", color=INK_2, fontsize=10)
    ax.set_ylabel("Measured current (mA)", color=INK_2, fontsize=10)
    ax.set_title("Transfer function", color=INK, fontsize=11, loc="left", pad=10)
    ax.text(0.97, 0.10, "ideal 1:1", transform=ax.transAxes, ha="right",
            color=INK_3, fontsize=9, style="italic")
    ax.text(0.03, 0.93, "error is ~0.5 % —\ninvisible at this scale",
            transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)

    # -- 2. error -----------------------------------------------------------
    ax = axes[1]; style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    ax.plot(xs, fit_ua, color=C_ORANGE, lw=2, zorder=2)
    ax.plot(xs, err_ua, "o", ms=4.5, color=C_BLUE, mew=0, zorder=3)
    ax.set_xlabel("Expected current (mA)", color=INK_2, fontsize=10)
    ax.set_ylabel("Measured − expected (µA)", color=INK_2, fontsize=10)
    ax.set_title("Error: offset + gain", color=INK, fontsize=11, loc="left", pad=10)
    ax.annotate(f"offset {b*1e3:+.2f} µA at I = 0", xy=(xs[0], err_ua[0]),
                xytext=(0.05, 0.58), textcoords="axes fraction",
                color=INK_2, fontsize=9,
                arrowprops=dict(arrowstyle="-", color=INK_3, lw=0.8,
                                shrinkA=2, shrinkB=4))
    # Text in ink, not series colour — the orange line beside it carries identity.
    ax.text(0.96, 0.62, f"fitted slope\n{(m-1)*100:+.3f} % gain error",
            transform=ax.transAxes, ha="right", va="top",
            color=INK_2, fontsize=9)

    # -- 3. residual --------------------------------------------------------
    ax = axes[2]; style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    if have_ramp:
        ax.plot(rx, rinl, "s", ms=3.4, color=C_ORANGE, mew=0, alpha=0.75, zorder=2,
                label="monotonic ramp (self-heated)")
    ax.plot(xs, inl, "o", ms=4.5, color=C_BLUE, mew=0, zorder=3,
            label="randomized + drift-corrected")
    ax.set_xlabel("Expected current (mA)", color=INK_2, fontsize=10)
    ax.set_ylabel("Residual (LSB)", color=INK_2, fontsize=10)
    ax.set_title("Nonlinearity, after removing offset + gain",
                 color=INK, fontsize=11, loc="left", pad=10)
    leg = ax.legend(loc="lower center", frameon=False, fontsize=8.5,
                    handletextpad=0.4, borderaxespad=0.4)
    for t in leg.get_texts():
        t.set_color(INK_2)

    fig.suptitle("Koi 8×8 channel 0 current accuracy — measured with a Keithley 2100 "
                 f"across a {R_LOAD:.2f} Ω four-wire load (ADC not used)",
                 color=INK, fontsize=12, x=0.008, ha="left", y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(args.out, dpi=200, facecolor=SURFACE)
    print(f"wrote {args.out}")

    # -- error summary ------------------------------------------------------
    import math
    dev = [abs(e) for e in err_ua]
    rms_raw = (sum(e*e for e in err_ua)/len(err_ua))**0.5
    rms_inl = (sum(i*i for i in inl)/len(inl))**0.5
    worst_inl = max(inl, key=abs)
    print(f"""
ERROR SUMMARY — channel 0, {len(xs)} points, 0.2–{max(xs):.3f} mA
  reference: Keithley 2100 across {R_LOAD} Ω (4-wire), randomized order,
             drift-corrected;  1 LSB = {MA_PER_CODE*1e6:.3f} nA

  UNCALIBRATED (as shipped, commanded vs actual)
    gain error        {(m-1)*100:+.3f} %
    offset (I_OS)     {b*1e3:+.3f} µA
    worst deviation   {max(dev):.2f} µA  ({max(dev)/MAX_MA/10:.3f} % FS)
    rms deviation     {rms_raw:.2f} µA

  AFTER a 2-parameter (gain + offset) calibration
    worst residual    {abs(worst_inl)*MA_PER_CODE*1e3:.3f} µA = {worst_inl:+.2f} LSB
    rms residual      {rms_inl*MA_PER_CODE*1e3:.3f} µA = {rms_inl:.2f} LSB
    as % of FS        {abs(worst_inl)*MA_PER_CODE/MAX_MA*100:.4f} %
    effective bits    {16 - math.log2(abs(worst_inl)):.1f} of 16
""")


if __name__ == "__main__":
    main()
