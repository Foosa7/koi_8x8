# Motherboard power tree — design spec

Protected barrel-jack entry, 5 V buck, 3.3 V LDO, and a hardware interlock that makes the Pico a
subsystem of the board rather than an independent peer.

**Status: released to fabrication 2026-08-12.** The tree, the protection chain and the interlock
are on the board as of `397a4a4`; "What exists today" lists what is still outstanding. Input range
for this revision is **9–15 V**. Changes are no longer free — this revision is at the fab, so
anything below now describes a future spin.

Make changes **in KiCad**, not by editing `motherboard.kicad_sch` by hand.

## Architectural rule

**The barrel jack is the only power source, and the Pico cannot run without it.**

The Pico is a communications peripheral. It never sources power into the board, the board never
back-powers it, and — the part that makes everything else simple — the two can never be in
different power states. That last property is enforced in hardware by the `3V3_EN` interlock
below, not in firmware.

## What exists today

**Built as of `397a4a4` (2026-08-10).** The rework in this document has landed: the protection
chain, the two-stage tree and the interlock are all in the schematic and on the board. All three
defects this section used to list are fixed.

```
J12 ──► VIN_JACK ──► F1 (1812L150/24MR) ──┬──► D2 (TVS) ──► GND
                                          └──► Q1 (AO3407A) drain
                            Q1 source ──► +12V ──┬──► J1–J8 pin A30, J13
                                                 ├──► C5 10µ, C7 220µ/35 V
                                                 └──► U6 (K7805-500R3) Vin
                                 U6 Vout ──► +5V ──┬──► D1 (SS34) ──► VSYS (A1 pin 39)
                                                   ├──► U4 (LP5912-3.3) IN + EN
                                                   └──► C6 22µ, C8 10µ
                                 U4 OUT ──► +3.3V ──► U1/U2/U3/U5, J1–J8 pin A2, C1–C4 100n, C9 10µ
                                 U4 PG  ──► R6 100k ──► +3.3V, and A1 pin 11 (GPIO8)
      3V3_EN (A1 pin 37) ──┬── R7 10k ── GND
                           └── R8 4.7k ── +3.3V (U4 pin 1)
      +12V ──► R5 100k ──┬── 12V_SENSE ──► A1 pin 31 (GPIO26/ADC0)
                         ├── R10 22k ── GND
                         └── C10 100n ── GND (at the Pico pin)
      A1 pin 36 (3V3) and pin 40 (VBUS): unconnected.
      R1 100k: Q1 gate → GND.   D6: Q1 gate–source clamp.   R9 10k: U3 `~OE` pull-up.
      D3/D4/D5 + R2/R3/R4: rail LEDs on +12 V / +5 V / +3.3 V.
```

**Input range for this revision is 9–15 V**, set at the jack. This is wider than the 12 V nominal
the first draft assumed, and it drives the D2 and D6 selections below. Note the jack voltage *is*
the +12 V rail — it reaches `J1..J8.A30` and thence `VSP` (pin 5) of all 64 XTR200s — so it is not
a free input range: raising it raises XTR200 pass dissipation proportionally.

### Refdes drift — read this before cross-referencing

The build tables further down were written against the pre-rework schematic and use refdes that
the board assigns differently. Current mapping:

| This document originally said | On the board | Role |
| --- | --- | --- |
| `U8` | **`U6`** | K7805-500R3 buck |
| `R3` | **`R1`** | Q1 gate → GND, 100 kΩ |
| `D3` (optional gate zener) | **`D6`** | Q1 gate–source clamp — now placed, and now required |
| `C8` | **`C5`** | K7805 input, 10 µF 1210 |
| `C9` | **`C6`** | K7805 output, 22 µF 0805 |
| `C10` | **`C8`** | LDO input, 10 µF 0805 |
| `C11` | **`C9`** | LDO output, 10 µF 0805 |

`R2`/`R3`/`R4` and `D3`/`D4`/`D5` are the **rail LEDs** on the board, so the "Still to add" table's
use of `R4`, `R5` and `D4` for the sense divider and clamp is a collision.

`R5`, `R10` and `C10` are now the sense divider and its filter — note the bottom leg is **`R10`**,
not the `R13` earlier drafts of this document specced. Free refdes are `R11`+, `C11`+, `D7`+.

### Still outstanding

Verified against a fresh netlist export and the `.kicad_pcb` on **2026-08-11**. Schematic and
PCB agree on every net; no pad is left unreached by its own net's copper.

| Item | Where |
| --- | --- |
| `Q1` is drawn with the `AO3401A` symbol while valued `AO3407A` | "Schematic hygiene" |
| `D2`'s symbol carries no polarity (pins `A1`/`A2`) | "Schematic hygiene" |

**Electrically the board is complete.** Both open items are drawing hygiene, not connectivity.

Closed since the last revision of this document: the +12 V sense divider (`R5`/`R10`/`C10` →
`GPIO26`), the `U3.~OE` cutover to `GPIO19`, `D2` → `SMBJ16A`, `D6` → `BZT52C18`, the
`#PWR050`/`#PWR036` symbol swap, and `J1..J8.B31` → GND on all eight with the dangling one-node
`PRSNT` net deleted.

Decided against, deliberately — see "Board-side defaults" and "Decoupling": the `ADC_EN`/`DAC_EN`
pull-ups, the `U2` `EN` pull-down, and the 10 µF at the +3.3 V connector bank.

✅ **`production/` was regenerated 2026-08-11 20:27** from the current board — it now includes the
sense divider, the `~OE` cutover, the `D2`/`D6` changes and `J8.B31`. **This is the revision sent
to fabrication (2026-08-12).**

