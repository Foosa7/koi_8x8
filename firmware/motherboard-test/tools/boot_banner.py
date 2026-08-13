#!/usr/bin/env python3
"""Capture the Koi boot banner — the raw AD7193 ID byte for all 8 positions.

`adc.begin()` prints one line per device ("# AD7193 #n OK, ID=0x.." or
"# AD7193 #n ID mismatch: 0x..") before board detection runs, so the banner is
the only place the RAW id byte is visible over the wire. That byte separates
"the ADC is dead" from "the ADC never got selected":

    lower nibble 0x2   part alive, SPI fine  -> failure is downstream (clock,
                       reference, power) and NOT dead silicon
    0xFF               MISO floating high    -> no device driving: CS never
                       asserts, MISO open, or the board is unpowered
    0x00               MISO stuck low        -> documented on this hardware as
                       the 74HC138 G1 (pin 6) enable problem, which disables the
                       decoder so every CS stays high (see CLAUDE.md gotchas)

The firmware waits for the host to open the port before printing, so this polls
for the serial node and opens it the moment it appears. Start it FIRST, then
replug the Pico's USB (or hit its reset).

    source /home/foosa/jax-env/jax_env/bin/activate
    python3 boot_banner.py
"""

import glob
import sys
import time

import serial

DEADLINE_S = 180.0      # how long to wait for a replug
QUIET_S = 12.0          # stop this long after the last line


def find_node():
    # by-id is stable across replugs; the ttyACMn number is not.
    for pat in ("/dev/serial/by-id/*Pico*", "/dev/ttyACM*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


def main():
    start = time.time()

    # The banner only prints once, at boot. If a node is ALREADY present the
    # board is up and has long since printed it, so opening that node captures
    # nothing (this bit me once). Wait for the device to disappear first, so we
    # only ever open a genuinely fresh enumeration.
    if find_node():
        print("Pico is currently up. UNPLUG its USB now...")
        while time.time() - start < DEADLINE_S and find_node():
            time.sleep(0.05)
        if find_node():
            print("Still present after the deadline — nothing captured.")
            return 1
        print("Unplugged. Now PLUG IT BACK IN.")
    else:
        print("Waiting for the Pico to enumerate — plug its USB in now.")

    ser = None
    while time.time() - start < DEADLINE_S:
        node = find_node()
        if node:
            try:
                ser = serial.Serial(node, 115200, timeout=1.0)
                print(f"# opened {node}\n")
                break
            except (OSError, serial.SerialException):
                pass        # node exists but isn't ready yet — keep polling
        time.sleep(0.05)

    if ser is None:
        print("No Pico appeared. Is it plugged in?")
        return 1

    ids = {}
    last = time.time()
    try:
        while time.time() - last < QUIET_S:
            try:
                raw = ser.readline()
            except (OSError, serial.SerialException):
                print("# port vanished (device reset again) — re-run and replug once")
                break
            if not raw:
                continue
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            print(line)
            sys.stdout.flush()
            last = time.time()
            if "AD7193 #" in line and "ID=" in line or "ID mismatch" in line:
                dev = line.split("#")[-1].split()[0] if "#" in line else "?"
                val = line.rsplit("0x", 1)[-1].strip() if "0x" in line else "?"
                ids[dev] = val
            if line.startswith("# READY"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    if ids:
        print("\n=== raw AD7193 ID bytes ===")
        for dev in sorted(ids):
            v = ids[dev]
            try:
                nib = int(v, 16) & 0x0F
            except ValueError:
                nib = None
            if nib == 0x02:
                verdict = "ALIVE (SPI ok)"
            elif v.upper().rstrip("H") in ("FF",):
                verdict = "MISO floating high — CS never asserted / unpowered"
            elif v.upper().rstrip("H") in ("0", "00"):
                verdict = "MISO stuck low — check 74HC138 G1 (pin 6) tied to Vcc"
            else:
                verdict = "unexpected"
            print(f"  device {dev}: 0x{v}  -> {verdict}")
    else:
        print("\nNo '# AD7193 #n ID=' lines seen — the banner was missed. "
              "Start this script BEFORE replugging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
