#!/usr/bin/env python3
"""gain_sweep.py — measure the ADC with all XTR200 front-ends OFF, at each PGA gain.

With the front-ends disabled (`XTR 0x00`) no channel sources any current, so what
the AD7193 reads is the pure ADC/PCB/divider offset floor. This steps the PGA
gain through 1/8/16/32/64/128 (each `GAIN` runs a zero-scale recal in firmware),
takes `--avg` MEASA? scans per gain, and writes a wide CSV: one row per gain,
columns g0..g63 of the reported raw ADC-pin voltage (already ÷gain in firmware,
so the numbers are input-referred and directly comparable across gains).

    ~/.platformio/penv/bin/python tools/gain_sweep.py [--port /dev/ttyACM0]
        [--avg 8] [--settle 0.3] [--gains 1,8,16,32,64,128] [--out FILE.csv]

(pyserial only — no jax venv needed.)
"""
import argparse
import csv
import os
import statistics
import sys
import time
from datetime import datetime

from koi_bench import bench_outdir
from koi_gui import KoiLink, autodetect_port, NUM_BOARDS, CH_PER_BOARD, TOTAL_CH

DEFAULT_GAINS = [1, 8, 16, 32, 64, 128]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default=None, help="serial port (default: autodetect)")
    ap.add_argument("--gains", default=",".join(map(str, DEFAULT_GAINS)),
                    help="comma-separated PGA gains to sweep")
    ap.add_argument("--avg", type=int, default=8,
                    help="MEASA? scans averaged per gain (default 8)")
    ap.add_argument("--settle", type=float, default=0.3,
                    help="seconds to settle after each GAIN before measuring")
    ap.add_argument("--bipolar", action="store_true",
                    help="use bipolar mode (±FS) so the near-zero offset floor "
                         "reads as signed values instead of clamping at 0")
    ap.add_argument("--out", default=None, help="CSV path (default: auto-timestamped)")
    args = ap.parse_args()

    gains = [int(g) for g in args.gains.split(",") if g.strip()]

    port = args.port or autodetect_port()
    if not port:
        sys.exit("no serial port found (is the Koi plugged in?)")
    link = KoiLink(port)

    out = args.out or os.path.join(bench_outdir(),
                                   f"gain_sweep_{datetime.now():%Y%m%d_%H%M%S}.csv")
    try:
        print("IDN:", link.command("*IDN?"))
        active = link.rescan()
        if not active:
            sys.exit("RESCAN failed / no active boards")
        boards = [b for b in range(NUM_BOARDS) if active & (1 << b)]
        print(f"active boards: {boards} (0x{active:02X})")

        # Front-ends OFF → measuring the pure ADC/PCB offset floor.
        print("XTR off:", link.command("XTR 0x00"))
        if args.bipolar:
            print("BIPOLAR:", link.command("BIPOLAR ON"))
        time.sleep(0.2)

        rows = []            # each: {"gain":g, "v":[64 means]}
        for g in gains:
            reply = link.command(f"GAIN {g}")
            print(f"GAIN {g}: {reply}")
            if reply is None or reply.startswith("ERR"):
                print(f"  !! skipping gain {g} (no OK reply)")
                continue
            time.sleep(args.settle)

            # Average `avg` scans per channel (ignoring NaN for absent channels).
            acc = [[] for _ in range(TOTAL_CH)]
            for _ in range(max(1, args.avg)):
                vals = link.measure_all()
                if not vals:
                    continue
                for i, v in enumerate(vals):
                    if v == v:                      # not NaN
                        acc[i].append(v)
            mean = [statistics.fmean(a) if a else float("nan") for a in acc]
            rows.append({"gain": g, "v": mean})

            # Console summary: mean/µV over populated channels.
            pv = [m * 1e6 for m in mean if m == m]
            if pv:
                print(f"  populated ch: mean={statistics.fmean(pv):+8.2f} µV  "
                      f"min={min(pv):+8.2f}  max={max(pv):+8.2f}  (input-referred)")

        # Restore gain 1, unipolar, and re-enable the boards we found.
        link.command("GAIN 1")
        if args.bipolar:
            link.command("BIPOLAR OFF")
        link.command(f"XTR 0x{active:02X}")

        # Write wide CSV: gain, g0..g63 (volts).
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["gain"] + [f"g{i}" for i in range(TOTAL_CH)])
            for r in rows:
                w.writerow([r["gain"]] +
                           ["" if v != v else f"{v:.9f}" for v in r["v"]])
        print(f"\nwrote {out}  ({len(rows)} gains × {TOTAL_CH} channels, XTR off)")
    finally:
        link.close()


if __name__ == "__main__":
    main()