⚠️ **`motherboard.net` is still the 2026-08-10 18:04 export** and predates those changes. Re-export
from KiCad before using it to check connectivity; `production/netlist.ipc` is the current one.

✅ **DRC run 2026-08-12** (`kicad-cli pcb drc motherboard.kicad_pcb` — the CLI *can* open this board
now that KiCad 10.0.5 has re-saved it; only older on-disk formats defeated it). **0 errors, 0
unconnected pads**, 4 warnings, none of them manufacturing defects:

| Warning | What it is |
| --- | --- |
| `silk_overlap` ×1 | `CP` text vs `A1`'s reference designator |
| `silk_edge_clearance` ×2 | `J12` silkscreen clipped by the board edge |
| `lib_footprint_mismatch` ×1 | `U3`'s TSSOP-16 differs from `Package_SO`'s copy (local override) |

## Why this topology

The 5 V rail pre-regulates for the LDO and feeds VSYS. Dropping 12 V → 3.3 V linearly would
burn 8.7 V × 110 mA ≈ **0.96 W**; the switching stage cuts that to ~0.19 W. Switcher-then-LDO
also keeps switching noise off the rail feeding AD7193 AVDD.

The board previously carried an L7805 here, which threw that saving away: 7 V × 210 mA ≈ **1.5 W**
in TO-220F with no heatsink (θJA ≈ 60 °C/W) is a ~90 °C rise. The K7805-500R3's pinout is
deliberately LM78xx-compatible, so the swap cost **no net changes** — symbol, value and footprint
only. At 15 V in the linear option would have been worse still (9.7 V × 210 mA ≈ 2.0 W).

## The 3V3_EN interlock

`3V3_EN` is the RT6150's enable, pulled up to VSYS through 100 kΩ on the Pico module. Two
resistors turn "the Pico must not run without board power" into a hardware fact:

| Ref | Value | Connection |
| --- | --- | --- |
| `R7` | **10 kΩ** | 3V3_EN → GND |
| `R8` | **4.7 kΩ** | board +3.3 V (U4 pin 1) → 3V3_EN |

✅ **Built and verified against the netlist.** Both resistors carry these values and `R8` returns
to the board rail. **The 10 kΩ is what makes it work:** it has to be small against the module's
internal 100 kΩ pull-up to VSYS. A 100 kΩ/100 kΩ pair — what the board carried before the
rework — leaves 3V3_EN at ~1.6 V with the board rail dead, which the RT6150 reads as "enabled".

✅ **Thresholds verified (RT6150A/B datasheet DS6150A/B-06, July 2018).** EN input voltage:
**logic-high min 1.2 V, logic-low max 0.4 V**, input current 0.01 µA typ / 1 µA max. The Raspberry
Pi documentation confirms 3V3_EN is pulled to VSYS through **100 kΩ** on the module.

| Condition | 3V3_EN | Result |
| --- | --- | --- |
| Board +3.3 V dead, VSYS = 4.7 V from USB | **0.146 V** | 0.25 V below the guaranteed-low threshold → off |
| Board +3.3 V up, VSYS = 4.7 V | **2.32 V** | 1.12 V above the guaranteed-high threshold → on |

Both are outside the indeterminate band. **The 10 kΩ is load-bearing** — it has to be small
against the internal 100 kΩ. At 100 k/100 k the "off" case lands at 1.57 V (enabled); even at
22 kΩ it reaches 0.6 V, inside the undefined region.

⚠️ **Where the 0.146 V comes from, because it matters.** An earlier version of this section
quoted **0.43 V** for the same case — 4.7 V × 10/110, i.e. the internal pull-up against `R7`
alone. That figure ignores `R8`, and it sits *above* the 0.4 V guaranteed-low threshold. The
0.146 V figure is the correct one and includes `R8` pulling into a dead +3.3 V rail
(10 k ‖ 4.7 k = 3.2 k). It is valid only because that rail really does sit near 0 V when
unpowered — `D5` + `R4` (680 Ω) to GND plus five CMOS loads hold it down. If the rail ever
floated instead, you would be back at 0.43 V and marginally out of spec. It doesn't, so the
design is sound as built; but do not "simplify" `R8` away or move it to a lighter rail.

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
throughout.

**Status: half done.** `R9` (10 kΩ, `C84376`) is placed next to U3 in the schematic, but
`U3.13` is still tied to GND — the pull-up cannot do anything until that connection is cut and
the pin routed to a GPIO. **Assigned pin: `GPIO19` (A1 pad 25).** With PRSNT dropped (below) the
GPIO cost is no longer a problem.

Firmware follow-up in `firmware/motherboard-test/`: hold `GPIO19` high — or simply leave it an
input and let `R9` do the work — until after the first `0xFF` shift and latch, then drive it low.

This also neutralises the other floating-input concern in the same window. `SCK_0`, `MOSI_0` and
`XTR_RCLK` float during boot too and could clock garbage into the '595, but with `~OE` high the
outputs are Hi-Z regardless of the register contents, so no pull resistors are needed on the SPI
lines.

## Board-side defaults

Even with the interlock, the Pico takes ~100–200 ms to boot and configure its GPIOs after the
rails are up. During that window `ADC_EN` and `DAC_EN` — the '138s' active-low `~E0` inputs —
float.

| Signal | Pull | Status |
| --- | --- | --- |
| `ADC_EN`, `DAC_EN` | 10 kΩ up to +3.3 V | ⛔ **Decided against (2026-08-11).** Not fitted. |

