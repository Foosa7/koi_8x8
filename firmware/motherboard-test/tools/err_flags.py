#!/usr/bin/env python3
"""Read all 64 XTR200 ERRORFLAG bits via the firmware's ERR? command.

The flags come from one SN74LV165 per daughterboard, daisy-chained and
bit-banged by the Pico (CP=GP16, Q7=GP17, PL=GP18). The firmware reports the
RAW input level per channel (bit g = channel g's EF pin, g = board*8 + ch);
this script applies polarity. The XTR200 EF output is open-drain with an
on-board pullup, so 1 = OK and 0 = FAULT (use --active-high if a board
buffers/inverts it).

A missing daughterboard breaks the 165 chain, so bits for boards upstream of
the gap are garbage — boards outside the firmware's active set print '-'.

Run inside the jax venv:
    source /home/foosa/jax-env/jax_env/bin/activate
    python err_flags.py [--port /dev/ttyACM0] [--loop 1.0] [--active-high]
"""

import argparse
import sys
import time

from koi_gui import KoiLink, autodetect_port, NUM_BOARDS, CH_PER_BOARD


def print_grid(raw, active_mask, fault_is_low):
    """8×8 grid: '.' = OK, 'F' = FAULT, '-' = board not populated."""
    print("board  " + "  ".join(f"ch{c}" for c in range(CH_PER_BOARD)))
    n_faults = 0
    for b in range(NUM_BOARDS):
        if not (active_mask >> b) & 1:
            cells = ["-"] * CH_PER_BOARD
        else:
            cells = []
            for c in range(CH_PER_BOARD):
                level = (raw >> (b * CH_PER_BOARD + c)) & 1
                fault = (level == 0) if fault_is_low else (level == 1)
                n_faults += fault
                cells.append("F" if fault else ".")
        print(f"  {b}    " + "    ".join(cells))
    print(f"raw=0x{raw:016X}  active=0x{active_mask:02X}  faults={n_faults}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", default=None, help="serial port (default: autodetect)")
    ap.add_argument("--mask", type=lambda s: int(s, 0), default=None,
                    help="active-board bitmask override (default: RESCAN result)")
    ap.add_argument("--loop", type=float, default=None, metavar="SECS",
                    help="poll repeatedly at this interval instead of once")
    ap.add_argument("--active-high", action="store_true",
                    help="treat a HIGH EF level as the fault (default: low = fault)")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        sys.exit("no serial port found (is the Koi plugged in?)")
    link = KoiLink(port)
    try:
        if args.mask is not None:
            active = args.mask
        else:
            active = link.rescan()
            if active is None:
                sys.exit("RESCAN failed — no active-board mask")

        while True:
            raw = link.error_flags()
            if raw is False:
                sys.exit("firmware has no ERR? — reflash with the errorflag build")
            if raw is None:
                print("ERR? failed (no/invalid reply)", file=sys.stderr)
            else:
                print_grid(raw, active, fault_is_low=not args.active_high)
            if args.loop is None:
                break
            time.sleep(args.loop)
            print()
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    main()
