#!/usr/bin/env python3
"""Map firmware DAC channel -> ADC channel (board 0). Run with the GUI CLOSED."""
import time
from koi_gui import KoiLink, autodetect_port, TOTAL_CH

link = KoiLink(autodetect_port(), 115200)
time.sleep(0.3); link.ser.reset_input_buffer()
link.command("*IDN?")
link.command("XTR 255")

def zero():
    link.command("ISETA " + " ".join("0" for _ in range(TOTAL_CH))); time.sleep(0.3)

def board0_mv():
    v = link.measure_all() or [0]*8
    return [x*1e3 for x in v[:8]]

zero()
base = board0_mv()
print("baseline mV:", [round(x,1) for x in base])
print(f"\n{'DAC ch':>6} -> {'ADC ch (max Δ)':>14}   deltas(mV)")
mapping = {}
for d in range(8):
    zero()
    link.command(f"ISET {d} 2.0")
    time.sleep(0.5)
    v = board0_mv()
    deltas = [v[i] - base[i] for i in range(8)]
    a = max(range(8), key=lambda i: deltas[i])
    mapping[d] = a
    print(f"{d:>6} -> {a:>14}   " + " ".join(f"{x:7.1f}" for x in deltas))
zero()
link.close()

print("\nDAC->ADC map:", mapping)
inv = {a: d for d, a in mapping.items()}
print("ADC->DAC map:", dict(sorted(inv.items())))
print("\nbijection?", sorted(mapping.values()) == list(range(8)))
