# Motherboard power tree — design spec

Reworked input stage: protected barrel-jack entry, 5 V buck, 3.3 V LDO, and a hardware
interlock that makes the Pico a subsystem of the board rather than an independent peer.

Implement this **in KiCad**, not by editing `motherboard.kicad_sch` by hand.

## Architectural rule

**The barrel jack is the only power source, and the Pico cannot run without it.**

The Pico is a communications peripheral. It never sources power into the board, the board never
back-powers it, and — the part that makes everything else simple — the two can never be in
different power states. That last property is enforced in hardware by the `3V3_EN` interlock
below, not in firmware.

## What exists today

Partially reworked (uncommitted, 2026-08-08). PS1 is gone; the two-stage tree is drawn but the
protection chain in front of it is not. Netlist state:

```
J12 pin1 ──► VIN_RAW ──► U8 (L7805) pin1        U8 pin3 ──► +5V ──┬──► D1 (SS34) ──► VSYS (A1 pin39)
                                                                  ├──► U4 (LP5912-3.3) IN + EN
+12V ──► J1–J8 pin A30 ... and NOTHING ELSE     U4 OUT ──► +3.3V ──► U1/U3/U5, U2, J1–J8 pin A2
                                                U4 PG ──► R6 100k ──► +3.3V, and A1 pin11 (GPIO8)
3V3_EN (A1 pin37) ──┬── R7 100k ── GND
                    └── R8 100k ── A1 pin36 (Pico's own 3V3 output)
```

### Three defects to fix in the same pass

1. **`+12 V` has no source.** It reaches only `J1..J8.A30`. The jack lands on `VIN_RAW`, which
   goes to U8 pin 1 and nowhere else, so every daughterboard's +12 V rail is currently floating.
   The protection chain below is what closes this gap — its output *is* `+12V`.
2. **The `3V3_EN` interlock is inert.** R8 returns to the Pico's *own* 3V3 (pin 36), not the
   board rail, and both resistors are 100 kΩ. With the module's internal 100 kΩ pull-up to VSYS,
   3V3_EN sits at VSYS × (100k‖100k)/(100k + 100k‖100k) ≈ 1.6 V at startup — above the RT6150
   threshold — so it enables on USB alone. It is a bootstrap divider off the regulator's own
   output, not an interlock. Fix: **R7 = 10 kΩ to GND, R8 = 4.7 kΩ to the board +3.3 V rail
   (U4 pin 1)**, and leave A1 pin 36 unconnected (see "Rejected alternatives").
3. **Regulator caps are below datasheet minimum.** The only capacitors are six 0.1 µF (C1–C6).
   LP5912 wants ≥ 1 µF in *and* ≥ 1 µF out for stability; the K7805 wants 10 µF in / 22 µF out.
   Also: three 74HC138s, an SN74LV595, a TMUX1208 and eight edge connectors share those six caps.

Still outstanding from the sections below: no fuse, no TVS, no reverse-polarity FET, no bulk
capacitance, no +12 V rail sense, no `ADC_EN`/`DAC_EN` pull-ups, and PRSNT wired on only one
socket (`J1.B31` on net `PRSNT`; `J3.B31` incorrectly shorted to GND; J2, J4–J8 unconnected).

## Target topology

```
             F1           D2          Q1 (P-FET)
J12 pin1 ──[PPTC]───┬───[TVS]───┬───[rev-pol]───┬──► +12V rail ────────────────► J1–J8 (A30)
 (VIN_RAW)          │           │               │
                   GND         GND     C7 100µF │
                                                │
                                                └──► U8 K7805-500R3 ──┬──[D1 SS34]──► A1 VSYS (39)
                                                     12→5V, 500 mA    │
                                                     C8 10µ │ C9 22µ  │
                                                                      └──► U4 LP5912-3.3 ──┬──► +3.3V
                                                                          C10 1µ │ C11 10µ │  ('138 ×3,
                                                                                           │   '595, TMUX,
                                                                     R8  4k7 ──────────────┘   J1–J8 A2)
                                                                          │
                                                     A1 3V3_EN (pin 37) ──┤
                                                                          │
                                                                     R7  10k
                                                                          │
                                                                         GND

A1 3V3 (pin 36): UNCONNECTED — the Pico makes its own 3.3 V from VSYS.
A1 VBUS (pin 40): UNCONNECTED — USB is data only.
```

The 5 V rail pre-regulates for the LDO and feeds VSYS. Dropping 12 V → 3.3 V linearly would
burn 8.7 V × 110 mA ≈ **0.96 W**; the switching stage cuts that to ~0.19 W. Switcher-then-LDO
also keeps switching noise off the rail feeding AD7193 AVDD.

**U8 is populated as an L7805 today**, which throws away that saving: 7 V × 210 mA ≈ **1.5 W**
in TO-220F with no heatsink (θJA ≈ 60 °C/W) is a ~90 °C rise. Replace it with the K7805-500R3
module. Its pinout is deliberately LM78xx-compatible, so **no net changes** — footprint only.

## The 3V3_EN interlock

`3V3_EN` is the RT6150's enable, pulled up to VSYS through 100 kΩ on the Pico module. Two
resistors turn "the Pico must not run without board power" into a hardware fact:

