#!/usr/bin/env python3
"""
padne_plots_layers.py — same maps as padne_plots_v2.py, but one PNG per copper layer.

Usage:
    python3 padne_plots_layers.py padne_out_v4_fine results_v4_fine/layers
    python3 padne_plots_layers.py padne_out_v3/Archive.zip results_v3/layers

Requires: numpy, matplotlib

Outputs, for every layer present in the export:
    current_density_black_<layer>.png  — sheet current density (A/m), log scale, black bg
    ir_drop_<layer>.png                — per-net voltage deviation from net median (µV)

Colour scales (J_VMIN/J_VMAX, IR_RANGE_UV) and the SIGMA/THICKNESS constants are taken
from padne_plots_v2, so the per-layer images are directly comparable to each other and to
the combined figures. The IR-drop net references are computed across *all* layers before
splitting, so a net keeps one reference level in every image.
"""

import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm

import padne_plots_v2 as pp


def plot_current_density_layer(name, pieces, outfile):
    fig, ax = plt.subplots(figsize=(9, 7.5), facecolor="black")
    norm = LogNorm(vmin=pp.J_VMIN, vmax=pp.J_VMAX)
    allK = []
    for P, V, T in pieces:
        K = np.clip(pp.triangle_current_density(P, V, T), 1e-4, None)
        ax.tripcolor(P[:, 0], P[:, 1], T, facecolors=K, norm=norm, cmap="inferno")
        allK.append(K)
    ax.set_aspect("equal")
    ax.set_facecolor("black")
    ax.set_title(f"{name} — sheet current density (A/m, log scale)",
                 fontsize=12, color="white")
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#444444")
    sm = plt.cm.ScalarMappable(norm=norm, cmap="inferno")
    cb = fig.colorbar(sm, ax=ax, shrink=0.75)
    cb.set_label("sheet current density (A/m) — log scale", color="white")
    cb.ax.yaxis.set_tick_params(color="white")
    plt.setp(cb.ax.get_yticklabels(), color="white")
    plt.savefig(outfile, dpi=pp.DPI, bbox_inches="tight", facecolor="black")
    plt.close(fig)

    stat = ""
    if allK:
        allK = np.concatenate(allK)
        allK = allK[np.isfinite(allK)]
        if len(allK):
            stat = (f"K p50={np.percentile(allK, 50):.3g}  "
                    f"p99={np.percentile(allK, 99):.3g}  max={allK.max():.3g} A/m")
    print(f"  {name:8s} {stat}\n           -> {outfile}")


def plot_ir_drop_layer(name, pieces, off, cref, outfile):
    fig, ax = plt.subplots(figsize=(9, 7.5))
    for P, V, T in pieces:
        key = round(float(np.median(V)) - off, 2)
        if key not in cref:
            continue
        dv = (V - cref[key]) * 1e6
        ax.tripcolor(P[:, 0], P[:, 1], T, dv, cmap="RdBu_r",
                     vmin=-pp.IR_RANGE_UV, vmax=pp.IR_RANGE_UV, shading="gouraud")
    ax.set_aspect("equal")
    ax.set_facecolor("#f2f2f2")
    ax.set_title(f"{name} — deviation from each net's median (µV)", fontsize=12)
    ax.set_xticks([]), ax.set_yticks([])
    sm = plt.cm.ScalarMappable(
        norm=plt.Normalize(-pp.IR_RANGE_UV, pp.IR_RANGE_UV), cmap="RdBu_r")
    fig.colorbar(sm, ax=ax, shrink=0.75, label="deviation (µV)")
    plt.savefig(outfile, dpi=pp.DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"           -> {outfile}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {src} ...")
    layers = pp.gather_input(src)
    if not any(len(v) for v in layers.values()):
        sys.exit("ERROR: no finite data in any layer. The padne solve failed "
                 "(look for 'Matrix is exactly singular' in the solve log) - "
                 "fix the directives and re-solve before plotting.")
    for name, pieces in layers.items():
        pts = sum(len(v) for _, v, _ in pieces)
        print(f"  {name}: {len(pieces)} islands, {pts} mesh points")

    # Net references come from the whole board, so a net sits at the same
    # reference level in every per-layer image.
    off = pp.global_offset(layers)
    cref = pp.net_cluster_refs(layers, off)

    for name, pieces in layers.items():
        if not pieces:
            print(f"  {name}: no plottable pieces, skipped")
            continue
        plot_current_density_layer(
            name, pieces, outdir / f"current_density_black_{name}.png")
        plot_ir_drop_layer(
            name, pieces, off, cref, outdir / f"ir_drop_{name}.png")
    print("Done.")