⚠️ **An earlier version of this section called a floating enable "harmful" because it can assert
a chip select into powered daughterboards. That overstates it.** A chip select asserted with no
`SCK` activity does nothing — SPI slaves act only on clock edges, and the Pico isn't driving the
clock during the window in question either. No transaction can occur, so nothing is corrupted.

What remains is the ordinary objection to a floating CMOS input: `~E0` sitting near mid-rail
turns both input transistors partly on, raising supply current and risking oscillation, for
~100–200 ms per power cycle. The bench result — eight daughterboards, working — is real evidence
that this window is benign in practice.

So these are **not** required. ⛔ **Decision, 2026-08-11: skipped.** The bench result — eight
daughterboards, working — stands, and a ~100–200 ms window of elevated supply current per power
cycle is not worth the parts. `ADC_EN` is `A1.7` + `U1.4` and `DAC_EN` is `A1.9` + `U5.4`, both
with no pull, and that is the intended final state.

If a future spin ever revisits it: 10 kΩ or 100 kΩ to +3.3 V, either fine against the RP2040's
12 mA drive (100 kΩ is gentler — 33 µA vs 330 µA sunk when the Pico drives low).

Same class, lowest priority, and **also skipped**: `U2` (TMUX1208PW) has an **active-high** EN on
pin 2, driven only by `GPIO9`. A 10 kΩ pull-**down** to GND would default the EEPROM mux to
disconnected. Genuinely optional — a mux connection with nothing driving it does nothing.

## Rail sensing

Only **+12 V** needs sensing. The +3.3 V sense originally specced here is redundant: with the
`3V3_EN` interlock, if the Pico is executing code at all then +3.3 V is necessarily up.

✅ **Built and verified 2026-08-11.** The net is named `12V_SENSE` on the board.

| Leg | Ref | Value | LCSC | Node |
| --- | --- | --- | --- | --- |
| Top | `R5` | 100 kΩ 1% 0805 | `C96346` | `+12V` → `12V_SENSE` |
| Bottom | `R10` | 22 kΩ 1% 0805 | `C114565` | `12V_SENSE` → GND |
| Filter | `C10` | 100 nF 0603 | `C14663` | `12V_SENSE` → GND, at the pin |

Ratio 22/122 = **0.1803**, into `GPIO26` / ADC0 (A1 pad 31). *(`R9` was already taken by the `~OE`
pull-up, so the bottom leg is `R10`.)*

**`C10` belongs at the Pico, not at the divider — and it is there:** 4.4 mm from `A1` pad 31,
on the same vertical trace. The tap runs ~110 mm from the divider at the power stage to the
Pico, at an 18.03 kΩ Thévenin impedance, so the filter earns its place at the load end. The
corridor it shares carries `A3`, `PL` and `Q7_8` — no SPI clock, and `A3` is static during a
transfer, so the coupling is benign.

Across the 9–15 V input range:

| Jack | ADC pin |
| --- | --- |
| 9 V | 1.623 V |
| 12 V | 2.164 V |
| 15 V | **2.705 V** |
| 18.3 V | 3.300 V — the fault ceiling |

Firmware conversion: `V12 = adc_volts * 5.5455` (122/22).

**No Schottky clamp.** Earlier drafts specced a BAT54S to the Pico's 3V3. Drop it: the 100 kΩ
top leg limits current into the pin's own ESD clamp to (26 − 3.6)/100 k ≈ 224 µA even during a
full TVS event, and the 100 nF slews the edge. **`C10` is load-bearing for that argument** —
half the justification for omitting the clamp is the cap, so do not delete it as "just a filter". The external diode adds a part and a net for
nothing. It would also have had nowhere good to clamp *to* — `A1` pin 36 must stay unconnected
(see "Rejected alternatives"), so the only available node is the board's own +3.3 V rail.

**No buffer either.** The 18.03 kΩ Thévenin looks high for an ADC input, but nothing here needs
an op-amp. Settling is not the issue — R_th × C_sample is ~100 ns, well inside the RP2040's
sample aperture. The switched-cap input's average draw is C_s·V·f_s, and *you choose f_s*: at a
monitoring rate of ~1 kSPS it is sub-µV across 18 kΩ. Take short bursts and idle; do not
free-run the ADC on this channel at hundreds of kSPS, which is the only way to make the
impedance matter. And a buffer would add offset to a measurement whose error is already
dominated by `ADC_VREF` (below), improving nothing measurable.

*(Contrast the daughterboard, where the AD7193 does need external OPA2333 followers behind an
almost identical 16.7 kΩ divider — a ΣΔ modulator samples continuously at a rate you cannot
lower, into a µV error budget, and both of its internal buffer modes are ruled out. See
`hardware/daughterboard/CLAUDE.md`.)*

**Accuracy is not a design goal here.** `ADC_VREF` (A1 pad 35) stays unconnected; the module
derives it from its own 3V3, so absolute accuracy is roughly ±4 % once the Pico's rail tolerance
and 1 % resistors are combined — ±600 mV on a 15 V rail. That is entirely adequate for "is the
adapter sagging", which is the whole purpose. Do not build a calibration on it. Quantisation is
irrelevant by comparison (~4.5 mV of rail per LSB), which is also why the divider was left at
100 k/22 k rather than rescaled to use more of the ADC span.

What firmware should do with it:

- Report the rail in the identify/status response.
- Refuse `ISET`/enable commands when +12 V is out of range, with a distinct error.
- Flag a sagging +12 V under full 64-channel load. At ~640 mA an undersized adapter will droop,
  and without rail sensing that surfaces as an unexplained *accuracy* problem rather than an
  obvious power problem.

## PRSNT — daughterboard seating detection

