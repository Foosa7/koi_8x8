#!/usr/bin/env python3
"""
plot_paper_figs.py — Section 7 (validation) figures for the HardwareX manuscript.

Reads the CSVs a `run_campaign.py` / GUI Bench-row session writes into
`bench/<date>/`, picking the newest file of each measurement type. Every figure
is real measured data from one channel; figure slots with no data yet
(crosstalk, thermal, board-to-board) stay as placeholders in the manuscript
rather than being invented here.

    figures/i-transfer.png    <- set_vs_dmm + codes
    figures/i-deviation.png   <- set_vs_dmm + drive_sweep
    figures/i-linearity.png   <- codes + inl (randomized AND monotonic)
    figures/i-precision.png   <- drive_sweep + noise
    figures/v-sense.png       <- low_current

R is applied at ANALYSIS time, from the CSV header (or --rload to override), so
a corrected load resistance never means re-acquiring anything.

Usage:
    python3 plot_paper_figs.py --benchdir bench/20260730
    python3 plot_paper_figs.py --benchdir bench/20260730 --rload 995.1
"""

import argparse
import glob
import math
import os
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import koi_bench as kb

R_DIVIDER = kb.R_DIVIDER
CODE_MA = kb.CODE_MA
MAX_MA = kb.MAX_MA

# Categorical slots 1-3 of the validated reference palette (light mode); only
# the first three clear the all-pairs CVD gate that applies to scatter marks.
C_BLUE, C_ORANGE, C_AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE = "#fcfcfb"
INK, INK_2, INK_3 = "#0b0b0b", "#52514e", "#8a8880"


# --- loading ----------------------------------------------------------------
def newest(benchdir, prefix, want_order=None, min_rows=3):
    """Newest CSV of a measurement type. `want_order` filters INL runs by the
    `order:` recorded in the header (random vs monotonic).

    Files with fewer than `min_rows` data rows are skipped in favour of an older
    one: a run still in progress has its rows sitting in the file object's
    buffer, and an aborted run can leave a header with nothing under it. Either
    would otherwise shadow the last good dataset.
    """
    hits = sorted(glob.glob(os.path.join(benchdir, f"{prefix}_ch*.csv")))
    for path in reversed(hits):
        meta, rows = kb.load_bench_csv(path)
        if want_order and want_order not in meta.get("order", ""):
            continue
        if len(rows) < min_rows:
            continue
        return meta, rows, path
    return None, None, None


def r_from(meta, override):
    if override:
        return override
    try:
        return float(meta["r_load_ohm"].split()[0])
    except (KeyError, ValueError, IndexError):
        return 996.4


def i_source(v, r):
    """mA out of the source: the load's share plus the sense divider's."""
    return (v / r + v / R_DIVIDER) * 1e3


def col(rows, name, cast=float):
    out = []
    for x in rows:
        try:
            out.append(cast(x[name]))
        except (KeyError, ValueError):
            out.append(float("nan"))
    return out


def linfit(xs, ys):
    pts = [(a, b) for a, b in zip(xs, ys) if a == a and b == b]
    n = len(pts)
    mx = sum(a for a, _ in pts) / n
    my = sum(b for _, b in pts) / n
    sxx = sum((a - mx) ** 2 for a, _ in pts)
    m = sum((a - mx) * (b - my) for a, b in pts) / sxx
    return m, my - m * mx


# --- styling ----------------------------------------------------------------
def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK_3)
        ax.spines[s].set_linewidth(0.8)
    ax.grid(True, color=INK_3, alpha=0.25, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_2, labelsize=9, length=3, width=0.8)


def titles(ax, title, xlabel, ylabel):
    ax.set_title(title, color=INK, fontsize=10.5, loc="left", pad=8)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=9.5)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9.5)


def legend(ax, **kw):
    leg = ax.legend(frameon=False, fontsize=8.5, handletextpad=0.4,
                    borderaxespad=0.4, **kw)
    for t in leg.get_texts():
        t.set_color(INK_2)


