#!/usr/bin/env python3
"""
run_campaign.py — the full single-channel validation campaign, headless.

Runs every koi_bench routine back to back and writes the CSVs into
`bench/<date>/`. The same routines the GUI's Bench row calls; this exists so the
whole set can run unattended in one go.

R is a placeholder here (four-wire ohms needs the 2100's SENSE pair connected).
That is deliberate and safe: every routine stores raw DMM volts, so correcting R
afterwards is a re-analysis, not a re-acquisition. Fix it with
`--rload` once the true value is known and nothing needs re-measuring.

Usage:
    python3 run_campaign.py                       # ch0, R placeholder
    python3 run_campaign.py --rload 996.4 --channel 0
    python3 run_campaign.py --quick               # short version, ~3 min
"""

import argparse
import os
import sys
import time
import traceback

import koi_bench as kb
from koi_gui import KoiLink, autodetect_port
from keithley2100 import Keithley2100, autodetect_usbtmc

# The characterized operating point: past RATE 96 / AVG 4 the ADC is at its
# noise floor and further averaging buys nothing.
RATE, AVG = 96, 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--rload", type=float, default=996.4)
    ap.add_argument("--r-source", default="externally measured 4-wire (not via jig)")
    ap.add_argument("--settle", type=float, default=kb.DEFAULT_SETTLE)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--quick", action="store_true",
                    help="fewer points everywhere, for a fast end-to-end check")
    args = ap.parse_args()

    outdir = args.outdir or kb.bench_outdir()
    q = args.quick

    link = dmm = None
    results, failures = [], []
    try:
        port = autodetect_port()
        link = KoiLink(port)
        print(f"Koi   : {link.command('*IDN?')}  on {port}")
        print(f"boards: {link.command('RESCAN')}")

        # Set the operating point explicitly. The flashed firmware boots at
        # RATE 8, which returns raw code 0 on both read paths while still
        # replying OK — an ADC column taken there is silently all zeros.
        link.command(f"RATE {RATE}")
        link.command(f"AVG {AVG}")
        print(f"adc   : {link.command('ADC?')}")

        dmm = Keithley2100(autodetect_usbtmc())
        print(f"DMM   : {dmm.idn()}")
        print(f"R     : {args.rload} Ω  ({args.r_source})")
        print(f"out   : {outdir}\n")

        def ctx():
            return kb.BenchCtx(
                link, dmm, args.channel, args.rload, args.r_source,
                outdir=outdir, settle=args.settle, xtr_mask=0x01,
                emit=lambda s: print(f"    {s}", flush=True),
                stopped=lambda: False)

        # (label, callable) — ordered cheapest-first so a wiring problem shows
        # up in the first minute rather than the twentieth.
        plan = [
            ("set vs 2100", lambda: kb.meas_set_vs_dmm(
                ctx(), npoints=16 if q else 64, nreads=8)),
            ("drive sweep", lambda: kb.meas_drive_sweep(
                ctx(), npoints=12 if q else 48, nreads=8, koi_reads=4)),
            ("low current", lambda: kb.meas_low_current(ctx(), nreads=8)),
            ("single codes", lambda: kb.meas_codes(
                ctx(), centres=(3.0,) if q else (1.0, 3.0, 5.0),
                ncodes=9 if q else 33, nreads=8)),
            ("INL randomized", lambda: kb.meas_inl(
                ctx(), npoints=16 if q else 48, passes=1 if q else 2,
                nreads=8, order="random")),
            # With the blower holding the load at ambient, the monotonic ramp
            # should now agree with the randomized run. Taking both makes that a
            # measured check rather than an assumption.
            ("INL monotonic", lambda: kb.meas_inl(
                ctx(), npoints=16 if q else 48, passes=1, nreads=8,
                order="monotonic")),
            ("noise grid", lambda: kb.meas_noise(
                ctx(), nreads=10 if q else 30, discard=5)),
            ("settling", lambda: kb.meas_settling(ctx(), duration=6.0)),
        ]

        t_all = time.time()
        for name, fn in plan:
            print(f"--- {name} ---", flush=True)
            t0 = time.time()
            try:
                out = fn()
                results.append((name, out[0], out[1]))
                print(f"  OK  ({time.time()-t0:.0f} s)  {out[1]}\n", flush=True)
            except Exception as e:
                failures.append((name, f"{type(e).__name__}: {e}"))
                print(f"  FAILED ({type(e).__name__}: {e})\n", flush=True)
                traceback.print_exc()

        print(f"\n{'='*78}\nCAMPAIGN SUMMARY — ch{args.channel}, "
              f"{time.time()-t_all:.0f} s total\n{'='*78}")
        for name, path, summary in results:
            print(f"{name:16s} {os.path.basename(path)}\n{'':16s} {summary}")
        for name, err in failures:
            print(f"{name:16s} FAILED — {err}")
        print(f"\n{len(results)} ok, {len(failures)} failed → {outdir}/")

    except Exception:
        traceback.print_exc()
        return 1
    finally:
        if link:
            try:
                link.command(f"ISET {args.channel} 0")
                link.command("XTR 0x00")
                print(f"\nidled: ch{args.channel} 0 mA, front-ends off")
                link.close()
            except Exception as e:
                print("cleanup:", e)
        if dmm:
            dmm.close()
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