> ⛔ **Deferred — not implemented in this revision.** The decision is to tie `B31` to GND on all
> eight sockets and not use the feature. The rest of this section is kept because the reasoning is
> sound and the daughterboards already carry the loop, so it can be picked up in a later spin at
> no cost to them.
>
> **What to do on the board now:** tie `J1..J8.B31` to GND uniformly, and delete the dangling
> one-node net `PRSNT`. ✅ Done for `J1`–`J7`, and the dangling net is gone. ⚠️ **`J8.B31` is
> still unconnected** — one wire left.
>
> Dropping it is what makes the GPIO budget close — see "GPIO budget" below.

### The loop already exists; the motherboard discards it

Each daughterboard shorts `J1.A1` to `J1.B31` on its `PRSNT` net. Those are **diagonally
opposite corners** of the edge connector — the PCIe arrangement, chosen so the loop closes only
when the card is fully *and squarely* seated. A cocked board reads as absent, which is exactly
what you want.

### The change, if it is ever picked up

Keep `A1` on GND; move `B31` on each socket to a Pico GPIO with the RP2040's **internal pull-up**
enabled.

| State | B31 | GPIO reads |
| --- | --- | --- |
| Fully seated | shorted to A1 (GND) by the daughterboard | **LOW** |
| Absent or partially inserted | open | **HIGH** (internal pull-up) |

**Zero new components** — no external resistors, mux, or shift register. Eight net changes on the
motherboard and nothing else. **The daughterboards need no change at all**, which matters given
eight are already populated.

### Pin assignment — does not fit on direct GPIOs

This was originally planned as eight direct GPIOs, on the claim that nine were free
(`GPIO0, 1, 8, 14, 15, 19, 26, 27, 28`). **That count was wrong** — `GPIO8` carries the LDO's
power-good — and `GPIO19` has since gone to `~OE`. Six are actually free (`GPIO0, 1, 14, 15, 27,
28`), which is two short of the eight PRSNT needs.

So if PRSNT is ever picked up, it has to go through the **74HC165 on the CP/PL chain** described
under "GPIO budget", not onto direct pins. That costs one IC and zero GPIOs, and it leaves
`GPIO0/1` free as a debug UART.

Whichever route, the pins are ordinary bank-0 GPIOs with software-selectable internal pull-ups
(~50–80 kΩ), and none carries a conflicting function — the SPI0 (`GPIO2/3/4`) and SPI1
(`GPIO10/11/12`) pinmuxes are untouched.

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

## GPIO budget — closes comfortably, with PRSNT dropped

⚠️ **Correction to an earlier count.** This document previously claimed nine free GPIOs,
listing `GPIO0, 1, 8, 14, 15, 19, 26, 27, 28`. **`GPIO8` is not free** — it carries the LDO's
power-good via `R6` on the built board. Eight are actually free, which meant the ten-wanted plan
(eight PRSNT + `~OE` + sense) was short by two, not one, and *neither* of the cheap escapes
worked: dropping the +12 V sense still left a shortfall.

Dropping PRSNT resolves it outright:

| Function | Pin | Pico pad | Status |
| --- | --- | --- | --- |
| +12 V sense | `GPIO26` / ADC0 | 31 | to wire |
| `U3.~OE` | `GPIO19` | 25 | to wire (`R9` placed) |
| LDO power-good | `GPIO8` | 11 | built, via `R6` |

That leaves `GPIO0, 1, 14, 15, 27, 28` free — six spare, and `GPIO0/1` stay available as a debug
UART, which matters more than it used to now that the interlock has removed USB-only flashing.
The SPI0 (`GPIO2/3/4`) and SPI1 (`GPIO10/11/12`) pinmuxes are untouched. `GPIO27/28` are
ADC-capable but unused; only `GPIO26` needs its analog function.

**The 74HC165 option is therefore not needed.** It remains the right answer if PRSNT is ever
picked up: the daughterboards already implement a serial chain — `GPIO16` (`CP`) broadcasts to
every `A8`, `GPIO18` (`PL`) to every `A10`, each board's `A11` feeds the next board's `A9`, and
the tail `J8.A11` reads back on `GPIO17`. The head, `J1.A9`, is tied to **GND**, which is a free
insertion point. A '165 reading the eight `B31` pins and driving `J1.A9` appends its byte to the
stream already being shifted: one IC, zero GPIOs, no daughterboard change.

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

**Widening the input range to 15 V does not move these numbers.** The +12 V draw is set by the
DAC-programmed current sources, not by the rail voltage, so the fuse and the converter's input
current are unaffected. What *does* move is dissipation inside the XTR200s: the pass element
drops (V_rail − V_heater), so 12 V → 15 V is roughly +30 % heat per channel, times 64. If 15 V is
only a "must survive it" limit that is fine; if it is an operating point, redo the thermals on the
daughterboard.

### F1 — fuse
PPTC, **1.5 A hold / 3 A trip**, 1812. Auto-recovery matters for a shared lab instrument.

Total +12 V draw is the daughterboards (~640 mA) *plus* the K7805's input current (~100 mA,
below) ≈ **740 mA**. A 1.1 A-hold part derates to ~0.85–0.9 A hold at 40 °C inside an enclosure,
which is uncomfortably close to 740 mA — hence 1.5 A. ~0.1 Ω at 740 mA is 74 mV — negligible.
A 2 A slow-blow cartridge is the alternative if you prefer a hard failure.