| Ref | Value | Connection |
| --- | --- | --- |
| `R7` | **10 kΩ** | 3V3_EN → GND |
| `R8` | **4.7 kΩ** | board +3.3 V (U4 pin 1) → 3V3_EN |

Both resistors are placed but wrong today (100 kΩ each, and R8 returns to the Pico's own 3V3
pin 36 rather than the board rail) — see defect 2 above. **The 10 kΩ is what makes it work:** it
has to be small against the module's internal 100 kΩ pull-up to VSYS. A 100 kΩ/100 kΩ pair leaves
3V3_EN at ~1.6 V with the board rail dead, which the RT6150 reads as "enabled".

| Board +3.3 V | 3V3_EN sits at | RP2040 |
| --- | --- | --- |
| Absent | 4.7 V × 10/110 ≈ **0.43 V** | Regulator disabled — never powers up |
| Present | 3.3 V × 10/14.7 ≈ **2.32 V** | Enabled, boots normally |

✅ **Verified (RT6150A/B datasheet DS6150A/B-06, July 2018).** EN input voltage: **logic-high
min 1.2 V, logic-low max 0.4 V**, input current 0.01 µA typ / 1 µA max. The Raspberry Pi
documentation confirms 3V3_EN is pulled to VSYS through **100 kΩ** on the module.

Against those numbers, with R7 = 10 kΩ and R8 = 4.7 kΩ:

| Condition | 3V3_EN | Result |
| --- | --- | --- |
| Board +3.3 V dead, VSYS = 4.7 V from USB | **0.146 V** | 0.25 V below the guaranteed-low threshold → off |
| Board +3.3 V up, VSYS = 4.7 V | **2.32 V** | 1.12 V above the guaranteed-high threshold → on |

Both are outside the indeterminate band. **The 10 kΩ is load-bearing** — it has to be small
against the internal 100 kΩ. At 100 k/100 k the "off" case lands at 1.57 V (enabled); even at
22 kΩ it reaches 0.6 V, inside the undefined region.

### Why this closes every case

| Jack | USB | +12 V | +3.3 V | VSYS | RP2040 | Result |
| --- | --- | --- | --- | --- | --- | --- |
| ✅ | ✅ | up | up | from either source | running | Normal operation |
| ✅ | ❌ | up | up | from board 5 V via D1 | running | Board alive, Pico alive — no dead-Pico back-feed |
| ❌ | ✅ | — | — | 4.7 V from VBUS | **held off** | IOVDD never comes up; every GPIO genuinely unpowered and high-Z |
| ❌ | ❌ | — | — | — | off | Everything down |

The third row is the one that matters. With the regulator disabled, the RP2040's IOVDD is
unpowered, so its pins are high-Z in the real sense — not "firmware left them as inputs." There
is nothing to drive the board and nothing to gate in software.

**D1 is load-bearing for row two.** Feeding VSYS from the board's 5 V is what guarantees
jack-on ⇒ Pico-on. Without it, a jack-on/USB-off system would have powered daughterboards
driving MISO into a dead Pico — the interlock alone doesn't fix that direction.

### The cost

**You cannot flash the Pico over USB alone.** Firmware updates require the jack connected.
Defensible for an instrument — nothing else works without power either — but it's a real change
in how the board is serviced. Decide deliberately rather than discovering it during bring-up.

### Power-up ordering

+12 V rises first (straight from the jack), then 5 V, then 3.3 V, and the Pico enables last.

⚠️ **An earlier version of this section claimed that ordering was automatically safe on the
daughterboard side. It is not, and the reason is U3's `~OE` wiring.** The XTR200's `OD` pin is
active-**LOW**-enable (OD HIGH = disabled, the power-on default from its internal pullup; see
`firmware/motherboard-test/CLAUDE.md` § XTR595). But `U3.13` (`~OE`) is hardwired to **GND**, so
the '595 *actively drives* `XTR_OD_1..8` the moment +3.3 V comes up — the XTR200's internal
pullup never gets a say. `~SRCLR` (`U3.10`) is hardwired to **+3.3 V**, so nothing clears the
register either. Its power-on contents are indeterminate, and **any bit that comes up as 0
enables that whole daughterboard's front ends** for the ~100–200 ms before `xtr.begin()` runs.

Severity is limited by the DAC80508**Z**'s zero-scale power-on reset — the DACs sit at 0 V, so
the enabled channels are commanded to zero current rather than midscale. It is still a state the
board should not be able to reach.

**Fix:** pull `~OE` **up** to +3.3 V through 10 kΩ and drive it from a GPIO. Outputs stay Hi-Z
until firmware has shifted and latched a known 0xFF, and the XTR200 pullups hold OD high
throughout. Note this costs a GPIO the budget does not currently have — see "GPIO budget" below.

## Board-side defaults

Even with the interlock, the Pico takes ~100–200 ms to boot and configure its GPIOs after the
rails are up. During that window `ADC_EN` and `DAC_EN` — the '138s' active-low `~E0` inputs —
float.

| Signal | Pull | Status |
| --- | --- | --- |
| `ADC_EN`, `DAC_EN` | 10 kΩ up to +3.3 V | **Optional.** Not fitted; the board works without them. |

⚠️ **An earlier version of this section called a floating enable "harmful" because it can assert
a chip select into powered daughterboards. That overstates it.** A chip select asserted with no
`SCK` activity does nothing — SPI slaves act only on clock edges, and the Pico isn't driving the
clock during the window in question either. No transaction can occur, so nothing is corrupted.

