#!/usr/bin/env python3
"""Koi 8x8 system architecture block diagram (fig0_architecture.png)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path

HERE = Path(__file__).parent
BLUE, AQUA, YELLOW = "#2a78d6", "#1baf7a", "#eda100"
TEXT, MUTED, EDGE = "#0b0b0b", "#52514e", "#b9b8b2"

fig, ax = plt.subplots(figsize=(12.5, 6.0))
ax.set_xlim(0, 12.5); ax.set_ylim(0, 6.0); ax.axis("off")

def box(x, y, w, h, title, lines=(), ec=EDGE, title_c=TEXT, lw=1.4, fs=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.03,rounding_size=0.08",
                                fc="white", ec=ec, lw=lw))
    ty = y + h - 0.30
    ax.text(x + w / 2, ty, title, ha="center", va="center",
            fontsize=fs, fontweight="bold", color=title_c)
    for i, ln in enumerate(lines):
        ax.text(x + w / 2, ty - 0.32 - i * 0.28, ln, ha="center", va="center",
                fontsize=fs - 1.5, color=MUTED)

def arrow(x1, y1, x2, y2, color=MUTED, two_way=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="<|-|>" if two_way else "-|>",
                                 mutation_scale=16, lw=1.6, color=color))

# header
ax.text(0.25, 5.72, "Koi 8×8 — 64-channel current-source driver / readout",
        fontsize=13, fontweight="bold", color=TEXT, va="center")
ax.text(0.25, 5.40, "one controller, 8 hot-pluggable analog boards, 8 channels each",
        fontsize=10, color=MUTED, va="center")

# Host PC
box(0.25, 2.2, 1.65, 1.3, "Host PC", ["Python GUI /", "experiment scripts"])
arrow(1.95, 2.85, 2.95, 2.85, two_way=True)
ax.text(2.45, 3.02, "USB serial\nASCII protocol", ha="center", va="bottom",
        fontsize=8, color=MUTED)

# Motherboard container
box(3.0, 0.5, 2.6, 4.4, "Motherboard", fs=11)
box(3.2, 3.35, 2.2, 1.05, "RP2040 (Pico)", ["command server fw1.1", "SPI0 + SPI1 masters"], ec=BLUE, title_c=BLUE, fs=9.5)
box(3.2, 2.2, 2.2, 1.05, "2× 74HC138", ["per-board chip-select", "(ADC bank / DAC bank)"], fs=9.5)
box(3.2, 1.05, 2.2, 1.05, "SN74LV595", ["per-board front-end", "enables (XTR_OD)"], fs=9.5)
box(3.2, 0.62, 2.2, 0.36, "12 V DC-DC + 3.3 V", fs=8.5)

# Motherboard -> daughterboards
arrow(5.65, 2.85, 7.55, 2.85)
ax.text(6.6, 3.02, "8× PCIe-form slots\nSPI + CS + EN + 12 V", ha="center", va="bottom",
        fontsize=8.5, color=MUTED)

# Daughterboard stack (x8)
for off in (0.30, 0.20, 0.10):
    ax.add_patch(FancyBboxPatch((7.6 + off, 0.5 + off), 2.95, 4.4,
                                boxstyle="round,pad=0.03,rounding_size=0.08",
                                fc="white", ec=EDGE, lw=1.0))
box(7.6, 0.5, 2.95, 4.4, "Daughterboard  (×8)", fs=11)
box(7.75, 3.3, 1.35, 1.05, "DAC80508", ["16-bit", "8-ch DAC"], ec=BLUE, title_c=BLUE, fs=9.5)
box(9.3, 3.3, 1.15, 1.05, "8× XTR200", ["current", "sources"], ec=AQUA, title_c=AQUA, fs=9)
arrow(9.12, 3.82, 9.28, 3.82)
box(7.75, 1.85, 1.35, 1.05, "AD7193", ["24-bit ΔΣ", "8-ch ADC"], ec=BLUE, title_c=BLUE, fs=9.5)
box(9.3, 1.85, 1.15, 1.05, "6:1 sense", ["dividers", "(100k/20k)"], fs=9)
arrow(9.28, 2.37, 9.12, 2.37)
box(7.75, 0.62, 2.7, 0.9, "REF5030 — 3.0 V reference", ["shared by DAC + ADC"], ec=YELLOW, title_c="#8a5f00", fs=9.5)
ax.add_patch(FancyArrowPatch((9.875, 3.28), (9.875, 2.92), arrowstyle="-", lw=1.4, color=MUTED))
arrow(10.47, 2.37, 11.25, 2.37)

# Output
box(11.3, 1.7, 1.05, 1.7, "64 ch", ["0–6 mA", "sourced +", "read back", "(V, R, P)"], ec=AQUA, title_c=AQUA, fs=9.5)
ax.text(11.825, 1.45, "TiN thermo-optic\nheaters on\nphotonic chip", ha="center", va="top",
        fontsize=8.5, color=MUTED, style="italic")

fig.savefig(HERE / "fig0_architecture.png", dpi=300, bbox_inches="tight", facecolor="white")
print("fig0_architecture.png written")
