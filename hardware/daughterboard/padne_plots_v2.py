#!/usr/bin/env python3
"""
padne_plots.py — render current-density and IR-drop maps from a padne ParaView export.

Usage:
    python3 padne_plots.py padne_out.zip           # or a directory of .vtu files
    python3 padne_plots.py padne_out.zip results/  # optional output directory

Requires: numpy, matplotlib   (pip install numpy matplotlib)

Outputs:
    current_density_black.png  — sheet current density (A/m), log scale, black background
    ir_drop.png                — per-net voltage deviation from net median (µV)

Notes:
  * Each .vtu layer file contains one <Piece> per connected copper island.
  * Floating/unconnected islands solve to garbage values (~1e8 V); they are skipped.
  * Copper conductivity 5.8e7 S/m and 35 µm thickness are assumed for all layers;
    edit SIGMA/THICKNESS if your stackup differs (e.g. 17.5 µm inner layers).
"""

import sys
import zipfile
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm

# ----------------------------- configuration ---------------------------------

SIGMA = 5.8e7          # copper conductivity, S/m
THICKNESS = 35e-6      # copper thickness, m (per layer; edit if stackup differs)
MAX_PLAUSIBLE_DROP = 1.0  # V; a copper island with more IR drop than this is
                          # solver garbage (unconnected island), skip it.
                          # NOTE: absolute level is NOT used - if no VOLTAGE
                          # source lands on connected copper the whole board
                          # floats at an arbitrary offset, which is harmless
                          # because every view here is relative.
J_VMIN, J_VMAX = 1e-2, 30   # current density color range, A/m (log scale)
IR_RANGE_UV = 15            # IR-drop color range, ±µV
DPI = 130

LAYER_ORDER = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]

# ----------------------------- data loading ----------------------------------


def parse_floats(text):
    return np.array(text.split(), dtype=float)


def load_pieces(vtu_path):
    """Return a list of (points[N,2], voltage[N], triangles[M,3]) per copper island."""
    root = ET.parse(vtu_path).getroot()
    pieces = []
    for piece in root.findall(".//Piece"):
        volt = None
        for da in piece.find("PointData").findall("DataArray"):
            if da.get("Name") == "voltage":
                volt = parse_floats(da.text)
        pda = piece.find("Points").find("DataArray")
        ncomp = int(pda.get("NumberOfComponents", "3"))
        pts = parse_floats(pda.text).reshape(-1, ncomp)[:, :2]
        tris = None
        for da in piece.find("Cells").findall("DataArray"):
            if da.get("Name") == "connectivity":
                tris = np.array(da.text.split(), dtype=int).reshape(-1, 3)
        if volt is not None and tris is not None and len(tris):
            if not np.all(np.isfinite(volt)):
                continue  # NaN/inf -> failed solve for this piece, skip
            if float(volt.max() - volt.min()) > MAX_PLAUSIBLE_DROP:
                continue  # implausible IR drop -> unconnected island
            pieces.append((pts, volt, tris))
    return pieces


def gather_input(arg):
    """Accept a zip file or a directory; return {layer_name: [pieces...]}."""
    p = Path(arg)
    if p.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(p) as z:
            z.extractall(tmp)
        vtus = sorted(tmp.rglob("*.vtu"))
    else:
        vtus = sorted(p.rglob("*.vtu"))
    if not vtus:
        sys.exit(f"No .vtu files found in {arg}")
    layers = {}
    for f in vtus:
        raw = len(ET.parse(f).getroot().findall('.//Piece'))
        layers[f.stem] = load_pieces(f)
        skipped = raw - len(layers[f.stem])
        if skipped:
            print(f"  WARNING {f.stem}: {skipped}/{raw} pieces had non-finite "
                  f"voltages (singular solve?) and were skipped")
    # stable, sensible ordering
    ordered = {k: layers[k] for k in LAYER_ORDER if k in layers}
    for k in layers:
        if k not in ordered:
            ordered[k] = layers[k]
    return ordered


# ----------------------------- computation -----------------------------------


def triangle_current_density(P, V, T):
    """Sheet current density per triangle, A/m (linear FEM gradient, coords in mm)."""
    p0, p1, p2 = P[T[:, 0]] / 1000, P[T[:, 1]] / 1000, P[T[:, 2]] / 1000
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    det = (p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) - (p2[:, 0] - p0[:, 0]) * (
        p1[:, 1] - p0[:, 1]
    )
    det[det == 0] = 1e-30
    gx = ((v1 - v0) * (p2[:, 1] - p0[:, 1]) - (v2 - v0) * (p1[:, 1] - p0[:, 1])) / det
    gy = ((v2 - v0) * (p1[:, 0] - p0[:, 0]) - (v1 - v0) * (p2[:, 0] - p0[:, 0])) / det
    return SIGMA * THICKNESS * np.hypot(gx, gy)