What remains is the ordinary objection to a floating CMOS input: `~E0` sitting near mid-rail
turns both input transistors partly on, raising supply current and risking oscillation, for
~100–200 ms per power cycle. Two resistors if you want them; the bench result — eight
daughterboards, working — is real evidence that this window is benign in practice. Not a defect.

## Rail sensing

Only **+12 V** needs sensing. The +3.3 V sense originally specced here is redundant: with the
`3V3_EN` interlock, if the Pico is executing code at all then +3.3 V is necessarily up. Dropping
it frees the pin that makes PRSNT detection fit exactly (below).

| Sense | Divider | At nominal | Pin |
| --- | --- | --- | --- |
| +12 V | 100 kΩ / 22 kΩ → 0.180 | 2.16 V | `GPIO26` / ADC0 (pin 31) |

100 nF to GND at the ADC pin, plus a BAT54-class Schottky clamp to the Pico's 3V3 so a +12 V
fault can't drive the pin above its rail. The ratio sits near mid-scale at nominal and stays
under 3.3 V even at +15 V input.

What firmware should do with it:

- Report the rail in the identify/status response.
- Refuse `ISET`/enable commands when +12 V is out of range, with a distinct error.
- Flag a sagging +12 V under full 64-channel load. At ~640 mA an undersized adapter will droop,
  and without rail sensing that surfaces as an unexplained *accuracy* problem rather than an
  obvious power problem.

## PRSNT — daughterboard seating detection

### The loop already exists; the motherboard discards it

Each daughterboard shorts `J1.A1` to `J1.B31` on its `PRSNT` net. Those are **diagonally
opposite corners** of the edge connector — the PCIe arrangement, chosen so the loop closes only
when the card is fully *and squarely* seated. A cocked board reads as absent, which is exactly
what you want.

The motherboard currently ties **both** ends to GND on all eight sockets, shorting the loop to
ground at both ends. It does nothing today.

### The change

Keep `A1` on GND; move `B31` on each socket to a Pico GPIO with the RP2040's **internal pull-up**
enabled.

| State | B31 | GPIO reads |
| --- | --- | --- |
| Fully seated | shorted to A1 (GND) by the daughterboard | **LOW** |
| Absent or partially inserted | open | **HIGH** (internal pull-up) |

**Zero new components** — no external resistors, mux, or shift register. Eight net changes on the
motherboard and nothing else. **The daughterboards need no change at all**, which matters given
eight are already populated.

### Pin assignment

Nine GPIOs are free: `GPIO0, 1, 8, 14, 15, 19, 26, 27, 28`. With the +3.3 V sense dropped, the
budget is exactly nine for nine:

| Function | Pin | Pico pad |
| --- | --- | --- |
| +12 V sense | `GPIO26` / ADC0 | 31 |
| PRSNT J1–J8 | `GPIO0, 1, 8, 14, 15, 19, 27, 28` | 1, 2, 11, 19, 20, 25, 32, 34 |

All nine are ordinary bank-0 GPIOs with software-selectable internal pull-ups (~50–80 kΩ), and
none carries a conflicting function in this design — the SPI0 (`GPIO2/3/4`) and SPI1
(`GPIO10/11/12`) pinmuxes are untouched. `GPIO26/27/28` are ADC-capable but work as plain digital
inputs; only `GPIO26` needs its analog function.

⚠️ **Do not rely on the pad reset state.** RP2040 pads come out of reset with the input buffer
disabled and a pull-*down* selected, so an unconfigured PRSNT pin reads as "seated." Firmware must
explicitly enable the input buffer and the pull-up before the first read.

### What it buys

Same class of protection as the `3V3_EN` interlock, one level down: **don't drive signals into a
board that isn't properly mated.** Gate SPI and the decoder enables for a slot on its PRSNT bit,
so a half-inserted board never sees clock or chip-select while its power fingers are still making
intermittent contact. That is the real hot-plug hazard — more so than ESD.

It also replaces the current SPI-probe detection with something instant and hardware-true: no
need to talk to a board to find out whether it's there.

Debounce in firmware — require the bit stable for ~10 ms before acting. Contact bounce on
insertion is real. If you ever want more noise margin than the internal pull-up gives across a
connector, add external 10 kΩ; not needed for a static signal.

### GPIO budget — this no longer closes

The nine-for-nine fit above assumed nothing else needed a pin. The `~OE` fix in "Power-up
ordering" needs one, so the budget is **ten wanted, nine available**. Options, best first:

1. **Move PRSNT onto a motherboard 74HC165, inserted at the head of the existing CP/PL chain.**
   The daughterboards already implement a serial chain: `GPIO16` (`CP`) broadcasts to every
   `A8`, `GPIO18` (`PL`) to every `A10`, each board's `A11` feeds the next board's `A9`, and the
   tail `J8.A11` reads back on `GPIO17`. The head, `J1.A9`, is currently tied to **GND** — that
   is a free insertion point. A '165 reading the eight `B31` pins and driving `J1.A9` appends its
   byte to the stream you already shift. **Cost: one IC, zero GPIOs, no daughterboard change**,
   and it frees all eight PRSNT pins, of which one goes to `~OE`.
   This is the option §"Pins deliberately not moved" rejected — the `~OE` requirement changes
   the arithmetic that made it rejectable, and it hands back a debug UART on `GPIO0/1` as well.