The placed part is `1812L150/24MR` — **24 V rated, not the 30 V originally specced**; LCSC stocks
the series only to `/24MR`. Still correct: a fuse's voltage rating is what it must *interrupt*,
which is the 15 V source, not a microsecond TVS transient. (An earlier version of this note
justified 24 V by comparing it against the 21.5 V TVS clamp. With `D2` moving to SMBJ16A that
clamp becomes 26 V, so the comparison no longer holds — but the reasoning above never depended
on it.)

### D2 — transient suppressor
Unidirectional, cathode to +12 V, anode to GND, **between F1 and Q1** so a surge *or* a
reverse-polarity event conducts and blows the fuse — a crowbar backing up the FET.

✅ **Now `SMBJ16A` (`C151254`).** The previously placed `SMBJ13A` was wrong for a 15 V input
range: a 13 V standoff and a 14.4 V *minimum* breakdown means that on a 15 V rail it conducts
continuously and destroys itself. SMBJ15A is not the answer either — its 15 V standoff sits
exactly at the maximum, with no margin for an adapter that idles at 15.4 V.

| Part | Standoff | V_BR min | Clamp | Verdict for a 9–15 V rail |
| --- | --- | --- | --- | --- |
| SMBJ13A | 13 V | 14.4 V | 21.5 V | **conducts continuously — do not fit** |
| SMBJ15A | 15 V | 16.7 V | 24.4 V | zero standoff margin |
| **SMBJ16A** | **16 V** | **17.8 V** | **26.0 V** | **correct** |

26 V still sits below the K7805's 32 V input limit, so the converter survives whatever the TVS
lets through.

⚠️ **Do not "oversize" a TVS by voltage.** Clamping voltage scales with standoff, so a
higher-voltage part passes *more* stress downstream, not less — SMBJ24A would clamp at 38.9 V,
past the K7805's 32 V limit and far past Q1's ±20 V Vgs. It would protect nothing and destroy the
buck. Pick the standoff to the rail and stop.

**Oversize by *power* instead, if you want margin.** `SMCJ16A` is the same 16 V part in the
1500 W SMC package: it clamps *lower* at the same surge current and absorbs more energy. Pure
win, at the cost of board area and a footprint change from `Diode_SMD:D_SMB` to `D_SMC`.

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

⚠️ **Symbol polarity — still not addressed, despite an earlier ✅ here.** The board originally
drew `D2` with `Device:D_TVS`, the *bidirectional* symbol, whose pins are `A1`/`A2` and which
therefore carries no polarity at all, against a polarized `D_SMB` land pattern. Swapping to
`Diode:SM6T68A` did **not** fix that: its pins are also named `A1`/`A2`. The sheet still cannot
express which end is the cathode.

✅ **The board itself is correct**, verified 2026-08-11 against the PCB: pad 1 of the `D_SMB`
footprint carries the cathode silkscreen bracket and sits on the fused input node with `F1.1`
and `Q1.3` (drain); pad 2 is on GND. Cathode to the rail, anode to GND, TVS between fuse and
FET — as specced. But it is right by land-pattern convention, not because the schematic says so.
Getting this backwards is not a subtle failure: a reversed TVS forward-conducts at ~0.9 V and
shorts the rail on first power-up.

⚠️ **But the symbol name lies.** `SM6T68A` is a 68 V TVS; only the `Value` field (`SMBJ16A`) and
the LCSC field (`C151254`) describe the part actually fitted. That is legal in KiCad and the
netlist is correct, but the next person to read the sheet will be misled. Either rename via a
local symbol or accept it deliberately — see "Schematic hygiene".

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
| `R1` | 100 kΩ (`C96346`) | Gate → GND. Turns Q1 on when polarity is correct. Placed. |
| `D6` | **BZT52C18** (`C192739`, 18 V zener, SOD-123) | Gate–source clamp, cathode to source. ✅ Placed and valued; verified `K` → `+12V` (Q1 source), `A` → Q1 gate + `R1`. |

**Why not AO3401A** (the obvious, cheapest choice, and what earlier drafts of this spec
selected): it is rated **Vgs ±12 V**. With the gate pulled to GND on a 12 V rail, Vgs sits at
exactly −12 V *continuously* — at the absolute maximum, with zero margin, and over it on any
adapter that idles above 12.0 V. AO3407A is the same package and pinout with 8 V of Vgs
margin, so the whole problem disappears for ~$0.08.

⚠️ **The symbol still says `AO3401A`.** `Q1` is drawn with `Transistor_FET:AO3401A` and valued
`AO3407A` (`C15155`, the correct part). Same pinout and package, so the board is right — but
given that the *entire* reason for choosing the 3407 is its Vgs rating, a sheet that names the
3401 is a trap. See "Schematic hygiene".

### D6 — why the gate zener is now required, not optional

At a 12 V rail this was a documented corner worth skipping: Vgs = −12 V against ±20 V left 8 V of
margin, and the SMBJ13A's 21.5 V clamp only occurs at its full 600 W / ~100 A rating — a bench
adapter's surge energy clamps nearer 14–17 V, inside the rating.

**A 15 V rail closes that margin from both directions.** Vgs sits at −15 V continuously (5 V of
headroom), and the SMBJ16A clamps as high as **26 V** — straight through the ±20 V rating rather
than near it. Fit the clamp.

**Use BZT52C18 (18 V), not the BZT52C15 this document specced for 12 V.** The value is boxed in
on both sides and 18 V is the only comfortable fit:

- It must sit **above 15 V** so it never conducts in normal operation. A 15 V part on a 15 V rail
  idles on its knee (V_z spread 13.8–15.6 V), which makes it load-bearing rather than protective —
  exactly the objection that ruled out a 10 V part at 12 V.
