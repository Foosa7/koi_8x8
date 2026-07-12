# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

PlatformIO firmware for a Raspberry Pi Pico (RP2040, Arduino framework via Mbed) that
controls up to **8 identical daughterboards** from one controller. Each daughterboard carries
an **8-channel DAC80508** (write-only), an **8-channel AD7193** ADC, and **8 XTR200**
current-source front ends (one per channel). Per channel: the DAC sets an XTR200 current
source, the current flows through an external load (in the real system a **TiN thermo-optic
heater** on a photonic chip) to ground, and the AD7193 measures the voltage developed across
that load. `src/main.cpp` is a **host command server**: the PC sends per-channel current
setpoints (mA) and the firmware sources the current and reports back the measured voltage, so
the host can compute resistance / power. See "Host command protocol" below.

In firmware, **"device index" = daughterboard** (0-based). A daughterboard's 8 DAC/ADC
channels are the 8 current-source / measurement channels on that board. The 74HC138 decoders
pick *which daughterboard's* ADC or DAC is on the bus; the SN74LV595 picks *which
daughterboards'* XTR200 front ends are powered (see below).

> Bring-up note: with **no load resistor** installed on a channel, the ADC reads ≈0 even
> though the DAC/current source is working — there's nothing for the current to develop a
> voltage across. That's expected, not a fault.

## Build & Flash

```bash
pio run                 # build (env:pico)
pio run -t upload       # build + flash over USB
pio device monitor      # serial monitor @ 115200 (type commands by hand)
pio run -t clean
```

There is no working test suite — `test/` contains only the PlatformIO boilerplate README.

## Host command protocol

`src/main.cpp` runs a line-based ASCII command server over USB serial (115200, `\n`-terminated).
Every command yields exactly **one** reply line so the host stays in sync; errors reply
`ERR <reason>`. Channels use a flat global index **`g = board*8 + channel`** (0..63);
unpopulated channels report `nan`. The firmware auto-detects populated boards at boot (AD7193
ID nibble) and only configures/measures those.