2. Drop the +12 V sense and give that pin to `~OE`. Cheapest, but loses the rail diagnostic that
   catches a sagging adapter under 64-channel load.
3. Wire PRSNT on seven slots. Don't — an asymmetric slot is a permanent trap.

### Pins deliberately not moved

Assigning all nine free GPIOs leaves **no pin for a debug UART** (`GPIO0/1` are the conventional
UART0 pair). That matters slightly more now that the interlock removed USB-only flashing. The
alternative is a 74HC165 on the motherboard sharing the existing `CP`/`PL` chain — one IC, frees
eight GPIOs. Rejected here because direct GPIO costs nothing and USB serial remains available
whenever the jack is connected; revisit only if bring-up needs a console independent of USB.

The eight PRSNT pins are **not contiguous**, so firmware assembles the byte from a
`gpio_get_all()` mask rather than a single shift. Making them contiguous would mean re-routing
established, working SPI and decoder traces — not worth it for a few lines of code.

## Component selection

Currents come from the padne per-channel model. **Measure them on the 8-board system before
committing** — the XTR200 quiescent figure is modelled, not measured, and it sizes the tree.

| Rail | Load | Worst case | Design for |
| --- | --- | --- | --- |
| +12 V | 8 boards × 8 ch × (3 mA quiescent + 6 mA pass + 0.6 mA SET) ≈ 80 mA/board | **~640 mA** | 1 A |
| +3.3 V | 8 boards × ~11 mA + motherboard logic ~20 mA (Pico is *not* on this rail) | **~110 mA** | 400 mA |
| 5 V | LDO input ~110 mA + Pico via VSYS ~100 mA | **~210 mA** | 1 A |

### F1 — fuse
PPTC, **1.5 A hold / 3 A trip, 30 V**, 1812. Auto-recovery matters for a shared lab instrument.

Total +12 V draw is the daughterboards (~640 mA) *plus* the K7805's input current (~100 mA,
below) ≈ **740 mA**. A 1.1 A-hold part derates to ~0.85–0.9 A hold at 40 °C inside an enclosure,
which is uncomfortably close to 740 mA — hence 1.5 A. ~0.1 Ω at 740 mA is 74 mV — negligible
on 12 V. A 2 A slow-blow cartridge is the alternative if you prefer a hard failure.

### D2 — transient suppressor
Unidirectional, cathode to +12 V, anode to GND, **between F1 and Q1** so a surge *or* a
reverse-polarity event conducts and blows the fuse — a crowbar backing up the FET.

**SMBJ13A** (13 V standoff, 21.5 V clamp, 600 W) for a regulated 12 V adapter. **Measure your
adapter's no-load output first** — unregulated bricks idle well above 12 V; above 13 V you need
SMBJ15A (24.4 V clamp). Either clamp sits far below the K7805's 32 V input limit, so the
converter survives whatever the TVS lets through.

**Unidirectional, not bidirectional.** The rail is DC and single-polarity, so a bidirectional
part's reverse blocking buys nothing — and the forward direction is the whole point. A
unidirectional TVS conducts like an ordinary diode at ~0.9 V when the input is reversed, which
crowbars the reversed adapter and trips F1. That is the **backup for Q1**: if the FET is fitted
backwards, fails short, or the wrong part gets stuffed, the TVS and fuse still catch the event.
SMBJ13**CA** (bidirectional) blocks to −13 V instead and makes Q1 a single point of failure, for
more money and more capacitance.

Consequence to size for: on reverse polarity the TVS dead-shorts the supply through F1, and a
PPTC takes ~0.1–1 s to trip. SMB handles that (IFSM ≈ 100 A / 8.3 ms), but only because the fuse
is **upstream** of the TVS. Wiring the TVS ahead of the fuse removes the only thing that ends the
event — see "Layout notes".

⚠️ **Symbol polarity.** `Device:D_TVS` is the *bidirectional* symbol — its pins are `A1`/`A2`,
so it carries no polarity, while the `D_SMB` land pattern on the board is polarized. Use a
polarized symbol (e.g. `Device:D_Zener`, pin 1 = K, pin 2 = A) valued `SMBJ13A`, with **pin 1
(cathode) on the +12 V side and pin 2 (anode) on GND**. Getting this backwards is not a subtle
failure: a reversed TVS forward-conducts at ~0.9 V and shorts the rail on first power-up.

### Q1 — reverse-polarity protection
P-channel MOSFET, high side. **Drain to the supply, source to the load.**

Not optional with this converter: the K78xx-500R3 datasheet lists "Reverse Polarity at Input:
**Avoid / Not protected**". A reversed barrel jack destroys the module directly.

- *Correct polarity*: the body diode (drain→source) conducts first, the load rises, so
  Vgs = 0 − V_load goes strongly negative and the FET turns on hard, shorting out its own diode.
- *Reversed*: the body diode is reverse-biased and Vgs ≈ 0 — nothing conducts.

Source-to-supply is the common mistake, and it fails *silently*: the body diode forward-biases
on reverse polarity and passes current anyway, so the circuit tests fine and doesn't protect.