- It must sit **below 20 V** to clamp inside Q1's Vgs rating. BZT52C18's V_z spread is
  16.8–19.1 V, so even the worst-case unit clamps under the limit.

Power rating is irrelevant here — the clamp never carries more than (26 − 18)/100 kΩ ≈ 80 µA
through `R1`, so the smallest 200 mW part is already a thousandfold overrated. There is nothing to
gain by oversizing `D6`; only the voltage matters, and it has one right answer.

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

**Use 35 V, not 25 V.** ✅ The placed `C7` is 35 V (`C125981`). The case for it only got stronger:
with `D2` at SMBJ16A the clamp is **26 V**, and standard ~80 % electrolytic derating puts a 25 V
part at 20 V — well under the clamp, and now under the *continuous* headroom you'd want on a
15 V rail too.

⚠️ **Check `C5` while you are here.** It sits directly on the +12 V rail as the K7805's input
capacitor, and the netlist carries no voltage rating. The specced part (`C77102`) is 50 V, which
is correct — confirm that is what the schematic field actually says. A 25 V X5R at 15 V DC bias
would lose over half its capacitance, and Table 1's 10 µF is a switching-loop requirement rather
than decoupling by habit.

This does not replace the K7805's own input capacitor; that is a separate Table 1 requirement.
680 µF is the module's max capacitive load on its *output*, so it doesn't constrain this.

### Capacitor part numbers

Verified on LCSC. Ceramics are specced **above** the datasheet's nominal voltage on purpose:
DC bias derating costs an X5R most of its capacitance near its rating, so a 10 V-rated 22 µF at
5 V is really ~11 µF, while the 25 V part holds most of its value.

**Mixed case sizes, by design.** There is no single-size rule on this board: each ceramic is
sized for its job — 0603 for the 100 nF decouplers and the sense filter, 0805 for the 10/22 µF
regulator caps, 1210 for `C5`. An earlier draft of this document specced a blanket 0805
standardisation; it was never carried out and is not wanted.

**Refdes below are as built** — this table previously used the pre-rework numbering; see "Refdes
drift" at the top if you are cross-referencing an older revision of this document.

| Ref | Role | Value / package | Brand & MPN | LCSC |
| --- | --- | --- | --- | --- |
| `C7` | +12 V bulk | 220 µF 35 V, Ø8×10 mm can, `Capacitor_SMD:CP_Elec_8x10` | Nichicon `UWT1V221MNL1GS` | **`C125981`** |
| `C5` | K7805 input (Table 1) | 10 µF 50 V X7R **1210** | Murata `GRM32ER71H106KA12L` | **`C77102`** |
| `C6` | K7805 output (Table 1) | 22 µF 25 V X5R 0805 | Samsung `CL21A226MAQNNNE` | **`C45783`** |
| `C8`, `C9` | LDO in / LDO out | 10 µF 25 V X5R 0805 | Murata `GRM21BR61E106KA73L` | **`C84416`** |
| `C1`–`C4` | Per-IC decoupling | 100 nF 50 V X7R **0603** | Samsung `CL10B104KB8NNNC` | **`C14663`** |
| `C10` | +12 V sense filter | 100 nF 50 V X7R **0603** | Samsung `CL10B104KB8NNNC` | **`C14663`** |

**Why the bulk values are not 0603:** 0603 tops out near 10 µF at 6.3–10 V. Neither 22 µF/25 V
nor 10 µF/50 V exists in that case size, so `C5`/`C6`/`C8`/`C9` have to be larger regardless.
100 nF in 0603 is unaffected by any of that, which is why the decouplers stay small.

**Why `C5` is 1210.** DC bias derating worsens as the case shrinks — same voltage rating in a
smaller package means thinner dielectric — and the 0805 50 V part is X5R where the 1210 is X7R,
the more bias- and temperature-stable dielectric. `C5` sits on the +12 V rail, up to 15 V here,
where an 0805 would keep roughly half its 10 µF. Table 1's 10 µF is a requirement of the module's
input switching loop, not decoupling by habit, so undershooting it is a real compromise. The 0805
option (`GRM21BR61H106KE43L`, `C440198`) is also 3× the price for less delivered capacitance —
$0.325 vs $0.114.

Ceramics are specced **above** the datasheet's nominal voltage on purpose: an X5R loses most of
its capacitance near its rating, so a 10 V-rated 22 µF at 5 V is really ~11 µF while the 25 V
part holds most of its value.

A THT bulk alternative exists — Nichicon `UHE1V221MPD6`, **`C251010`**, D10×L12.5 mm, 5 mm pitch,
80 mΩ, 865 mA, 7000 h @105 °C — but it won't be machine-placed if the board goes through JLCPCB
assembly.

### U6 — 12 V → 5 V converter
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

**Pin compatibility is the whole point of picking it:** U6's nets (`+12V` → pin 1,
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

The pre-rework `Package_TO_SOT_THT:TO-220F-3_Vertical` was wrong on both counts: TO-220 is 10.16 mm
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

Its input range is **2.2–5.5 V** — it cannot be fed from 12 V, which is precisely why U6 exists
rather than a single linear stage.

✅ **Required caps — fitted.** Both are 10 µF `C84416`, above the datasheet's 1 µF minimum:

| Ref | Value | Node |
| --- | --- | --- |
| `C8` | 10 µF ceramic | IN → GND, at the pin |
| `C9` | 10 µF ceramic | OUT → GND (datasheet minimum is 1 µF; 10 µF for the connector bank) |

**Thermals:** 1.7 V × 110 mA ≈ **0.19 W**. In WSON-6 with the exposed pad soldered to a
via-stitched pour this is a non-issue; keep the thermal vias in the footprint.