def global_offset(layers):
    """Median of the largest piece: removes any arbitrary floating offset."""
    best = (0, 0.0)
    for pieces in layers.values():
        for P, V, T in pieces:
            if len(V) > best[0]:
                best = (len(V), float(np.median(V)))
    return best[1]


def net_cluster_refs(layers, off=0.0):
    """Group islands by rounded median voltage (= net) across all layers."""
    clusters = {}
    for pieces in layers.values():
        for P, V, T in pieces:
            key = round(float(np.median(V)) - off, 2)
            clusters.setdefault(key, []).append(float(np.median(V)))
    return {k: float(np.median(v)) for k, v in clusters.items()}


# ----------------------------- plotting --------------------------------------


def grid(n):
    cols = 2 if n > 1 else 1
    rows = (n + cols - 1) // cols
    return rows, cols


def plot_current_density(layers, outfile):
    n = len(layers)
    rows, cols = grid(n)
    fig, axes = plt.subplots(rows, cols, figsize=(9 * cols, 7.5 * rows), facecolor="black")
    axes = np.atleast_1d(axes).ravel()
    norm = LogNorm(vmin=J_VMIN, vmax=J_VMAX)
    stats = {}
    for ax, (name, pieces) in zip(axes, layers.items()):
        allK = []
        for P, V, T in pieces:
            K = np.clip(triangle_current_density(P, V, T), 1e-4, None)
            ax.tripcolor(P[:, 0], P[:, 1], T, facecolors=K, norm=norm, cmap="inferno")
            allK.append(K)
        if allK:
            allK = np.concatenate(allK)
            allK = allK[np.isfinite(allK)]
        if len(allK):
            stats[name] = (np.percentile(allK, 50), np.percentile(allK, 99), allK.max())
        ax.set_aspect("equal")
        ax.set_facecolor("black")
        ax.set_title(name, fontsize=12, color="white")
        ax.set_xticks([]), ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color("#444444")
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle(
        "Sheet current density (A/m, log scale)", fontsize=13, color="white", y=0.995
    )
    sm = plt.cm.ScalarMappable(norm=norm, cmap="inferno")
    cb = fig.colorbar(sm, ax=axes[:n].tolist(), shrink=0.55)
    cb.set_label("sheet current density (A/m) — log scale", color="white")
    cb.ax.yaxis.set_tick_params(color="white")
    plt.setp(cb.ax.get_yticklabels(), color="white")
    plt.savefig(outfile, dpi=DPI, bbox_inches="tight", facecolor="black")
    plt.close(fig)
    for name, (p50, p99, mx) in stats.items():
        print(f"  {name:8s} K p50={p50:.3g}  p99={p99:.3g}  max={mx:.3g} A/m")
    print(f"  -> {outfile}")


def plot_ir_drop(layers, outfile):
    off = global_offset(layers)
    cref = net_cluster_refs(layers, off)
    n = len(layers)
    rows, cols = grid(n)
    fig, axes = plt.subplots(rows, cols, figsize=(9 * cols, 7.5 * rows))
    axes = np.atleast_1d(axes).ravel()
    for ax, (name, pieces) in zip(axes, layers.items()):
        for P, V, T in pieces:
            key = round(float(np.median(V)) - off, 2)
            if key not in cref:
                continue
            dv = (V - cref[key]) * 1e6
            ax.tripcolor(
                P[:, 0], P[:, 1], T, dv,
                cmap="RdBu_r", vmin=-IR_RANGE_UV, vmax=IR_RANGE_UV, shading="gouraud",
            )
        ax.set_aspect("equal")
        ax.set_facecolor("#f2f2f2")
        ax.set_title(name, fontsize=12)
        ax.set_xticks([]), ax.set_yticks([])
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle(
        "IR drop — voltage deviation from each net's median (µV)", fontsize=13, y=0.995
    )
    sm = plt.cm.ScalarMappable(
        norm=plt.Normalize(-IR_RANGE_UV, IR_RANGE_UV), cmap="RdBu_r"
    )
    fig.colorbar(sm, ax=axes[:n].tolist(), shrink=0.55, label="deviation (µV)")
    plt.savefig(outfile, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {outfile}")


# ----------------------------- main ------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {src} ...")
    layers = gather_input(src)
    if not any(len(v) for v in layers.values()):
        sys.exit("ERROR: no finite data in any layer. The padne solve failed "
                 "(look for 'Matrix is exactly singular' in the solve log) - "
                 "fix the directives and re-solve before plotting.")
    for name, pieces in layers.items():
        pts = sum(len(v) for _, v, _ in pieces)
        print(f"  {name}: {len(pieces)} islands, {pts} mesh points")

    print("Rendering current density ...")
    plot_current_density(layers, outdir / "current_density_black.png")
    print("Rendering IR drop ...")
    plot_ir_drop(layers, outdir / "ir_drop.png")
    print("Done.")
