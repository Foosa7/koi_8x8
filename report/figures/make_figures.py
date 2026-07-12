#!/usr/bin/env python3
"""Generate report figures for Koi 8x8 from bench data.

Source: firmware/motherboard-test/tools/rsweep_boards012_20260703_170458.csv
(3 daughterboards / 24 channels, 0.1-6 mA sweep into ~820 ohm loads,
measured 2026-07-03). Channel 22 had no load fitted during the run and is
excluded from fits. heater_V is the divider-corrected (x6) load voltage;
I_heat_mA is the commanded current minus the analytic 120k divider draw.

Run with the matplotlib venv:
  <venv>/bin/python3 make_figures.py
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

HERE = Path(__file__).parent
CSV = HERE / "../../firmware/motherboard-test/tools/rsweep_boards012_20260703_170458.csv"
BOARD_COLORS = ["#2a78d6", "#1baf7a", "#eda100"]  # validated categorical slots 1-3
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e5e4e0"
EXCLUDED = {22}  # no load fitted during this run

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.edgecolor": MUTED, "axes.labelcolor": TEXT,
    "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": TEXT, "font.size": 11,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "savefig.dpi": 300, "savefig.bbox": "tight",
})

d = np.genfromtxt(CSV, delimiter=",", names=True)
g = d["g"].astype(int)
I, V, Ih = d["I_cmd_mA"], d["heater_V"], d["I_heat_mA"]

# per-channel linear fits over the 0.25-6 mA working range
fits = {}
for ch in range(24):
    if ch in EXCLUDED:
        continue
    m = (g == ch) & (I >= 0.25) & ~np.isnan(V)
    slope, icpt = np.polyfit(Ih[m], V[m], 1)
    resid_uA = (V[m] - (slope * Ih[m] + icpt)) / slope * 1000
    fits[ch] = dict(m=m, R=slope * 1000, offs_uA=-icpt / slope * 1000,
                    resid_uA=resid_uA, I=I[m])

def board_label(ax, ch, x, y, seen):
    b = ch // 8
    if b not in seen:
        ax.annotate(f"Board {b}", (x, y), xytext=(6, 0), textcoords="offset points",
                    color=BOARD_COLORS[b], fontsize=10, fontweight="bold", va="center")
        seen.add(b)

# ---------------------------------------------------------------- fig 1: V-I
fig, ax = plt.subplots(figsize=(7.2, 4.6))
seen = set()
for ch, f in fits.items():
    b = ch // 8
    ax.plot(I[f["m"]], V[f["m"]], color=BOARD_COLORS[b], lw=1.6, alpha=0.75)
handles = [plt.Line2D([], [], color=BOARD_COLORS[b], lw=2, label=f"Board {b}") for b in range(3)]
ax.legend(handles=handles, frameon=False, loc="upper left", fontsize=9)
ax.annotate("all 23 curves overlap within the line width\n(slope spread < 1%, set by load tolerance)",
            (0.97, 0.06), xycoords="axes fraction", ha="right", va="bottom",
            fontsize=10, color=MUTED)
ax.set_xlabel("Commanded current (mA)")
ax.set_ylabel("Load voltage (V)")
ax.set_title("V–I response, 23 loaded channels across 3 daughterboards", fontsize=12, pad=12)
ax.set_xlim(0, 6.4)
fig.savefig(HERE / "fig1_linearity_23ch.png")
plt.close(fig)

# ------------------------------------------------- fig 2: residuals from fit
fig, ax = plt.subplots(figsize=(7.2, 4.2))
worst = 0.0
for ch, f in fits.items():
    ax.plot(f["I"], f["resid_uA"], color=BOARD_COLORS[ch // 8], lw=1.4, alpha=0.7)
    worst = max(worst, np.max(np.abs(f["resid_uA"])))
ax.axhline(0, color=MUTED, lw=0.8)
ax.set_xlabel("Commanded current (mA)")
ax.set_ylabel("Deviation from linear fit (µA)")
ax.set_title("Linearity residuals per channel, 0.25–6 mA", fontsize=12, pad=12)
ax.annotate(f"worst case ±{worst:.2f} µA\n(≈{worst/5750*100:.4f}% of span)",
            (0.98, 0.04), xycoords="axes fraction", ha="right", va="bottom",
            fontsize=10, color=MUTED)
handles = [plt.Line2D([], [], color=BOARD_COLORS[b], lw=2, label=f"Board {b}") for b in range(3)]
ax.legend(handles=handles, frameon=False, loc="upper right", fontsize=9)
fig.savefig(HERE / "fig2_linearity_residuals.png")
plt.close(fig)

# ---------------------------------- fig 3: per-channel uniformity (R, offset)
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True,
                               gridspec_kw={"hspace": 0.18})
chs = sorted(fits)
Rs = [fits[c]["R"] for c in chs]
Os = [fits[c]["offs_uA"] for c in chs]
for ax, ys, ylab in ((ax1, Rs, "Fitted load resistance (Ω)"),
                     (ax2, Os, "Pre-cal offset current (µA)")):
    for c, y in zip(chs, ys):
        ax.plot(c, y, "o", ms=7, color=BOARD_COLORS[c // 8],
                markeredgecolor="white", markeredgewidth=1.2)
    mean = np.mean(ys)
    ax.axhline(mean, color=MUTED, lw=0.8, ls=(0, (4, 3)))
    ax.annotate(f"mean {mean:.1f}", (23.5, mean), xytext=(6, 0),
                textcoords="offset points", fontsize=9, color=MUTED, va="center",
                bbox=dict(fc="white", ec="none", pad=1.5))
    ax.set_ylabel(ylab, fontsize=10)
ax1.set_title("Per-channel uniformity (linear fit, 0.25–6 mA)", fontsize=12, pad=12)
ax2.set_xlabel("Global channel index (board × 8 + channel)")
ax2.set_xticks(range(0, 24, 2))
ax1.annotate("nominal 820 Ω ±1% loads", (0.02, 0.06), xycoords="axes fraction",
             fontsize=9, color=MUTED)
ax2.annotate("systematic ≈24 µA, removed by single-point calibration\n(planned Keithley 2400 campaign trims to the ±4 µA XTR200 floor)",
             (0.02, 0.72), xycoords="axes fraction", fontsize=9, color=MUTED)
handles = [plt.Line2D([], [], marker="o", ls="", color=BOARD_COLORS[b],
                      markeredgecolor="white", label=f"Board {b}") for b in range(3)]
ax1.legend(handles=handles, frameon=False, loc="lower right", fontsize=9)
fig.savefig(HERE / "fig3_channel_uniformity.png")
plt.close(fig)

print("figures written:", *sorted(p.name for p in HERE.glob("fig*.png")))