### Decoupling
100 nF per IC power pin — two '138s, the '595, the TMUX1208 — plus 10 µF near the connector
bank, and the regulator caps above.

The board carries `C1`–`C4` (100 nF) plus `C9` (10 µF) on +3.3 V. That is four 100 nF against
four ICs, so it is one-per-IC only if they are placed one-per-IC — check that in layout rather
than assuming it from the count. There is **no** bulk ceramic at the +3.3 V connector bank beyond
`C9`, and none at the +12 V bank at all; the +12 V side leans on `C7`'s 220 µF, which is upstream
by the regulator rather than out at the sockets.

⛔ **The extra 10 µF at the far end of the connector bank is skipped (2026-08-11).** `C9` plus
the daughterboards' own local decoupling carry it; each board brings its own bulk behind its
edge fingers.

## Layout notes

- `C5`/`C6` hard against U6's pins 1 and 3 — the datasheet is explicit about this, and the
  module's switching loop closes through them.
- Protection chain physically ordered jack → F1 → TVS → Q1, short and wide. A TVS on a long
  trace clamps its own inductance instead of the transient.
- Keep `R7`/`R8` close to the Pico's pin 37; it's a high-impedance node and shouldn't run
  alongside the SPI clocks.
- U6 is a shielded module, but keep its ground return tight and don't run the +12 V sense divider
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

Three symbol/value mismatches. In every case KiCad takes the net name and the BOM line from the
`Value` and `LCSC` fields, so **the netlist and the board are correct** — but the sheet reads
wrong, and one of them is genuinely dangerous to leave.

| Ref | Symbol on the sheet | Actual part | Why it matters |
| --- | --- | --- | --- |
| `Q1` | `Transistor_FET:AO3401A` | AO3407A (`C15155`) | ⚠️ **Still open — fix this one.** The whole reason for the 3407 is its ±20 V Vgs; the 3401 is ±12 V and would be over its absolute maximum on a 15 V rail. A sheet naming the 3401 invites someone to "correct" the BOM to match. |
| `D2` | `Diode:SM6T68A` | **SMBJ16A** (`C151254`) | ⚠️ **Still open.** SM6T68A is a 68 V part, *and* its pins are `A1`/`A2`, so it does not even buy the polarity it was chosen for. |
| `U3` | `74xx:74HC595` | SN74LV595 (`C116847`) | Harmless — same pinout and function. |
| `D6` | `Diode:BZT52Bxx` | BZT52C18 (`C192739`) | Harmless — generic zener symbol, correctly valued and sourced. |

✅ `#PWR050` and `#PWR036` are fixed — no power symbol on the sheet now has a `Value` that
disagrees with its graphic.

## Build checklist

### Sourcing — every part in the schematic

Verified on LCSC. Rows marked *(BOM)* are already in `master_bom_lcsc.csv`.

| Ref | Part | LCSC | Footprint |
| --- | --- | --- | --- |
| `A1` | Raspberry Pi Pico *(BOM)* | `C7203003` | — |
| `U1`, `U5` | **SN74HC138PWR** (TI) — replaces SN74LVC138APWR, see below | `C157527` | `Package_SO:TSSOP-16_4.4x5mm_P0.65mm` |
| `U2` | TMUX1208PW | ✅ **`C494728`** | TSSOP-16 |
| `U3` | SN74LV595APWR *(BOM)* | `C116847` | TSSOP-16 |
| `U4` | LP5912-3.3DRVR (TI) | `C524780` | WSON-6 |
| `U6` | K7805-500R3 (EVISUN) | `C19188491` | `Converter_DCDC:Converter_DCDC_TRACO_TSR-1_THT` |
| `Q1` | AO3407A (AOS, Vgs ±20 V) | `C15155` | SOT-23 |
| `D1` | SS34 (MDD) | `C8678` | `Diode_SMD:D_SMA` |
| `D2` | SMBJ16A (Littelfuse) — replaced SMBJ13A (`C133675`) | ✅ **`C151254`** | `Diode_SMD:D_SMB` |
| `D3`–`D5` | Rail LEDs (+12 V / +5 V / +3.3 V) | `C157740` | `LED_SMD:LED_0603_1608Metric` |
| `D6` | BZT52C18-7-F (Diodes Inc) — Q1 gate clamp | ✅ **`C192739`** | `Diode_SMD:D_SOD-123` |
| `F1` | 1812L150/24MR (Littelfuse) | `C142805` | 1812 |
| `R1`, `R6` | 100 kΩ 0805 1% (Yageo `RC0805FR-07100KL`) | `C96346` | `Resistor_SMD:R_0805_2012Metric` |
| `R2`, `R8` | 4.7 kΩ 0805 | `C431850` | `R_0805_2012Metric` |
| `R3` | 1.5 kΩ 0805 | `C4310` | `R_0805_2012Metric` |
| `R4` | 680 Ω 0805 | `C17798` | `R_0805_2012Metric` |
| `R7`, `R9` | 10 kΩ 0805 | `C84376` | `R_0805_2012Metric` |
| `J1`–`J8` | Samtec PCIE-064-02-F-D-TH *(BOM)* | `C4597619` | — |
| `J9`–`J11` | ✅ Hirose FH12-50S-0.5SH — replaces the un-stocked Molex 54132-5033 | `C202116` | `Hirose_FH12-50S-0.5SH_1x50-1MP_P0.50mm_Horizontal` |
| `J12` | DC005-T20 barrel jack *(BOM)* | `C84007` | — |
| `J13` | 1×2 pin header, +12 V | — | `PinHeader_1x02_P2.54mm_Vertical` |