Selected: **Q1 = AO3407A** (LCSC `C15155`) — −30 V Vds, −4.3 A, 48 mΩ, SOT-23, **Vgs ±20 V**,
~150 k in stock at ~$0.10 (clone versions `C181093`/`C727158` at ~$0.016). At 740 mA / 48 mΩ
that's 36 mV and 26 mW, against ~0.33 W for a series Schottky.

| Ref | Part | Role |
| --- | --- | --- |
| `R3` | 100 kΩ | Gate → GND. Turns Q1 on when polarity is correct. |
| `D3` | *optional* BZT52C15 (15 V zener) | Gate–source clamp, cathode to source. Only for the TVS-clamp corner below. |

**Why not AO3401A** (the obvious, cheapest choice, and what earlier drafts of this spec
selected): it is rated **Vgs ±12 V**. With the gate pulled to GND on a 12 V rail, Vgs sits at
exactly −12 V *continuously* — at the absolute maximum, with zero margin, and over it on any
adapter that idles above 12.0 V. Making that safe needs a **10 V** gate zener, which then
conducts **continuously in normal operation** ((12 − 10)/100 kΩ ≈ 20 µA), making the zener
load-bearing rather than protective. AO3407A is the same package and pinout with 8 V of Vgs
margin, so the whole problem disappears for ~$0.08.

**The remaining corner** is that Vgs follows the rail during a TVS event. 21.5 V is the SMBJ13A
clamp at its full 600 W / ~100 A rating; a bench adapter's surge energy clamps nearer 14–17 V,
inside the ±20 V rating. If you want it covered unconditionally, fit `D3` = **BZT52C15**: at
15 V it sits above the 12 V rail, so unlike the 10 V part it never conducts in normal operation
and only clamps on the transient.

**Why not N-channel** (low-side, in the ground return): two reasons, the first fatal here.

1. **The Pico's USB connector bonds board GND to PC GND**, giving ground a second path that
   bypasses the FET entirely. The protection does nothing whenever USB is plugged in — and on a
   reversed jack the return current routes through the USB cable into the host.
2. It inserts Rds(on) × 740 mA of offset between supply GND and board GND, in the return path
   the AD7193 measures against.

It also has the identical Vgs problem (gate to +12 V, source at supply ground → Vgs = +12 V), so
it buys nothing even ignoring the above.

### Bulk — electrolytic on +12 V

**220 µF / 35 V** aluminium electrolytic on the +12 V rail after Q1. Two jobs:

1. **Load step.** 64 channels enabling at once is a ~600 mA step, supplied from board capacitance
   until the adapter's loop and the cable inductance catch up (~50–200 µs). ΔV ≈ I·Δt/C: 220 µF
   gives ~0.27 V, 100 µF ~0.6 V, and a lone 1 µF ceramic lets the rail collapse. This is a
   measurement instrument — a rail that sags under full load shows up as an accuracy mystery.
2. **Hot-plug damping.** Plugging the jack rings a metre of cable inductance against the board
   capacitance. Pure ceramic has no ESR to damp it and can transiently reach ~2× the supply; the
   TVS would clamp that, but then it eats energy on every plug-in. **Keep real ESR — do not
   substitute a large MLCC.** Critical damping wants R ≈ 2√(L/C) ≈ 135 mΩ at 1 µH / 220 µF; the
   part below is 80 mΩ, which with cable resistance gives Q ≲ 1 and essentially no overshoot.

**Use 35 V, not 25 V.** The TVS clamps as high as 21.5 V, and standard ~80 % electrolytic
derating puts a 25 V part at 20 V — under the clamp.

This does not replace the K7805's own input capacitor; that is a separate Table 1 requirement.
680 µF is the module's max capacitive load on its *output*, so it doesn't constrain this.

### Capacitor part numbers

Verified on LCSC. Ceramics are specced **above** the datasheet's nominal voltage on purpose:
DC bias derating costs an X5R most of its capacitance near its rating, so a 10 V-rated 22 µF at
5 V is really ~11 µF, while the 25 V part holds most of its value.

**Standardised on 0805** for every ceramic, with one deliberate exception (`C8`, below).

| Ref | Role | Value / package | Brand & MPN | LCSC |
| --- | --- | --- | --- | --- |
| `C7` | +12 V bulk | 220 µF 35 V, Ø8×10 mm can, `Capacitor_SMD:CP_Elec_8x10` | Nichicon `UWT1V221MNL1GS` | **`C125981`** |
| `C8` | K7805 input (Table 1) | 10 µF 50 V X7R **1210** | Murata `GRM32ER71H106KA12L` | **`C77102`** |
| `C9` | K7805 output (Table 1) | 22 µF 25 V X5R 0805 | Samsung `CL21A226MAQNNNE` | **`C45783`** |
| `C6`, `C10` | LDO out / LDO in | 10 µF 25 V X5R 0805 | Murata `GRM21BR61E106KA73L` | **`C84416`** |
| `C1`–`C4`, … | Per-IC decoupling | 100 nF 50 V X7R 0805 | Samsung `CL21B104KBCNNNC` | **`C1711`** |

**Why 0603 was rejected:** 0603 tops out near 10 µF at 6.3–10 V. Neither 22 µF/25 V nor
10 µF/50 V exists in that case size, so a single-size-0603 board is not buildable.