def figsave(fig, outdir, name, suptitle):
    fig.suptitle(suptitle, color=INK, fontsize=11, x=0.008, ha="left", y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.925))
    path = os.path.join(outdir, name)
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    print(f"  wrote {path}")


# ===========================================================================
def fig_transfer(bd, outdir, rload):
    meta, rows, _ = newest(bd, "set_vs_dmm")
    cmeta, crows, _ = newest(bd, "codes")
    if not rows:
        print("  skip i-transfer (no set_vs_dmm)")
        return
    r = r_from(meta, rload)
    cmd = col(rows, "cmd_mA")
    meas = [i_source(v, r) for v in col(rows, "dmm_V")]
    order = sorted(zip(cmd, meas))
    cmd, meas = [a for a, _ in order], [b for _, b in order]

    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.0))
    fig.patch.set_facecolor(SURFACE)

    ax = axes[0]
    style(ax)
    ax.plot([0, MAX_MA], [0, MAX_MA], color=INK_3, lw=2, ls=(0, (5, 4)),
            label="ideal 1:1")
    ax.plot(cmd, meas, "o", ms=3.8, color=C_BLUE, mew=0, zorder=3,
            label="measured (Keithley 2100)")
    m, b = linfit(cmd, meas)
    titles(ax, "Transfer function, full range",
           "Commanded current (mA)", "Measured current (mA)")
    ax.text(0.03, 0.94, f"{len(cmd)} points, randomized order\n"
                        f"{(m-1)*100:+.3f} % gain error is 1/200 of\n"
                        "the plot height — see deviation",
            transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)
    legend(ax, loc="lower right")

    ax = axes[1]
    style(ax)
    if crows:
        cr = r_from(cmeta, rload)
        centres = sorted({float(x["centre_mA"]) for x in crows})
        pick = centres[len(centres) // 2]
        sel = [x for x in crows if float(x["centre_mA"]) == pick]
        codes = [int(x["code"]) for x in sel]
        cur = [i_source(float(x["dmm_V"]), cr) * 1e3 for x in sel]
        c0 = codes[0]
        ax.plot([c - c0 for c in codes], cur, "o-", ms=4.2, lw=1.2,
                color=C_BLUE, mew=0)
        mm, bb = linfit([float(c) for c in codes], cur)
        ax.plot([c - c0 for c in codes], [mm * c + bb for c in codes],
                color=C_ORANGE, lw=1.6, zorder=1,
                label=f"fit: {mm*1e3:.2f} nA/code")
        titles(ax, f"Single-code steps at {pick:g} mA",
               f"DAC code offset from {c0}", "Measured current (µA)")
        ax.text(0.04, 0.94, f"1 LSB = {CODE_MA*1e6:.3f} nA\n"
                            "every code individually resolved",
                transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)
        legend(ax, loc="lower right")
    figsave(fig, outdir, "i-transfer.png",
            f"Current transfer function — channel 0, Keithley 2100 across a "
            f"{r:.2f} Ω load")


def fig_deviation(bd, outdir, rload):
    meta, rows, _ = newest(bd, "set_vs_dmm")
    dmeta, drows, _ = newest(bd, "drive_sweep")
    if not rows:
        print("  skip i-deviation (no set_vs_dmm)")
        return
    r = r_from(meta, rload)
    cmd = col(rows, "cmd_mA")
    meas = [i_source(v, r) for v in col(rows, "dmm_V")]
    order = sorted(zip(cmd, meas))
    cmd, meas = [a for a, _ in order], [b for _, b in order]
    m, b = linfit(cmd, meas)
    err = [(y - x) * 1e3 for x, y in zip(cmd, meas)]
    fit = [((m * x + b) - x) * 1e3 for x in cmd]

    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.0))
    fig.patch.set_facecolor(SURFACE)

    ax = axes[0]
    style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    ax.plot(cmd, fit, color=C_ORANGE, lw=2, zorder=2,
            label=f"fit: {(m-1)*100:+.3f} % gain, {b*1e3:+.2f} µA offset")
    ax.plot(cmd, err, "o", ms=4.2, color=C_BLUE, mew=0, zorder=3,
            label="measured")
    titles(ax, "Source accuracy (external DMM)",
           "Commanded current (mA)", "Measured − commanded (µA)")
    legend(ax, loc="upper left")

    ax = axes[1]
    style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    if drows:
        dr = r_from(dmeta, rload)
        dc = col(drows, "cmd_mA")
        kv = col(drows, "koi_raw_V")
        dv = col(drows, "dmm_V")
        # Calibrate the sense path on this very sweep, above the knee, then show
        # the readback error with and without that 2-parameter correction.
        hi = [(a, b) for a, b in zip(dv, kv) if a > 0.05 and b == b]
        mm, bb = linfit([a for a, _ in hi], [b for _, b in hi])
        ratio, off = 1 / mm, bb
        raw = [(i_source(k * 6.0, dr) - c) * 1e3 for c, k in zip(dc, kv)]
        cal = [(i_source((k - off) * ratio, dr) - c) * 1e3
               for c, k in zip(dc, kv)]
        ax.plot(dc, raw, "s", ms=3.6, color=C_ORANGE, mew=0, alpha=0.8,
                zorder=2, label="nominal ×6, uncalibrated")
        ax.plot(dc, cal, "o", ms=4.2, color=C_BLUE, mew=0, zorder=3,
                label=f"calibrated (×{ratio:.4f}, {off*1e3:+.3f} mV)")
        legend(ax, loc="lower right")
    titles(ax, "Closed-loop readback accuracy (on-board ADC)",
           "Commanded current (mA)", "ADC-derived − commanded (µA)")
    figsave(fig, outdir, "i-deviation.png",
            "Current deviation from nominal — source accuracy vs. on-board "
            "readback accuracy, channel 0")