Capacitors are in "Capacitor part numbers" above.

✅ **`D2` and `D6` are now sourced.** Both looked up on LCSC 2026-08-11 and set in the schematic:

- **`D2` = Littelfuse SMBJ16A, `C151254`** — 16 V standoff, V_BR 17.8–19.7 V, 26 V clamp, 600 W
  10/1000 µs, unidirectional, DO-214AA. Branded on purpose: LCSC lists SMBJ parts from a dozen
  no-name houses at a third of the price, and clamping voltage is exactly the spec generics are
  loosest about.
- **`D6` = Diodes Inc BZT52C18-7-F, `C192739`** — 18 V, V_z spread 16.8–19.1 V, 500 mW, SOD-123.
  A generic would be fine here (it carries microamps into a gate); the branded part costs
  cents.

**Note the `D6` footprint is plain `D_SOD-123`, not `SOD-123F`.** Every BZT52C18 on LCSC —
Diodes Inc, LGE `C545295`, JSCJ `C43490`, YONGYUTAI — is standard SOD-123; the `BZT52C18S` parts
that surface in a search are SOD-323, a different package. The board was corrected to match.

Two sourcing questions this document used to flag are now **closed**: `U2` (TMUX1208PW) resolved
to `C494728`, and `J9`–`J11` moved off the un-stocked Molex part to a stocked Hirose equivalent.

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

✅ **Both are resolved on the board.** `U1`/`U5` are `SN74HC138PWR` (`C157527`) in
`TSSOP-16_4.4x5mm_P0.65mm`, so all four logic ICs (`U1`, `U2`, `U3`, `U5`) now share one
footprint. The old `Package_SO:SSOP-16_5.3x6.2mm_P0.65mm` (TI's DB package) is gone.

The `master_bom_lcsc.csv` row may still read `SN74LV138 (74HC138)` mapped to `SN74LVC138APWR` —
three different logic families in one line. Update it against `production/bom.csv`.

**Pick branded parts for `D2` and `F1` deliberately** — see the note under the sourcing table.

### Still to add

**Nothing.** ✅ The sense divider went in as `R5` (100 kΩ, `C96346`), `R10` (22 kΩ, `C114565`)
and `C10` (100 nF 0603, `C14663`), and every other item this table used to list has been
deliberately dropped:

| Dropped | Why |
| --- | --- |
| `ADC_EN` / `DAC_EN` 10 kΩ pull-ups | Decision 2026-08-11 — see "Board-side defaults" |
| `U2` EN 10 kΩ pull-down | Same; was always optional |
| 10 µF at the J1–J8 +3.3 V bank | Decision 2026-08-11 — see "Decoupling" |
| `BAT54S` sense clamp | The 100 kΩ top leg makes it unnecessary — see "Rail sensing" |

The one open suggestion is extra 100 nF (`C14663`) on any IC power pin that turns out not to
have one — a layout check, not a known gap.

### Changes to placed parts

| Ref | From | To | Status |
| --- | --- | --- | --- |
| `D2` | `SMBJ13A` (`C133675`) | **`SMBJ16A`** (`C151254`) | ✅ done |
| `D6` | `BZT52Bxx` placeholder, no LCSC | **`BZT52C18`** (`C192739`), footprint `D_SOD-123` | ✅ done |
| `U3.13` (`~OE`) | hardwired to GND | `GPIO19` (A1 pad 25), with `R9` pulling up to +3.3 V | ✅ done |
| `Q1` | symbol `Transistor_FET:AO3401A` | a symbol that names the AO3407A actually fitted | ⚠️ open |
| `D2` | symbol `Diode:SM6T68A` (pins `A1`/`A2`) | a genuinely polarized symbol | ⚠️ open |
| `C5` | — | the `Value` field is bare `10uF`; `C77102` is the correct 50 V 1210 part, so BOM and fab are right | cosmetic |

Already done in `397a4a4`: the `U8`→`U6` K7805 swap, `C7` to a polarized `CP_Elec_8x10`, `C5` to
1210, `R7` 100 k→10 k, and `R8` 100 k→4.7 k with its far end moved from A1 pin 36 to `+3.3V`.
*(An earlier version of this line also claimed a 0603→0805 ceramic standardisation landed in
`397a4a4`. It did not — `C1`–`C4` are 0603 — and the board no longer wants one.)*

### Net changes

- ✅ `+12V` ← Q1 source. Done.
- ✅ `A1.31` (GPIO26/ADC0) ← `12V_SENSE`. Done.
- ✅ `A1.25` (GPIO19) ← `U3.13`, tie to GND cut. Done — net `~{OE}` = `U3.13` + `A1.25` + `R9.1`.
- ⚠️ `J1..J8.B31` → GND: **seven of eight done.** `J1`–`J7` are on GND and the dangling one-node
  `PRSNT` net is gone; **`J8.B31` is still unconnected.**

`R6`/`PG` on `GPIO8` **stays**. An earlier version of this list proposed dropping it to free the
pin for PRSNT — with PRSNT deferred there is nothing to free it for, and six GPIOs are spare
regardless.

## Firmware follow-up

Changes in `firmware/motherboard-test/` that this revision implies:

- Drive `GPIO19` (`~OE`) — hold high or leave as an input on `R9` until the first `0xFF` has been
  shifted and latched into `U3`, then drive low.
- Read `GPIO26` and report the +12 V rail in the identify/status response; refuse `ISET`/enable
  commands when it is out of range, with a distinct error. `V12 = adc_volts * 5.5455`.
- Nothing to do for PRSNT — the feature is deferred.
