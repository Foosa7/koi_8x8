#!/usr/bin/env python3
"""Isolate ISET (single channel) vs ISETA (all) — run with the GUI CLOSED."""
import time
from koi_gui import KoiLink, autodetect_port, TOTAL_CH

link = KoiLink(autodetect_port(), 115200)
# absorb any boot banner
t0 = time.time()
while time.time() - t0 < 2.0:
    if not link.ser.readline():
        break
link.ser.reset_input_buffer()
print("IDN:", link.command("*IDN?"))
print("XTR 255:", link.command("XTR 255"))      # ensure front-ends on

def board0():
    v = link.measure_all() or []
    return [round(x*1e3, 2) for x in v[:8]]      # board-0 channels in mV

# zero everything first
link.command("ISETA " + " ".join("0" for _ in range(TOTAL_CH)))
time.sleep(0.4)
print("\nbaseline (all 0 mA), board0 mV:", board0())

print("\n--- TEST 1: single ISET 0 2.0 ---")
print("reply:", link.command("ISET 0 2.0"))
time.sleep(0.5)
print("board0 mV:", board0(), "  <- g0 should change")

print("\n--- TEST 2: single ISET 3 2.0 (g0 back to 0) ---")
link.command("ISET 0 0.0")
print("reply:", link.command("ISET 3 2.0"))
time.sleep(0.5)
print("board0 mV:", board0(), "  <- g3 should rail, g0 back ~0")

print("\n--- TEST 3: ISET under continuous MEASA polling (GUI pattern) ---")
link.command("ISET 3 0.0")
time.sleep(0.3)
for k in range(6):
    link.measure_all()                            # poll like the GUI
    if k == 2:
        print("  injecting ISET 5 2.0 mid-poll:", link.command("ISET 5 2.0"))
time.sleep(0.5)
print("board0 mV:", board0(), "  <- g5 should rail")

# clean up
link.command("ISETA " + " ".join("0" for _ in range(TOTAL_CH)))
link.close()
print("\ndone (all channels set back to 0).")