def _inl(rows, r):
    by = {}
    for x in rows:
        try:
            ma = float(x["cmd_mA"])
            by.setdefault(round(ma, 9), []).append(float(x["I_corr_mA"]))
        except (KeyError, ValueError):
            pass
    xs = sorted(by)
    ys = [statistics.fmean(by[k]) for k in xs]
    m, b = linfit(xs, ys)
    inl = [(y - (m * x + b)) / CODE_MA for x, y in zip(xs, ys)]
    rms = math.sqrt(sum(v * v for v in inl) / len(inl))
    return xs, inl, rms, max(inl, key=abs)


def fig_linearity(bd, outdir, rload):
    cmeta, crows, _ = newest(bd, "codes")
    rmeta, rrows, _ = newest(bd, "inl", want_order="random")
    mmeta, mrows, _ = newest(bd, "inl", want_order="monotonic")
    if not crows or not rrows:
        print("  skip i-linearity (need codes + inl)")
        return
    cr = r_from(cmeta, rload)

    steps, dnl_by_centre = [], {}
    for centre in sorted({float(x["centre_mA"]) for x in crows}):
        sel = [x for x in crows if float(x["centre_mA"]) == centre]
        cur = [i_source(float(x["dmm_V"]), cr) for x in sel]
        codes = [int(x["code"]) for x in sel]
        st = [cur[i + 1] - cur[i] for i in range(len(cur) - 1)]
        steps += st
        dnl_by_centre[f"{centre:g} mA"] = (
            [c - codes[0] for c in codes[:-1]], [s / CODE_MA - 1 for s in st])

    xs, inl, rms, worst = _inl(rrows, r_from(rmeta, rload))

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.0))
    fig.patch.set_facecolor(SURFACE)

    ax = axes[0]
    style(ax)
    ax.hist([s * 1e6 for s in steps], bins=14, color=C_BLUE,
            edgecolor=SURFACE, linewidth=0.8)
    ax.axvline(CODE_MA * 1e6, color=C_ORANGE, lw=2,
               label=f"ideal LSB {CODE_MA*1e6:.1f} nA")
    ax.set_ylim(0, ax.get_ylim()[1] * 1.45)
    titles(ax, "Single-code step size", "Step (nA)", "Count")
    ax.text(0.97, 0.74, f"{len(steps)} steps\nmean "
                        f"{statistics.fmean(steps)*1e6:.2f} nA, sd "
                        f"{statistics.stdev(steps)*1e6:.2f} nA",
            transform=ax.transAxes, va="top", ha="right",
            color=INK_2, fontsize=8.5)
    legend(ax, loc="upper right")

    ax = axes[1]
    style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    for (lab, (cx, cy)), c in zip(dnl_by_centre.items(),
                                  (C_BLUE, C_ORANGE, C_AQUA)):
        ax.plot(cx, cy, "o-", ms=3.4, lw=1.0, color=c, mew=0, alpha=0.9,
                label=lab)
    alldnl = [v for _, cy in dnl_by_centre.values() for v in cy]
    ax.axhline(-1, color=INK_3, lw=1.0, ls=(0, (4, 3)))
    ax.set_ylim(-1.15, 0.62)
    titles(ax, "Differential nonlinearity", "DAC code offset from centre",
           "DNL (LSB)")
    ax.text(0.04, 0.96, f"max |DNL| = {max(abs(v) for v in alldnl):.2f} LSB\n"
                        f"{sum(1 for d in alldnl if d <= -1)} non-monotonic steps",
            transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)
    ax.text(0.5, 0.055, "missing-code limit, DNL = −1", transform=ax.transAxes,
            ha="center", color=INK_2, fontsize=8)
    legend(ax, loc="upper right", ncol=3)

    ax = axes[2]
    style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    note = ""
    if mrows:
        mx, minl, mrms, _ = _inl(mrows, r_from(mmeta, rload))
        ax.plot(mx, minl, "s", ms=3.2, color=C_ORANGE, mew=0, alpha=0.75,
                zorder=2, label=f"monotonic order (rms {mrms:.2f})")
        note = ("\nmonotonic and randomized agree —\n"
                "the blower removed the self-heating")
    ax.plot(xs, inl, "o", ms=4.2, color=C_BLUE, mew=0, zorder=3,
            label=f"randomized order (rms {rms:.2f})")
    ally = list(inl) + (list(minl) if mrows else [])
    lo_y, hi_y = min(ally), max(ally)
    ax.set_ylim(lo_y - 0.45 * (hi_y - lo_y), hi_y + 0.42 * (hi_y - lo_y))
    titles(ax, "Integral nonlinearity", "Commanded current (mA)", "INL (LSB)")
    ax.text(0.03, 0.05, f"rms {rms:.2f} LSB, max {worst:+.2f} LSB "
                        f"({abs(worst)*CODE_MA/MAX_MA*100:.4f} % FS){note}",
            transform=ax.transAxes, color=INK_2, fontsize=8.5)
    legend(ax, loc="upper center")

    figsave(fig, outdir, "i-linearity.png",
            "Code-level linearity — measured against the Keithley 2100 alone, "
            "on-board ADC unused")