**Why `C8` stays 1210.** DC bias derating worsens as the case shrinks — same voltage rating in a
smaller package means thinner dielectric — and the 0805 50 V part is X5R where the 1210 is X7R,
the more bias- and temperature-stable dielectric. `C8` sits at 12 V, where an 0805 would keep
roughly half its 10 µF. Table 1's 10 µF is a requirement of the module's input switching loop,
not decoupling by habit, so undershooting it is a real compromise. The 0805 option
(`GRM21BR61H106KE43L`, `C440198`) is also 3× the price for less delivered capacitance — $0.325
vs $0.114. If strict single-size matters more, fit **two** `C440198` in parallel instead.

Ceramics are specced **above** the datasheet's nominal voltage on purpose: an X5R loses most of
its capacitance near its rating, so a 10 V-rated 22 µF at 5 V is really ~11 µF while the 25 V
part holds most of its value.

A THT bulk alternative exists — Nichicon `UHE1V221MPD6`, **`C251010`**, D10×L12.5 mm, 5 mm pitch,
80 mΩ, 865 mA, 7000 h @105 °C — but it won't be machine-placed if the board goes through JLCPCB
assembly.

### U8 — 12 V → 5 V converter
Selected: **K7805-500R3** (EVISUN, LCSC `C19188491`, ~$1.07, SIP-3) — a drop-in switching
replacement for the LM78xx, which is exactly how it is being used here.

| Spec | Value |
| --- | --- |
| Input | 6.5–32 V (EVISUN spec; Mornsun original 6.5–36 V) |
| Output | 5 V ±2 %, **500 mA** |
| Efficiency | ~89 % at 12 V in, full load |
| Ripple & noise | 20 mVp-p typ, 75 mVp-p max (20 MHz BW) |
| Switching frequency | 550–850 kHz (EVISUN datasheet quotes 520 kHz) |
| Package | SIP-3, 11.60 × 7.55 × 10.16 mm, 2.54 mm pitch |
| Pinout | **1 = Vin, 2 = GND, 3 = +Vout** — identical to LM78xx |

At the ~210 mA 5 V load that's ~1.05 W out / ~1.18 W in ≈ **98 mA from +12 V**, and 2.4×
current headroom. Derating is flat to 71 °C ambient; no heatsink.

**Pin compatibility is the whole point of picking it:** U8's existing nets (`VIN_RAW` → pin 1,
GND → pin 2, `+5V` → pin 3) are already correct. The only schematic change is the symbol/value,
and the only layout change is the footprint.

**Symbol and footprint — use the TRACO TSR-1 pair from stock KiCad libraries.** No custom
drawing and no SnapMagic import needed:

| | Use |
| --- | --- |
| Symbol | `Converter_DCDC:TSR_1-2450` — pin 1 `Vin` (power_in), 2 `GND`, 3 `Vout` (power_out) |
| Footprint | `Converter_DCDC:Converter_DCDC_TRACO_TSR-1_THT` (the symbol's default, so it fills in automatically) |

Override the `Value` field to `K7805-500R3`. Verified against the Mornsun datasheet's recommended
layout:

- **Pads**: 3 × through-hole at 2.54 mm pitch, **drill Ø1.00 mm**, pad 1.5 × 2.5 mm. The
  datasheet specifies Ø1.00 mm holes on a 2.54 mm grid — exact match.
- **Pinout**: TRACO TSR-1 is 1 = Vin, 2 = GND, 3 = Vout, identical to the K7805 (both are LM78xx
  drop-ins).
- **Outline**: the footprint's fab layer is 11.94 × 7.85 mm against the K7805's 11.60 × 7.55 mm —
  0.3 mm generous on each axis, so the silkscreen is slightly larger than the part. Cosmetic.

U8's current `Package_TO_SOT_THT:TO-220F-3_Vertical` is wrong on both counts: TO-220 is 10.16 mm
wide with a mounting tab, and its holes are sized for 0.5 × 1.0 mm blades rather than the K7805's
0.50 mm pins.

Required external capacitors (datasheet Table 1, ceramic, as close to the pins as possible):

| Ref | Value | Node |
| --- | --- | --- |
| `C8` | **10 µF / 50 V** | pin 1 (Vin) → GND |
| `C9` | **22 µF / 10 V** | pin 3 (Vout) → GND |

These are *required*, not decoupling-by-habit — the module has no internal input filter beyond
what Table 1 assumes. Add 100 nF alongside each if you want HF bypass.

If 5 V ripple ever turns out to matter, the datasheet's note 5 offers an LC output filter
(10–47 µH + 22 µF). It shouldn't: the LP5912 downstream has ~40 dB PSRR at 500 kHz, and a
20 mVp-p input becomes sub-mV on +3.3 V.

### D1 — 5 V → VSYS
Schottky, 40 V / 1 A, low Vf (SS34, already placed, SMA). Anode to the +5 V rail, cathode to
VSYS. VSYS lands ~4.7 V — well inside the Pico's 1.8–5.5 V range — and ORs cleanly against the
module's internal VBUS diode. Note the K7805 is fixed-output, so unlike an adjustable buck you
cannot trim it to 5.3 V to land VSYS at exactly 5 V; 4.7 V is fine.

The K78xx datasheet's "cannot be used with outputs in parallel" warning does **not** apply here —
D1 is a diode-OR into VSYS against the Pico's internal VBUS diode, not two converters sharing a
node.

### U4 — 5 V → 3.3 V LDO
Placed: **LP5912-3.3DRV** (WSON-6, thermal pad) — 500 mA, low noise, high PSRR, with an enable
pin (currently tied to +5 V) and a power-good output (already routed to `GPIO8` via R6).

Feeds AD7193 AVDD and DAC80508 on every daughterboard, so PSRR is the spec that matters; the
LP5912 is the quiet end of the range and is a better choice than the AP2112K originally specced
here. Its thermal pad also solves the dissipation question below. 500 mA against a ~115 mA load
is >4× headroom.

Its input range is **2.2–5.5 V** — it cannot be fed from 12 V, which is precisely why U8 exists
rather than a single linear stage.

**Required caps (missing today — only 0.1 µF is fitted):**

| Ref | Value | Node |
| --- | --- | --- |
| `C10` | **1 µF** ceramic | IN → GND, at the pin |
| `C11` | **10 µF** ceramic | OUT → GND (datasheet minimum is 1 µF; 10 µF for the connector bank) |

**Thermals:** 1.7 V × 110 mA ≈ **0.19 W**. In WSON-6 with the exposed pad soldered to a
via-stitched pour this is a non-issue; keep the thermal vias in the footprint.

### Decoupling
100 nF per IC power pin — three '138s, the '595, the TMUX1208 — plus 10 µF near the connector
bank on **both** +3.3 V and +12 V, and the regulator caps above. Today there are six 0.1 µF
total (C1–C6) covering four ICs, both regulators and eight edge connectors.

## Layout notes

- `C8`/`C9` hard against U8's pins 1 and 3 — the datasheet is explicit about this, and the
  module's switching loop closes through them.
- Protection chain physically ordered jack → F1 → TVS → Q1, short and wide. A TVS on a long
  trace clamps its own inductance instead of the transient.
- Keep `R7`/`R8` close to the Pico's pin 37; it's a high-impedance node and shouldn't run
  alongside the SPI clocks.
- U8 is a shielded module, but keep its ground return tight and don't run the +12 V sense divider
  or `MISO_0/1` under it.
- Copper pour under the LDO's thermal pad, stitched to the plane with a via array.
- Keep +12 V and 5 V return currents off the analog reference path to the edge connectors.

## Rejected alternatives

- **Tying Pico 3V3 (pin 36) to the +3.3 V rail.** Parallels the RT6150 against the LDO with no
  droop sharing — whichever regulates higher carries everything until it current-limits.
- **Disabling the regulator and feeding 3V3 externally.** Documented by Raspberry Pi, but
  conditional on VSYS being unpowered, which USB makes impossible to guarantee.
- **Firmware GPIO gating alone.** Leaves protection dependent on correct firmware, and permits a
  half-powered board state that shouldn't be reachable at all.
- **`74LVC541`/`125` Ioff buffers at the boundary.** Correct and conventional, but ~3 ICs plus
  17 pull resistors to solve a problem the interlock removes for two resistors. Reconsider only
  if USB-only firmware flashing turns out to matter.

## Schematic hygiene while you're in there

`#PWR050` and `#PWR036` have `Value` = `+12V` but use the `power:+3.3V` symbol graphic. KiCad
takes the net name from `Value`, so the netlist is correct and the board is fine — but the
schematic reads wrong. Swap them for the right symbol.

## Build checklist

### Sourcing — every part in the schematic

Verified on LCSC. Rows marked *(BOM)* are already in `master_bom_lcsc.csv`.

| Ref | Part | LCSC | Footprint |
| --- | --- | --- | --- |
| `A1` | Raspberry Pi Pico *(BOM)* | `C7203002` | — |
| `U1`, `U5` | **SN74HC138PWR** (TI) — replaces SN74LVC138APWR, see below | `C157527` | `Package_SO:TSSOP-16_4.4x5mm_P0.65mm` |
| `U2` | TMUX1208PWR | **unconfirmed — see note** | TSSOP-16 |
| `U3` | SN74LV595APWR *(BOM)* | `C116847` | TSSOP-16 |
| `U4` | LP5912-3.3DRVR (TI) | `C524780` | WSON-6 |
| `U8` | K7805-500R3 (EVISUN) | `C19188491` | `Converter_DCDC:Converter_DCDC_TRACO_TSR-1_THT` |
| `Q1` | AO3407A (AOS, Vgs ±20 V) | `C15155` | SOT-23 |
| `D1` | SS34 (MDD) | `C8678` | `Diode_SMD:D_SMA` |
| `D2` | SMBJ13A (ST, `SMBJ13A-TR`) | `C133675` | `Diode_SMD:D_SMB` |
| `F1` | 1812L150/24MR (Littelfuse) | `C142805` | 1812 |
| `R1`, `R6` | 100 kΩ 0805 1% (Yageo `RC0805FR-07100KL`) | `C96346` | `Resistor_SMD:R_0805_2012Metric` |
| `J1`–`J8` | Samtec PCIE-064-02-F-D-TH *(BOM)* | `C4597619` | — |
| `J9`–`J11` | Molex 54132-5033 *(BOM)* | NOT STOCKED | — |
| `J12` | DC005-T20 barrel jack *(BOM)* | `C111567` | — |

Capacitors are in "Capacitor part numbers" above.

**`U1`/`U5`: use `SN74HC138PWR` (`C157527`), not `SN74LVC138APWR` (`C485077`).** The LVC part is
**$0.755 at 1+ with 669 in stock**; the HC part is **$0.095 with ~50 000 in stock** — 8× cheaper
and not a sole-source risk. Nothing in this design needs LVC:

- The decoders are static during a transfer. Firmware sets the address on `GPIO20/21/22`, asserts
  `ADC_EN`/`DAC_EN`, *then* clocks SPI — the decoder never switches at bus speed, so HC's ~50 ns
  worst-case t_pd at 3.3 V is setup time that's already covered by the intervening instructions.
- Each `Y` output drives one daughterboard CS input through the edge connector — microamps plus
  trace capacitance, against HC's ~5 mA drive.
- HC's 2–6 V range covers the 3.3 V rail, and V_IH = 0.7 × V_CC = 2.31 V against the RP2040's
  3.3 V CMOS output.

LVC would only earn its price if the decoders had to switch at SPI speed or run below 2 V.
Nexperia `74HC138PW,118` (`C47455`, $0.082) is an equally good second source.

⚠️ **The footprint has to change regardless.** `U1`/`U5` currently carry
`Package_SO:SSOP-16_5.3x6.2mm_P0.65mm` (TI's DB package) while `C485077` is the **PW/TSSOP-16**
part — the BOM and the board already disagree. Moving to `TSSOP-16_4.4x5mm_P0.65mm` fixes that
*and* makes all four logic ICs (`U1`, `U2`, `U3`, `U5`) share one footprint.

The `master_bom_lcsc.csv` row also reads `SN74LV138 (74HC138)` mapped to `SN74LVC138APWR` — three
different logic families in one line. Update it with the part above.

**`F1` is 24 V, not the 30 V originally specced.** LCSC stocks the 1812L150 series only up to
`/24MR`. That is sufficient: a fuse's voltage rating is what it must interrupt, and the rail is
12 V with a 21.5 V worst-case TVS clamp — both under 24 V. Hold 1.5 A / trip 3.0 A as specced.

**`U2` (TMUX1208PWR) could not be confirmed on LCSC.** It's stocked at Arrow/DigiKey/Mouser
(~$0.27). Check LCSC directly before assuming it can ship with the rest of the order — this
project already carries non-LCSC lines (XTR200, Molex `54132-5033`).

**Pick branded parts for `D2` and `F1` deliberately.** LCSC lists SMBJ13A from a dozen no-name
houses at a third of the price; the TVS is the part standing between a surge and everything
downstream, and clamping voltage is exactly the spec generics are loosest about.

### Still to add

| Ref | Part | LCSC | Where |
| --- | --- | --- | --- |
| `R4` | 100 kΩ 0805 | `C96346` | `+12V` → `V12_SENSE` |
| `R5` | 22 kΩ 0805 (Yageo `RC0805FR-0722KL`) | `C114565` | `V12_SENSE` → GND |
| `C12` | 100 nF 0805 | `C1711` | `V12_SENSE` → GND |
| `D4` | BAT54S | `C2828465` | `V12_SENSE` clamp to `+3.3V` / GND |
| `R11` | 10 kΩ 0805 (Yageo `RC0805FR-0710KL`) | `C84376` | `U3.13` (`~OE`) → `+3.3V`, plus a GPIO |
| `R9`, `R10` | 10 kΩ 0805 — *optional, see "Board-side defaults"* | `C84376` | `ADC_EN`, `DAC_EN` → `+3.3V` |
| `C13` | 10 µF 0805 | `C84416` | `+3.3V` at the J1–J8 bank |
| — | 100 nF 0805, ~4 more | `C1711` | one per remaining IC power pin |

### Changes to placed parts

| Ref | From | To |
| --- | --- | --- |
| `U8` | L7805, `TO-220F-3_Vertical` | **K7805-500R3** (`C19188491`) + TSR-1 symbol/footprint — nets unchanged |
| `C7` | `C_0603_1608Metric` | `Capacitor_SMD:CP_Elec_8x10`, and a **polarized** symbol (`Device:CP_Small`) |
| `C8` | `C_0603_1608Metric` | `Capacitor_SMD:C_1210_3225Metric` |
| `C6`, `C9`, `C10` | `C_0603_1608Metric` | `Capacitor_SMD:C_0805_2012Metric` |
| `C1`–`C4` | 0603 | 0805, for a single ceramic size |
| `R7` | 100 kΩ | **10 kΩ** (`C84376`) |
| `R8` | 100 kΩ, far end on A1 pin 36 | **4.7 kΩ** (`C60816`, Yageo `RC0805FR-074K7L`), far end on **`+3.3V` (U4 pin 1)**; leave A1 pin 36 unconnected |
| `F1`, `R1`, `R6`, `R7`, `R8` | *no footprint assigned* | 1812 / `R_0805_2012Metric` |

### Net changes

- `+12V` ← Q1 source. (Today `+12V` is orphaned on `J1..J8.A30`.)
- `A1.31` (GPIO26/ADC0) ← `V12_SENSE`.
- `J1..J8.B31` → eight GPIOs: `GPIO0, 1, 8, 14, 15, 19, 27, 28`. `GPIO8` currently carries the
  LDO's `PG` via R6 — with the `3V3_EN` interlock fixed, PG is redundant (if firmware runs, the
  rail is up), so drop R6 and reuse the pin. `J1.B31` is already on a `PRSNT` net and `J3.B31` is
  wrongly shorted to GND; both need rework.