**Two-way ack (fw1.1):** every `OK` reply echoes its command name so the host can verify a
reply belongs to the command it just sent, and **every firmware-initiated line starts with
`#`** (boot banner, `# READY`, cal progress, STREAM dumps, all driver diagnostics) — an
unprefixed line is always a direct command reply. Never add a bare `Serial.print` to firmware
code that can run mid-session (RESCAN's calibration used to do this and desynced the host).
The GUI's `KoiLink.command()` enforces the ack: it validates each reply's shape against the
command sent and, on mismatch or timeout, drains the stream until quiet and resends once.

| Command | Effect | Reply |
| --- | --- | --- |
| `*IDN?` | identify | `KOI,8x8,fw1.1` (the device is named **Koi 8×8**; pre-rename firmware replies `MOBO,…` and the GUI accepts both) |
| `*RST` | all currents 0, front-ends disabled | `OK *RST` |
| `ISETA i0 … i63` | set all 64 currents (mA, g-order) | `OK ISETA` |
| `ISET g mA` | set one channel | `OK ISET <g>` |
| `MEASA?` `[mask]` | measure all 64 (or masked boards) | `v0,…,v63` |
| `MEAS? g` | measure one channel | `<volts>` |
| `XTR enmask` | bit b=1 → board b front-ends enabled | `OK XTR 0x..` |
| `AVG n` | on-micro averages per measurement (1..64) | `OK AVG <n>` |
| `RATE fs` | AD7193 filter word (speed/noise, 1..1023) | `OK RATE <fs>` |
| `STREAM ON\|OFF` | periodic `# …` dump for eyeballing | `OK STREAM ON\|OFF` |
| `RESCAN` | re-detect boards at runtime (resyncs SPI + reconfigures as needed) | `OK RESCAN active=0x.. new=0x..` |
| `PING? b` | board liveness: ADC ID + one real conversion, non-mutating (`0` for an undetected board — RESCAN adopts it) | `OK PING <b> 1\|0` |

Typical experiment loop: `ISETA …` → settle → `MEASA?` (two round-trips).

The DAC80508 is write-only, so `PING?`'s ADC conversion is the strongest per-board liveness
proxy available (same seating/power/decoder address lines as the DAC). The only true DAC
verification is analog loopback on a loaded channel: `ISET g x` → settle → `MEAS? g` and check
the voltage lands in the plausible band.

**Reported voltage is the RAW ADC-pin voltage**, computed in `double` (a 24-bit code exceeds a
32-bit float's mantissa). The host applies the **6:1 input divider** (100k/20k) to get the true
heater voltage: `heater_V = reported_V × 6` (e.g. 2.5 V heater → 416.7 mV at the ADC pin). The
DAC side stays in `float` — a 16-bit code is exact in float and well below the DAC's own
quantization. Current→DAC-code uses a per-channel linear cal (`calSlope`/`calOffset`, seeded
with the ideal `Vdac = 0.47·I_mA` from the XTR200 transconductance) to be fit against a Keithley
2400 later. The `calSlope`/`calOffset` table (firmware) and the divider ratio (host, default
6.0) are the placeholders to trim during calibration.

`tools/koi_gui.py` is a Tkinter monitor that polls `MEASA?` and shows raw + divider-corrected
voltage for all 64 channels in an 8×8 grid (`pip install pyserial`; auto-detects the port).
It RESCANs automatically at connect (recovers boards the boot-time detect missed — flaky HASL
contact) and auto-re-RESCANs (rate-limited to 1/10 s) when a previously seen board stops
answering, so boards reappear without a manual Rescan click.

The GUI's "Cal offsets" builds a **per-channel XTR200 offset-current table**: it drives all
populated channels to `I test` against a known load resistance and solves
`IOS = heater_V/R_known + heater_V/120k − I_test` per channel (single point, no sweep). The
table is stored as a **current** (mA, in `offset_table.json`, auto-loaded at startup) — not a
voltage — because IOS's voltage signature scales with the load and the heater R changes with
power. It feeds the I/R/P columns and Characterize R. Channels whose implied |IOS| > 100 µA
(10× datasheet max → no load fitted / wrong R_known) are skipped, not written into the table.
The global "Offset+ (mV)" field remains for the small ADC-path voltage offset; the two are
complementary (current-side vs voltage-side).

## Architecture

Two hardware SPI buses, two **74HC138** 3-to-8 decoders for chip select, and one
**SN74LV595** shift register for analog-front-end enables.

**SPI buses** (`arduino::MbedSPI` instances in `main.cpp`):

- **SPI0** — GP2 SCK, GP3 MOSI, GP4 MISO. Shared by the AD7193 ADCs **and** the SN74LV595.
- **SPI1** — GP10 SCK, GP11 SDI. DAC80508 bus, **write-only** (no MISO — the DAC80508ZCRTER
  has no SDO; that pin becomes CLR and is tied high, so it is ignored).

**Chip-select / control GPIOs** (plain `digitalWrite`, not SPI):

- **GP20 / GP21 / GP22** — shared A0 / A1 / A2 address lines feeding **both** 74HC138 decoders.
- **GP5 ADC_EN**, **GP6 DAC_EN** — per-decoder enables. Assert exactly one to make that bank's
  addressed CS go active (low); a disabled decoder holds all 8 of its CS lines HIGH.
- **GP7** — SN74LV595 RCLK/latch (the 595 is clocked off SPI0: SER = GP3, SRCLK = GP2).

**Decoder CS mapping (inverted):** each 74HC138 output `Yn` is wired so `Y0→CS8, Y1→CS7, …
Y7→CS1`, for both the ADC and DAC banks. So to select device **k** (1..8), drive the address
lines with **`8 − k`** and enable that bank's decoder. (e.g. ADC #1 → address 7, ADC #8 → 0.)

The drivers are header/impl pairs under `include/` + `src/`, pulled in by the PlatformIO
Library Dependency Finder (no manual lib registration): `HC138` (shared decoder selector),
`AD7193Driver`, `DAC80508`, and `XTR595`. Reference C ports live in `example/` (`ad7193.c`
is the original working Pico SDK driver the Arduino class was ported from — consult it when
behavior diverges).

`HC138` (`HC138.cpp`/`.h`) owns one decoder's 3 address pins + enable. `selectDevice(d)`
sets the address to `7 − d` and pulls the active-low enable low; `deselect()` raises the
enable. Both `AD7193Driver` and `DAC80508` take an `HC138*` and wrap every transaction in
`selectDevice` → SPI → `deselect`. main.cpp constructs two `HC138`s sharing GP20–22 with
enables GP5 / GP6.

### AD7193Driver (`AD7193.cpp` / `AD7193.h`)

Up to **8 AD7193s** share **SPI0**. Per-device chip-select comes from the **ADC 74HC138**:
to talk to device k, drive the shared address lines (GP20–22) with `8 − k`, assert
**ADC_EN (GP5)**, run the transaction, then release the enable. The decoder holds the
selected CS low only while enabled, so no shift-register trickery is needed any more.

Data-ready is found by **polling the STATUS register RDY bit** (`waitForReady`), never by
`digitalRead` on MISO. SPI is 1 MHz, Mode 3, MSB first. The ID check uses the **lower**
nibble (`& 0x0F == 0x02`). Mode/config register values are cached per-device in the public
`modeReg[]` / `confReg[]` arrays — `main.cpp` mutates these directly before calling
`writeRegister`, so keep them in sync with the hardware. Register width per address comes
from `AD7193_REG_SIZE[]`. `AD7193_NUM_DEVICES` (8) sizes the caches; the runtime `_numDevices`
ctor arg bounds the `begin()`/`resetAll()`/`readAllDevices()` loops.

**Fast multi-channel readout — `scanContinuous()`.** `MEASA?` does **not** use
`singleConversion()` per channel (that pays a fixed ~90 ms per-channel restart/settle penalty
that the filter rate can't reduce — ~3.4 s for 24 channels). Instead, each populated board is
read with **one continuous-conversion sequencer pass**: enable the 8 channels in `CONF`, run
`MODE = CONT | DAT_STA` so every sample is tagged with its channel in the appended STATUS byte,
read one sample per channel, then return to idle (clearing `DAT_STA` so later 3-byte DATA reads
stay aligned). This drops per-channel time to roughly the filter-settling term (~1.15 ms × FS),
so the whole-board scan is gated by `RATE` (FS), set via the `adcRate` global (default FS=8 ≈
11 ms/ch; the `RATE` command changes it live). A per-sample timeout of `50 + 2×FS` ms bounds a
flaky/dead board to ~that per scan instead of a fixed 1 s. Measured: ~3.4 s → ~80 ms for a
healthy 16-channel sweep. `MEAS? g` (single channel) still uses `singleConversion()`.

### DAC80508 (`DAC80508.cpp` / `DAC80508.h`)

Up to **8 DAC80508ZC devices in parallel** on **SPI1** — **not** daisy-chained. Each is
addressed individually through the **DAC 74HC138**: drive GP20–22 with `8 − k`, assert
**DAC_EN (GP6)**, then clock the 24-bit frame. The bus is **write-only** (no SDO), so
register reads, device-ID verification, and CRC read-back are not possible — anything that
relied on reading a device back has been removed (no `readRegister`/`readDeviceID`/ID check/
CRC read). A write is one 24-bit frame clocked while the device's decoder CS is low, latched
on the CS rising edge at `deselect()`. `writeRegisterAll` just loops over the populated
devices (no simultaneous write — the decoder selects one CS at a time). `begin()` configures
external reference (`REF_PWDWN=1`) and `GAIN = DAC80508_GAIN_ALL_2X` (REFDIV÷2 + 2× buffer),
giving a full `Vout = VREF × code/65535` (0–3 V) span — see the gotcha below for why REFDIV is
mandatory here.

### XTR595 — XTR_OD front-end enables (`XTR595.cpp` / `XTR595.h`)

The SN74LV595, clocked off **SPI0** (SER = GP3, SRCLK = GP2) and latched by **GP7**, drives
**XTR_OD_1..8** on outputs **QA..QH** (QA = XTR_OD_1 … QH = XTR_OD_8). **Each XTR_OD_n is a
per-daughterboard enable: it gates all 8 XTR200 front ends on daughterboard n at once** (it
is *not* a per-channel enable). The **XTR200 OD pin is active-LOW-enable**: OD **HIGH =
output disabled** (the power-on default, from the XTR200's internal pullup), OD **LOW =
output enabled**. So `XTR595::begin()` starts at `0xFF` (all disabled) and you enable by
driving the bit **low** (`setOutputs(0x00)` = all on). `main.cpp` follows the datasheet
power-up order: hold OD high while the DACs are configured, then drive OD low to enable.

**The 595 must use `SPI_MODE3`, not MODE0** — it shares SPI0 with the AD7193s and has no CS, so
it shifts on every SRCLK edge. Using MODE0 (SCK idles low) vs the ADC's MODE3 (SCK idles high)
flips the bus idle level on every ADC↔595 transition, injecting a spurious shift edge that
corrupts the register (OD then won't latch low). Matching MODE3 (the original SN74LV595-CS code's
mode) fixes it. Latch OD **once** in setup and let the output register hold it — do **not**
re-shift it on a timer in the loop, or it races the ADC's SPI0 traffic and flips. ADC traffic
shifting bytes through the 595 between latches is harmless (outputs only change on the GP7 RCLK).

## Conventions

- External 3.0 V reference on both parts; AD7193 uses REFIN2 (`AD7193_CONF_REFSEL`),
  pseudo-differential, unipolar, buffered, gain 1, with internal zero/full-scale calibration
  run at startup.
- Assert only **one** decoder enable (ADC_EN / DAC_EN) at a time, and set the GP20–22 address
  *before* asserting it. Remember the inverted mapping: address `8 − k` selects device `k`.

### Hardware/encoding gotchas (learned during bring-up)

- **74HC138/SN74LV138 G1 (pin 6) must be tied to Vcc.** It is the active-high enable; an
  output only goes low when `G1=H && ~G2A=L && ~G2B=L`. If G1 is grounded the decoder is
  permanently disabled and every CS stays high → ADC reads `0x00` on all positions / DAC never
  latches. `~G2B` (pin 5) is tied low; `~G2A` is the Pico-driven ADC_EN/DAC_EN (active low).
- **AD7193 MODE clock bits `CLK1:CLK0` (19:18): `00`=external crystal, `10`=internal 4.92 MHz.**
  The boards have no crystal, so the mode register must select internal (`AD7193_MODE_CLKSRC_INT`
  = `2<<18`). With `00` the part has no conversion clock and RDY stays high forever
  (`STATUS=0x80` conversion timeout) even though register I/O works. (The `example/ad7193.h`
  comment labeling `0<<18` as "internal" is wrong for this part.)
- **DAC80508 needs REFDIV÷2 with the 3.0 V external ref on a 3.3 V VDD.** Running GAIN with no
  ref division puts the full 3.0 V on the DAC's reference buffer, which is out of range on a
  3.3 V supply → outputs stay dead at 0 V even though SPI writes clock in fine. Use
  `GAIN = DAC80508_GAIN_ALL_2X` (REFDIV÷2 → 1.5 V reference node, then 2× buffer to restore the
  0–3 V span). This matches the TI reference driver in `dac80508-main/`.
- **DAC80508ZC = CLR variant** (pin 15 is CLR, not SDO; zero-scale reset). CLR is **active-low**
  and must be tied **high** — while CLR is low, DAC-register writes are silently ignored and
  outputs hold at zero scale. (There is genuinely no SDO, so the bus is write-only.)
- **DAC CONFIG (external-ref select) is written twice** in `begin()`. The bus is write-only, so a
  single marginal frame can leave `REF_PWDWN=0` → DAC stays on the internal 2.5 V reference (VREF
  pin reads 2.5 V instead of 3.0 V; outputs scale ÷1.2). Double-writing CONFIG makes external ref
  reliably take. Symptom of it not taking: VREF = 2.5 V, DAC midscale ≈ 1.25 V instead of 1.5 V.
- **The AD7193 AIN1 input has a 6:1 divider** in front of it (scales the sense range into the
  0–3 V ADC window). So true sense voltage = `ADC_V × 6`, and channel current = `ADC_V × 6 / Rload`
  (e.g. ADC reads 0.426 V → 2.54 V across 820 Ω → 3.1 mA). The raw ADC reading is correct; apply
  the ×6 when reporting real volts/amps.
- **The 6:1 sense divider (100k + 20k = 120 kΩ) steals current from the load — correct for it.**
  The divider sits across the output node, so it draws `I_div = V_heater / 120kΩ`, equivalently
  `I_div = ADC_V / 20kΩ` (same current, seen across the 20 k bottom leg). The XTR200 sources the
  commanded current accurately (datasheet `RO = 47 GΩ`, essentially ideal — it is NOT the source
  of any droop), but that current splits between the heater and the divider:
  `I_heater = I_source − V_heater / 120kΩ`. Bench-measured this is ~0.68 % at 0.81 V (1 mA into
  811 Ω) and scales with V_heater, so it matters for accurate heater current / R / power. **It is
  exact and known a-priori (it's the on-board divider), not a calibrated parameter** — subtract it
  analytically using the live measurement; do not fold it into `calSlope`/`calOffset` (it is
  load-dependent, not a fixed current). Earlier this droop was mis-attributed to XTR200 output
  impedance; the datasheet's 47 GΩ rules that out — it is the divider.
- **XTR200 front-end accuracy (bench-measured 2026-06-17 vs Keithley, datasheet-confirmed).**
  - *Gain*: near-ideal (datasheet span error ±0.065 % max, nonlinearity ±0.003 %); the
    `calSlope = 0.47 V/mA` transconductance seed needs no per-channel trim — treat slope as a
    shared constant.
  - *Offset (`IOS`)*: a real per-part offset current, datasheet ±2 µA typ / **±10 µA max** (spec'd
    at both `VIN = 0` and `IOUT = 4 mA`), bench-measured ±~4 µA, both signs. This is the one term
    that genuinely needs per-channel measurement → it is exactly what `calOffset` captures, and it
    equals each channel's enabled zero-command reading, so the offset table can be built from a
    single zero reading per channel (no full sweep). Caveat: a channel may have a *negative*
    extrapolated offset while its actual current bottoms out near 0 (a current source can't sink) →
    the linear cal is excellent over the 0.25–6 mA working range but unreliable sub-100 µA; do not
    calibrate on a near-zero point. NB this enabled-state `IOS` (µA) is NOT the datasheet `ILEAK`
    (output-*disabled* leakage, 0.35 nA).
- **The DAC80508 outputs are scrambled relative to the physical channels; the AD7193 inputs are
  not.** The global index `g` is defined by the **physical channel** (`g % 8`). The AD7193 inputs
  are wired sequentially (ADC input n senses physical channel n), so the read path
  (`measureChannel`, `buildMeasureLine`) is **identity**. The DAC outputs are permuted:
  `out0→ch1, out1→ch3, out2→ch5, out3→ch7, out4→ch6, out5→ch4, out6→ch2, out7→ch0`. So only the
  DAC drive is remapped, through the inverse table `DAC_CH_FOR_PHYS = {7,0,6,1,5,2,4,3}` in
  `writeChannel()` (physical channel → DAC output). All daughterboards share the layout, so one
  table serves all.
  - Diagnosing a wrong table: `ISET g` drives current but the voltage develops on a *different*
    physical channel. **The bug is always on the DAC side, never the ADC side** — the ADC is
    sequential, so a readback always tells you the true physical channel. Beware: a swap on the
    *read* path can make the on-screen display look self-consistent (a swapped read cancels a
    swapped drive *on screen*) while the actual current is on the wrong heater — so verify with a
    physical probe / source meter, not just the GUI, when fixing this table.
- Voltage conversions are static helpers (`codeToVoltage` / `voltageToCode`) — use them
  rather than recomputing scale factors inline.
- Register bit names are macros in the headers; prefer composing them (e.g.
  `AD7193_CONF_PSEUDO | AD7193_CONF_UNIPOLAR | ...`) over raw hex.

