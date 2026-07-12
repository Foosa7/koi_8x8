#!/usr/bin/env python3
"""
sweep_ch0.py — dead-simple current sweep of one channel, no GUI.

Steps current start..stop on a channel, reads the raw ADC voltage (MEAS? g),
and prints raw_V, heater_V (×divider), the divider-corrected heater current, and
R. Just to eyeball the output with a resistor connected.

    ~/.platformio/penv/bin/python tools/sweep_ch0.py
    ... --port /dev/ttyACM1 --ch 0 --start 0 --stop 5 --step 0.5 --settle 0.3
"""
import argparse
import sys
import time

import serial

DIVIDER = 6.0            # 6:1 sense divider (heater_V = raw_V × 6)
DIVIDER_BOTTOM = 20000.0  # divider current = raw_V / 20k  (= V_heater/120k)


def read_reply(ser, timeout=5.0):
    """Return the first non-comment, non-blank reply line (or '' on timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if line and not line.startswith("#"):
            return line
    return ""


def cmd(ser, s, timeout=5.0):
    ser.reset_input_buffer()
    ser.write((s + "\n").encode())
    ser.flush()
    return read_reply(ser, timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM1")
    ap.add_argument("--ch", type=int, default=0)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--stop", type=float, default=5.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--settle", type=float, default=0.3)
    args = ap.parse_args()

    ser = serial.Serial(args.port, 115200, timeout=1.0)
    time.sleep(0.3)
    ser.reset_input_buffer()

    print("IDN:", cmd(ser, "*IDN?"))
    print("XTR:", cmd(ser, f"XTR {1 << (args.ch // 8)}"))   # enable this board's front-ends

    print(f"\n{'I_cmd':>7} {'raw_mV':>9} {'heater_V':>9} "
          f"{'I_heat':>8} {'R_ohm':>9}")
    print("-" * 46)

    ma = args.start
    try:
        while ma <= args.stop + 1e-9:
            cmd(ser, f"ISET {args.ch} {ma:.4f}")
            time.sleep(args.settle)
            rep = cmd(ser, f"MEAS? {args.ch}")
            try:
                raw = float(rep)
            except ValueError:
                raw = float("nan")
            heater_v = raw * DIVIDER
            i_div = raw / DIVIDER_BOTTOM * 1e3          # mA stolen by divider
            i_heat = ma - i_div                          # true heater current, mA
            r = heater_v / (i_heat * 1e-3) if i_heat > 0 else float("nan")
            print(f"{ma:7.3f} {raw*1e3:9.3f} {heater_v:9.4f} "
                  f"{i_heat:8.4f} {r:9.2f}")
            ma += args.step
    finally:
        cmd(ser, f"ISET {args.ch} 0")                    # leave channel at 0
        ser.close()


if __name__ == "__main__":
    sys.exit(main())