def fig_precision(bd, outdir, rload):
    dmeta, drows, _ = newest(bd, "drive_sweep")
    nmeta, nrows, _ = newest(bd, "noise")
    if not drows:
        print("  skip i-precision (no drive_sweep)")
        return
    r = r_from(dmeta, rload)
    cmd, ksd, dsd = [], [], []
    for x in drows:
        c = float(x["cmd_mA"])
        # Below ~0.1 mA the node sits at the sense path's knee, where relative
        # scatter explodes; including it costs two decades of the log axis and
        # says nothing about precision in the operating range.
        if c < 0.1:
            continue
        cmd.append(c)
        ksd.append(float(x["koi_sd_V"]) * 6.0 * 1e6)
        dsd.append(float(x["dmm_sd_V"]) * 1e6)

    ncols = 2 if nrows else 1
    fig, axes = plt.subplots(1, ncols, figsize=(10.6 if nrows else 7.0, 4.0))
    axes = axes if ncols > 1 else [axes]
    fig.patch.set_facecolor(SURFACE)

    ax = axes[0]
    style(ax)
    ax.plot(cmd, ksd, "o", ms=4.2, color=C_BLUE, mew=0, zorder=3,
            label="Koi on-board ADC (load-referred)")
    ax.plot(cmd, dsd, "s", ms=3.6, color=C_ORANGE, mew=0, alpha=0.85, zorder=2,
            label="Keithley 2100 (same reads)")
    ax.set_yscale("log")
    titles(ax, "Repeatability at each setpoint", "Commanded current (mA)",
           "1σ (µV at the load)")
    ax.text(0.03, 0.95, f"median: Koi {statistics.median(ksd):.1f} µV "
                        f"({statistics.median(ksd)/r*1e3:.1f} nA), "
                        f"2100 {statistics.median(dsd):.1f} µV",
            transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)
    legend(ax, loc="lower right")

    if nrows:
        ax = axes[1]
        style(ax)
        labels, sd_load, tper = [], [], []
        for x in nrows:
            labels.append(f"{x['rate']}/{x['avg']}")
            sd_load.append(float(x["koi_sd_load_uV"]))
            tper.append(float(x["t_per_read_s"]) * 1e3)
        pos = range(len(labels))
        ax.bar(pos, sd_load, color=C_BLUE, width=0.62)
        ax.set_xticks(list(pos))
        ax.set_xticklabels(labels, fontsize=8.5, color=INK_2)
        titles(ax, "Noise vs. sampling settings", "RATE / AVG",
               "1σ at the load (µV)")
        for p, s, t in zip(pos, sd_load, tper):
            ax.text(p, s, f"{t:.0f} ms", ha="center", va="bottom",
                    color=INK_2, fontsize=8)
        # State what these points show, not what an earlier dataset showed: the
        # noise falls monotonically with averaging across the whole grid here,
        # so no floor is reached and "further averaging buys nothing" would be
        # an unsupported claim.
        best = min(range(len(sd_load)), key=lambda i: sd_load[i])
        ratios = [sd_load[i] / sd_load[i + 1] for i in range(len(sd_load) - 1)
                  if labels[i].startswith("96") and labels[i + 1].startswith("96")]
        trend = (f"≈√n averaging (×{statistics.fmean(ratios):.2f} per ×4)"
                 if ratios else "monotonic in averaging")
        ax.set_ylim(0, max(sd_load) * 1.32)
        ax.text(0.97, 0.95, f"lowest at {labels[best]} — no floor reached\n{trend}",
                transform=ax.transAxes, va="top", ha="right",
                color=INK_2, fontsize=8.5)

    figsave(fig, outdir, "i-precision.png",
            "Measurement precision — repeat scatter and the sampling-setting "
            "noise floor, channel 0")


def fig_vsense(bd, outdir, rload):
    meta, rows, _ = newest(bd, "low_current")
    if not rows:
        print("  skip v-sense (no low_current)")
        return
    pts = [(float(x["dmm_V"]), float(x["koi_raw_V"])) for x in rows
           if float(x["cmd_mA"]) > 0]
    pts.sort()
    KNEE = 0.044
    lo = [p for p in pts if p[0] < KNEE]
    hi = [p for p in pts if p[0] >= KNEE]
    if len(lo) < 2 or len(hi) < 2:
        print("  skip v-sense (sweep does not span the knee)")
        return
    m_lo, b_lo = linfit([a for a, _ in lo], [b for _, b in lo])
    m_hi, b_hi = linfit([a for a, _ in hi], [b for _, b in hi])
    ratio, off = 1 / m_hi, b_hi

    inc_x, inc_r = [], []
    for i in range(len(pts) - 1):
        dv, dk = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        if dk > 1e-9:
            inc_x.append(pts[i + 1][0])
            inc_r.append(dv / dk)
    implied = [(a * 1e3, (b - a * m_hi) * 1e3) for a, b in pts]

    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.0))
    fig.patch.set_facecolor(SURFACE)

    ax = axes[0]
    style(ax)
    ax.plot(inc_x, inc_r, "o-", ms=4.0, lw=1.0, color=C_BLUE, mew=0)
    ax.axhline(ratio, color=C_ORANGE, lw=2,
               label=f"above-knee ratio {ratio:.3f}")
    ax.axvline(KNEE, color=INK_3, lw=1.2, ls=(0, (4, 3)))
    ax.set_xscale("log")
    ax.set_ylim(0, max(inc_r) * 1.30)
    titles(ax, "Incremental divider ratio", "Node voltage (V, log)",
           "ΔV$_{node}$ / ΔV$_{ADC}$")
    ax.text(KNEE * 1.3, max(inc_r) * 1.14, f"knee at {KNEE*1e3:.0f} mV",
            color=INK_2, fontsize=8.5)
    ax.text(0.04, 0.62, f"below the knee the path has\n"
                        f"ratio ≈{1/m_lo:.1f}, roughly half the gain",
            transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)
    legend(ax, loc="center right")

    ax = axes[1]
    style(ax)
    ax.axhline(0, color=INK_3, lw=1.0)
    ax.plot([p[0] for p in implied], [p[1] for p in implied], "o", ms=4.2,
            color=C_BLUE, mew=0, zorder=3)
    ax.axhline(off * 1e3, color=C_ORANGE, lw=2,
               label=f"saturates at {off*1e3:.2f} mV")
    ax.axvline(KNEE * 1e3, color=INK_3, lw=1.2, ls=(0, (4, 3)))
    ax.set_xscale("log")
    titles(ax, "Implied additive offset", "Node voltage (mV, log)",
           "V$_{ADC}$ − V$_{node}$/ratio  (mV)")
    ax.text(0.04, 0.30, "a true fixed offset would be flat\n"
                        "everywhere; this one builds up\n"
                        "through the low-signal region",
            transform=ax.transAxes, va="top", color=INK_2, fontsize=8.5)
    legend(ax, loc="center right")

    figsave(fig, outdir, "v-sense.png",
            "Voltage-sense linearity — the low-signal gain deficit that "
            "presents as a fixed offset above the knee")
    print(f"    sense: below-knee ratio {1/m_lo:.3f} (intercept "
          f"{b_lo*1e3:+.3f} mV) | above-knee ratio {ratio:.4f} "
          f"(intercept {off*1e3:+.3f} mV)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchdir", default=None,
                    help="directory of campaign CSVs (default: newest bench/*)")
    ap.add_argument("--outdir", default="../paper/figures")
    ap.add_argument("--rload", type=float, default=None,
                    help="override the load resistance recorded in the headers")
    args = ap.parse_args()

    bd = args.benchdir
    if bd is None:
        dirs = sorted(glob.glob("bench/*"))
        if not dirs:
            raise SystemExit("no bench/ data — run run_campaign.py first")
        bd = dirs[-1]
    os.makedirs(args.outdir, exist_ok=True)

    print(f"reading {bd}" + (f", R override {args.rload} Ω" if args.rload else ""))
    fig_transfer(bd, args.outdir, args.rload)
    fig_deviation(bd, args.outdir, args.rload)
    fig_linearity(bd, args.outdir, args.rload)
    fig_precision(bd, args.outdir, args.rload)
    fig_vsense(bd, args.outdir, args.rload)


if __name__ == "__main__":
    main()
