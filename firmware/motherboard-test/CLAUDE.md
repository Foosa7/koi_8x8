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

Standard PlatformIO workflow (`pio run` / `-t upload`; `pio device monitor` at 115200).

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
| `*IDN?` | identify | `KOI,8x8,fw1.3` (the device is named **Koi 8×8**; pre-rename firmware replies `MOBO,…` and the GUI accepts both). **Bump this string whenever firmware behaviour changes** — the boot banner is byte-identical across builds, so `*IDN?` is the only way to tell which binary is actually running |
| `*RST` | all currents 0, front-ends disabled | `OK *RST` |
| `ISETA i0 … i63` | set all 64 currents (mA, g-order) | `OK ISETA` |
| `ISET g mA` | set one channel | `OK ISET <g>` |
| `MEASA?` `[mask]` | measure all 64 (or masked boards) | `v0,…,v63` |
| `MEAS? g` | measure one channel | `<volts>` |
| `XTR enmask` | bit b=1 → board b front-ends enabled | `OK XTR 0x..` |
| `AVG n` | on-micro averages per measurement (1..64); multiplies scan time ×`n`, cuts white noise ~√`n` (orthogonal to `RATE`) | `OK AVG <n>` |
| `RATE fs` | AD7193 filter word (speed/noise, 1..1023); drives **both** the fast scan and `MEAS?`'s single conversion. **`fs` = 8 is fine** — verified 2026-07-30 (see below); the earlier "fs < 16 returns code 0" finding does **not** reproduce | `OK RATE <fs>` |
| `GAIN g` | AD7193 PGA gain (`1\|8\|16\|32\|64\|128`); folded into the reported voltage so it stays the true ADC-pin V. Runs a **zero-scale-only recal** at the new gain — the AD7193 offset register is gain-dependent, and a stale gain-1 offset applied at higher gain over-subtracts and clamps the unipolar reading to **0** (the symptom if you skip it). Full-scale is NOT re-run (valid at gain 1 only; boot coeff kept). Use gain 1 for the full 0–3 V heater span; step up to resolve small signals (max heater = 18/gain V) | `OK GAIN <g>` |
| `CHOP ON\|OFF` | chopper offset/drift cancellation (halves throughput; recalibrates) — a measure-side knob for the residual 0 mA offset | `OK CHOP ON\|OFF` |
| `FILTER SINC3\|SINC4` | digital filter type: SINC3 faster-settling, SINC4 (default) better rejection/noise | `OK FILTER SINC3\|SINC4` |
| `REJ60 ON\|OFF` | simultaneous 50/60 Hz notch rejection (slower, kills mains hum) | `OK REJ60 ON\|OFF` |
| `BIPOLAR ON\|OFF` | input polarity: OFF = unipolar 0..FS (default, normal heater use); ON = bipolar ±FS (midscale = 0 V) so a small **negative** residual reads as a signed value instead of clamping at 0 — needed to see the offset/noise floor with no signal (e.g. XTR off) at high gain | `OK BIPOLAR ON\|OFF` |
| `BUF ON\|OFF [CAL]` | AD7193 input buffer (**fw1.3**). `ON` is the only setting valid for normal use — the 6:1 divider is a ~16.7 kΩ source and needs a buffered input. `OFF` is for the `docs/measure-path-offset.md` §5 A/B and requires an SMU driving the pin directly on a desoldered channel. **No recal by default** — whenever `BUF OFF` is legitimately in use the input is being forced to a non-zero voltage, which a zero-scale cal would silently absorb into the offset register. Add the explicit `CAL` argument, with the input at 0 V, to refresh it (still skipped while bipolar) | `OK BUF ON\|OFF` |
| `ADC?` | read back the live sampling settings | `OK ADC rate=.. avg=.. gain=.. chop=.. filter=.. rej60=.. polarity=uni\|bi buf=0\|1` |
| `STREAM ON\|OFF` | periodic `# …` dump for eyeballing | `OK STREAM ON\|OFF` |
| `RESCAN` | re-detect boards at runtime (resyncs SPI + reconfigures as needed) | `OK RESCAN active=0x.. new=0x..` |
| `PING? b` | board liveness: ADC ID + one real conversion, non-mutating (`0` for an undetected board — RESCAN adopts it) | `OK PING <b> 1\|0` |
| `DACINIT [b]` | rewrite DAC config (soft reset → ext ref → REFDIV÷2 gain) + reload setpoints, board `b` or all populated — recovery for a DAC brownout back to internal 2.5 V ref (host detects: >1 V raw at 0 mA; the write-only bus can't) | `OK DACINIT 0x<mask>` |
| `CAL [b]` | run AD7193 internal zero/full-scale calibration, board `b` or all populated — front-ends forced **off** across the cal (via `configureBoard`) so no XTR200 offset current biases the zero-scale point, prior enables restored after | `OK CAL 0x<mask>` |
| `CALCLR [b]` | clear the user calibration: `reset()` the ADC(s) back to factory offset/full-scale + rewrite config, board `b` or all populated — A/B against `CAL` to isolate whether the residual 0 mA offset is cal-side or PCB/measure-side | `OK CALCLR 0x<mask>` |
| `ERR?` | snapshot all 64 XTR200 ERRORFLAG pins via the SN74LV165 chain; bit `g` = RAW pin level (host applies polarity: open-drain EF, 0 = fault). Bits of unpopulated boards are garbage (a missing board breaks the chain) — mask by the active set | `OK ERR 0x<16 hex>` |

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

> ## ⚠ THE 2026-07-29 CAMPAIGN IS SUPERSEDED — use `bench/20260730/`
>
> That campaign assumed the load was **1005.68 Ω**; it is **996.4 Ω** (−0.92 %), so every
> quantity derived by dividing a measured voltage by R was wrong, and the drive gain error had
> the wrong **sign**. It has been re-run in full. **Do not quote any 2026-07-29 number**, and
> note two of its stated conclusions do not reproduce at all (RATE<16 broken; noise floor at
> 96/AVG 4) — both are corrected in place elsewhere in this file.
>
> ### Authoritative results — 2026-07-30, ch0, `bench/20260730/` (8/8 clean)
>
> | Quantity | Value | Depends on R? |
> | --- | --- | --- |
> | Drive gain error | **+0.319 %** | **yes** — provisional |
> | `I_OS` | **+2.43 µA** | weakly |
> | Residual after gain+offset cal | 0.070 µA rms = **0.0011 % FS** | weakly |
> | DNL (96 steps @ 1/3/5 mA) | max **0.40 LSB**, 0 non-monotonic | no |
> | INL randomized (48×2) | rms **0.64 LSB**, max −1.31 LSB | no |
> | INL monotonic (48) | rms **0.60 LSB**, max −1.51 LSB | no |
> | Sense ratio / offset (above knee) | **5.9883** / **−3.752 mV** | no |
> | Sense ratio below the 44 mV knee | **11.696** (intercept −0.165 mV) | no |
> | R reported, calibrated sense, I≥2 mA | 1000.27 ± 0.24 Ω (**+0.389 %**) | yes |
> | Settable current levels | **65 536** per channel, 1 LSB = 97.398 nA | no |
>
> **R is still a placeholder (996.4 Ω, measured off-jig).** The 2100's SENSE pair is not
> connected, so `Measure R (4-wire)` refuses to run. Everything that scales with R is therefore
> provisional — but raw volts are stored, so fixing R is `plot_paper_figs.py --rload <new>`,
> not a re-run.
>
> **The blower fan works.** It holds the load at ambient, and the randomized and monotonic INL
> runs now **agree** (0.64 vs 0.60 LSB rms) where the monotonic ramp previously read ~60 % high.
> Self-heating is removed at the source rather than worked around, so randomized order is now a
> *check* rather than a requirement. Both are still taken.
>
> **The R-bias sign flip is self-consistent.** Old: −0.372/−0.393/−0.412 % with ±0.87/±0.49/±0.27 Ω
> scatter. New: +0.426/+0.406/+0.389 % with ±0.80/±0.45/±0.24 Ω. Same magnitudes, same scatter,
> mirrored sign — exactly what a −0.92 % error in R predicts. The scatter (the real figure of
> merit) is unchanged at 0.02–0.05 %.

**Superseded detail from 2026-07-29 follows (ch0, R assumed 1005.68 Ω).** Kept only for the
method notes; every number in it is wrong by the R error above. Reference load is a nominal
1 kΩ **±1 %, 100 mW** part — always measure it, never assume the nominal, and re-measure rather
than trusting a recorded value, which is exactly the failure above. Full scale 6.383 mA =
**41 mW, 41 % of rating**, so it cannot be overrun.
- *Drive (set vs actual current):* `I_actual = 0.99519·I_cmd + 2.45 µA` (randomized-order fit;
  three independent sweeps agree to ±0.004 %). **Two error terms only:** a **−0.481 % gain
  error** (`calSlope` 0.47 → **0.47226**) and `I_OS` = **+2.45 µA**. Uncalibrated, worst
  deviation is **28.4 µA = 0.445 % FS** (rms 16.0 µA); after a 2-parameter gain+offset cal the
  residual drops to **0.098 µA rms / 0.218 µA worst = 0.0034 % FS** — a ~130× improvement, and
  what is left is genuine converter INL, not something more calibration removes.
  - **The offset and gain cancel at 0.510 mA.** Below that the channel reads *high*, above it
    *low*: **+1.97 % at 0.1 mA**, +0.75 % at 0.2 mA, ~0 at 0.5 mA, −0.24 % at 1 mA, asymptoting
    to −0.44 % at full scale. Relative error is worst at the *bottom* of the range — the thing
    to watch for fine phase trim near zero, and not visible in a % FS figure.
  - Gain is expected to be a shared constant, but **`I_OS` is per-part** (±4 µA bench, ±10 µA
    datasheet), so +2.45 µA is channel 0's alone; every channel needs its own.
  - ~0.012 % of that gain error is **load self-heating**, not the driver (fits over restricted
    ranges: −0.469 % at 2.3 mW → −0.481 % at 41 mW), since the 4-wire 1005.68 Ω was measured
    cold. So the `calSlope` trim carries ~0.01–0.02 % uncertainty.
  - Plot: `paper/figures/current-accuracy.png` (transfer, error, residual) via
    `tools/plot_current.py`.
- *Sense:* `V_node = (V_ADC + 3.810 mV) × 5.9909`, residual **67 ppm of FS** (max −423 µV) after
  that 2-parameter cal, vs **0.36 % FS** using the nominal ×6 with no offset.
- *Resistance:* R from the Koi reads **−0.4 %** vs the 4-wire truth — that bias is the *drive*
  gain error (R uses commanded current), not the sense path. The real figure of merit is the
  **±0.03–0.05 % scatter**, which is what remains once `calSlope` is trimmed.
- *Measurement resolution:* **1.15 µV** 1σ at the ADC pin at RATE 96/AVG 4 (6.9 µV at the
  load, ~7 nA). **Superseded 2026-07-30** — the "floor at 96/4" does not reproduce with the
  blower fitted; noise now falls monotonically to 1.72 µV at 96/16. See the `RATE`/`AVG` section.
- *Drive resolution:* 1 DAC code = **97.398 nA** = 97.95 µV across 1 kΩ; 65536 codes over
  0–6.383 mA. **DNL ±0.26 LSB and monotonic** (0 non-monotonic steps in 96, every run).
- *INL:* **rms 1.01 LSB, max −2.24 LSB = 0.0034 % FS → 14.8 effective bits** — but **only when
  measured in RANDOMIZED code order** (`tools/inl_random.py`, 48 codes shuffled + a reference
  point re-measured every 4 points for drift correction). A monotonic ramp reads −3.73 LSB,
  ~60 % high, because **load self-heating is confounded with code order** (0→40 mW across the
  sweep; a 50 ppm/°C load warming a few °C drifts ~250 ppm). Demonstrated: the 5 mA single-code
  step reads 94.84 nA when ramped up from 3 mA but **97.9 nA after a 60 s soak**. Quote the
  **rms** (stable to 2 % over three seeds), not the max (an extremum, scatters 2.07–2.40 LSB).
  The residual is real converter INL, not heating — **the correct thermal predictor is I³, not
  I²** (ΔT ∝ I²R, but reported current is V/R_assumed, so the current error ∝ I³), and even I³
  explains only R² = 0.148. Decisive: attributing the whole INL to self-heating would need a
  load tempco of **1–4 ppm/°C**, which no ±1 % film resistor achieves.
- *Settling:* **0.8 s per point** is converged (0.8 s and 1.5 s agree to 0.12 nA); 0.3 s
  biases the mean step ~1.5 % low.
- The sense path is only linear **above a knee at node ≈44 mV**; below it the reading is not
  trustworthy (`docs/measure-path-offset.md` §5A).

**Bench tooling against the Keithley 2100** (`05e6:2100`, on `/dev/usbtmc*`). Run these in the
jax venv (`source /home/foosa/jax-env/jax_env/bin/activate`) with the **GUI closed** — the Koi
serial port is exclusive. Access needs a udev rule; see `tools/setup_usbtmc.md`.

**Every acquisition writes into `tools/bench/<YYYYMMDD>/`**, via `koi_bench.bench_outdir()`.
`tools/` itself holds only scripts. One dated folder per campaign is what makes "which run is
this number from?" answerable from the path alone — the question that the superseded-campaign
banner above exists because nobody could answer. Runs before 2026-07-30 were filed
retroactively (`bench/20260616/` … `bench/20260729/`), so `--random` / `--ramp` style defaults
point into those folders, not the cwd.

- `tools/keithley2100.py` — SCPI over the kernel USBTMC node, **no pyvisa needed**. VID/PID
  autodetect (the node number is not stable across replugs), `read_burst()` for fast
  multi-reading transfers, and stall recovery (the 2100's USBTMC interface hangs roughly once
  per few hundred readings; a device CLEAR is not always enough, a reopen is).
- `tools/vsweep_dmm.py` — current sweep logging Koi and 2100 together; fits
  `koi_raw = V_node/ratio + offset`.
- `tools/dac_char.py` — current-source characterization using **only** the 2100 (full-range
  accuracy plus single-DAC-code steps for DNL/monotonicity). **Its part-A INL is the
  monotonic-ramp one — do not quote it**; use `inl_random.py`.
- `tools/inl_random.py` — **the INL measurement that is actually valid**: randomized code order
  with an interleaved reference point for drift correction. ~2 min per run. Queries `SYST:ERR?`
  after every point and flags overload / short-burst separately from SCPI-queue noise.
- `tools/resolution_test.py` — ADC noise floor vs `RATE`/`AVG`.
- `tools/plot_current.py` — transfer / error / residual figure + printed error summary, from
  the `inl_random.py` CSV. Writes `paper/figures/current-accuracy.png`.
- `tools/plot_paper_figs.py` — the manuscript's Section 7 figures (transfer, deviation,
  linearity, precision, sense knee) from the bench CSVs into `paper/figures/`.

**`tools/koi_bench.py` + the GUI's Bench row — the paper validation runs.** These supersede
the standalone `dac_char.py` / `inl_random.py` / `resolution_test.py` scripts for new data:
same measurements, but driven from inside `koi_gui.py` so the Koi serial port and the USBTMC
node each keep a single owner and the GUI's `MEASA?` poll + DAC-brownout watchdog are
suspended for the duration (both otherwise fight a run that is deliberately holding a
setpoint). The eight buttons are `Measure R (4-wire)`, `Set vs 2100`, `Drive sweep`,
`Low-current sweep`, `Single-code steps`, `INL`, `Noise vs RATE/AVG`, `Settling`; a `Stop`
aborts at the next point without leaving the channel driven.

Conventions worth keeping:
- **R comes from the meter, not the keyboard.** `Measure R (4-wire)` (front-ends off,
  offset-compensated ohms) fills the R field and every other button refuses to run until it
  has one. Each CSV records R *and how it was obtained* in a `#` header block, alongside
  channel, live `ADC?` settings, both instrument IDNs and the timestamp — read them back with
  `koi_bench.load_bench_csv()` (or pandas `comment='#'`).
- **Raw volts are always a column**, so any later correction to R is a re-analysis rather than
  a re-acquisition. This is the direct fix for the invalidation described above.
- Exact DAC codes are commanded as `mA = code × VREF/(calSlope×65535)`; that inverse assumes
  the firmware still holds the ideal seed, so **trimming `calSlope` means updating `CODE_MA`
  in `koi_bench.py`** or the code-level runs land between codes.

**Always check the DMM error queue during acquisition, not just after config** — only
`inl_random.py` does this by default. Expect ~one `-113 "Undefined header"` per ~130 readings
(an artifact of the USBTMC stall recovery re-issuing commands mid-`READ?`); that one is
cosmetic, but an **overload (>1e30) or short burst is fatal to the run**.

**2100 speed:** use **NPLC 1 with autozero OFF** — one reading gives 0.1 LSB, 0.025 s/point,
~80× faster than 10 reads at NPLC 10 for the same precision. NPLC 10 is 10× slower for
identical noise. **The 2100 silently coerces NPLC** (0.02→1, 0.2→10, 100→10, no SCPI error), so
trust measured throughput over the request or the readback. Full detail:
`docs/characterization.md` §5B.

`tools/koi_gui.py` is a Tkinter monitor that polls `MEASA?` and shows raw + divider-corrected
voltage for all 64 channels in an 8×8 grid (`pip install pyserial`; auto-detects the port).
It RESCANs automatically at connect (recovers boards the boot-time detect missed — flaky HASL
contact) and auto-re-RESCANs (rate-limited to 1/10 s) when a previously seen board stops
answering, so boards reappear without a manual Rescan click. The **ADC settings bar** exposes
the AD7193 sampling knobs — RATE (FS), AVG, PGA gain, filter type (SINC3/SINC4), CHOP, REJ60,
Bipolar, and **Buffer** (fw1.3) — each sending its command on change; at first connect the GUI
pulls the live values via `ADC?` into the controls (so they reflect the device, not just
defaults).
Unchecking **Buffer** raises a confirmation first: unbuffered mode is only valid with an SMU on
the pin, and through the divider it times out the cal and pins every channel at exactly
`0.000000` — a plausible-looking number that is not a measurement. Pre-fw1.3 firmware omits
`buf=` from `ADC?`, in which case the control stays at its ON default. Each poll cycle also sends one
`ERR?`: a faulting channel (XTR200 ERRORFLAG asserted — compliance rail / open load) turns
red with an "EF!" tag and is listed in the control bar; polarity lives in
`ERRFLAG_ACTIVE_LOW` (raw pin low = fault). Unpopulated boards' flag bits are ignored, and
pre-errorflag firmware just shows "EF: n/a" (the GUI stops sending `ERR?` after one
"unknown command"). It also watches for the DAC-brownout signature — a populated channel
at 0 mA reading raw > 1 V (`DAC_FAULT_RAW_V`) — and auto-sends `DACINIT <b>` (rate-limited,
max 3 tries per episode, suppressed during sweeps/cals whose currents `setpoint_ma` doesn't
track); the "DAC init" button fires it manually for all boards.

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

Two hardware SPI buses, two **74HC138** 3-to-8 decoders for chip select, one
**SN74LV595** shift register for analog-front-end enables, and one **SN74LV165**
parallel-in shift register per daughterboard reading the 8 XTR200 ERRORFLAG pins.
The 165s are daisy-chained board1→…→board8 and bit-banged on dedicated GPIOs —
**CP = GP16** (shift clock), **Q7 of board 8 = GP17** (serial data in), **PL = GP18**
(parallel load, active low) — deliberately *not* on SPI0, so reading flags can never
disturb the ADC/595 bus. `readErrorChain()` in `main.cpp` pulses PL then clocks 64
bits; they arrive g = 63 → 0 (board 8 input H first). `tools/err_flags.py` prints the
8×8 fault grid (run it in the jax venv: `source /home/foosa/jax-env/jax_env/bin/activate`).

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
ctor arg bounds the `begin()`/`resetAll()` loops.

**Fast multi-channel readout — `scanContinuous()`.** `MEASA?` does **not** use
`singleConversion()` per channel (that pays a fixed ~90 ms per-channel restart/settle penalty
that the filter rate can't reduce — ~3.4 s for 24 channels). Instead, each populated board is
read with **one continuous-conversion sequencer pass**: enable the 8 channels in `CONF`, run
`MODE = CONT | DAT_STA` so every sample is tagged with its channel in the appended STATUS byte,
read one sample per channel, then return to idle (clearing `DAT_STA` so later 3-byte DATA reads
stay aligned). This drops per-channel time to just the filter-settling term, so the whole-board
scan is gated by `RATE` (FS), set via the `adcRate` global (default FS=16; the `RATE` command
changes it live). A per-sample timeout of `50 + 2×FS` ms bounds a flaky/dead board to ~that per
scan instead of a fixed 1 s. `MEAS? g` (single channel) still uses `singleConversion()`.

**Measured timing vs `RATE` (FS).** The output data rate is `ODR = 4.92 MHz / (1024 × FS)`, but
per-channel time is **settling-limited, not 1/ODR**: SINC4 settles in ~4 conversion periods, so
per-channel ≈ **0.83 ms × FS** (matches `MEAS?` and the per-channel scan term exactly on
hardware). Bench-measured 2026-07-23 (8 populated boards, AVG 1, SINC4, chop off):

| FS | ODR | per channel | per board (8 ch, one `MEASA?` mask) | full grid (64 ch) |
| --- | --- | --- | --- | --- |
| 8 | 600 Hz | 8 ms | 0.06 s | 0.45 s |
| 16 (default) | 300 Hz | 16 ms | 0.11 s | 0.9 s |
| 96 | 50 Hz | 80 ms | 0.64 s | 5.1 s |
| 240 | 20 Hz | 0.20 s | 1.6 s | 12.7 s |
| 480 | 10 Hz | 0.40 s | 3.2 s | 25 s |

CHOP roughly doubles these (two conversions/sample); SINC3 settles in ~3 periods instead of 4.

**Full-grid throughput measured 2026-07-30** (8 populated boards, all 64 channels returning
valid data at every setting — the FS=8 row above is confirmed, not invalid):

| RATE | AVG | full grid (64 ch) | per board | grid refresh |
| --- | --- | --- | --- | --- |
| 8 | 1 | **0.46 s** | 0.064 s | 2.17 Hz |
| 8 | 4 | **1.74 s** | 0.224 s | 0.57 Hz |
| 16 | 4 | 3.43 s | 0.436 s | 0.29 Hz |
| 32 | 4 | 6.82 s | 0.860 s | 0.15 Hz |

Per-board × 8 reproduces the full grid to ~2 % at every row, so the scan really is
settling-limited with no fixed per-board overhead worth chasing. **Above ~RATE 48 / AVG 4 the
full grid exceeds `KoiLink.command()`'s 10 s deadline** and the measured time becomes a
timeout→resync→retry artifact rather than a scan time — the GUI's per-board polling stays well
clear, but don't quote whole-grid numbers taken above that.

**`AVG n` (1..64) — on-micro averaging.** Orthogonal to `RATE`: it repeats the whole measurement
`n` times and averages (`MEASA?` runs `n` scan passes per board; `MEAS?` averages `n` single
conversions), so it **multiplies every time in the table above by `n`** — total ≈
`0.83 ms × FS × channels × n`. In return it cuts uncorrelated (white) noise by ~**√n**, but only
down to a noise floor.

**Measured 2026-07-30** (ch0, 1 mA, 30 reads/setting, 5 discarded after each change — the
first conversions after a `RATE` change are invalid), via `koi_bench.meas_noise`:

| RATE | AVG | t/read | 1σ at ADC pin | 1σ at load (×6) | 2100's own 1σ |
| --- | --- | --- | --- | --- | --- |
| 16 | 4 | 56 ms | 11.36 µV | 68.2 µV | 8.82 µV |
| 96 | 1 | 81 ms | 5.29 µV | 31.8 µV | 6.29 µV |
| 96 | 4 | 320 ms | 3.08 µV | 18.5 µV | 10.45 µV |
| 96 | 16 | 1276 ms | **1.72 µV** | 10.3 µV | 11.94 µV |
| 240 | 4 | 796 ms | 2.20 µV | 13.2 µV | 7.92 µV |

Plus, from the RATE-validation run at 2 mA: **RATE 8 / AVG 4 = 29 ms/read, 30.5 µV 1σ at the
pin** (~183 µV at the load, ~184 nA equivalent).

Timing is linear in `n` and matches `0.83 ms × FS × AVG` exactly.

Noise falls **monotonically** across the whole grid — 11.36 → 5.29 → 3.08 → 1.72 µV at ×1.75
per ×4 averaging, i.e. essentially √n — with **no floor reached** up to AVG 16. Two consequences:

- There is no "stop averaging here" point established by these data. If a floor matters for a
  claim, it needs a sweep past AVG 16.
- `AVG` is the better lever, not `RATE`: at ×1.6 the time cost, 96/AVG 16 (1276 ms, 1.72 µV)
  beats 240/AVG 4 (796 ms, 2.20 µV).

`avgCount` global; default 4.

**Default sampling profile: `RATE 16`, `AVG 4`** (gain 1, unipolar, SINC4, chop off) — the
live-monitoring balance from the settings analysis (~0.9 s full-grid refresh, quieter than the
old FS=8/AVG-1). Set in both the firmware boot globals and the GUI controls so a fresh connect
shows and uses them. Raise `RATE`/`AVG` + `CHOP` for deliberate low-noise single-channel reads;
`GAIN`/`BIPOLAR` for small-signal work.

The GUI polls **one board per `MEASA?`** (`_poll_once` → `measure_mask`),
so at high FS a whole board's cells refresh together every ~(per-board) seconds and the full
grid cycles in ~(full-grid) seconds — high FS is for careful low-noise reads, not live grid
refresh. Note `KoiLink.command()`'s 10 s deadline: a **full-grid** `measure_all()` (only used at
connect/rediscovery) exceeds it above FS≈190 and will time out→resync→retry; steady-state
per-board polling stays well under it.

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
  run at startup. **Caveat on "buffered":** with `BUF` on, the AD719x absolute input range is
  ~AGND+250 mV to AVDD−250 mV and that applies to **AINCOM too**, which is tied to AGND here, so
  the part runs out of spec by construction. This was long the prime suspect for the measure-side
  error, but that hypothesis is **REFUTED** (2026-07-29, `docs/measure-path-offset.md` §5) — see
  the sense-path gotcha below for what the error actually is. `BUF` is host-tunable as of
  **fw1.3** (`BUF ON|OFF`, default ON) — no reflash needed. Leave it on for normal use anyway — the 16.7 kΩ divider source impedance needs a
  buffered input, and unbuffered mode is only valid with a low-impedance source (an SMU) driving
  the pin directly.
- Assert only **one** decoder enable (ADC_EN / DAC_EN) at a time, and set the GP20–22 address
  *before* asserting it. Remember the inverted mapping: address `8 − k` selects device `k`.

### Hardware/encoding gotchas (learned during bring-up)

- **⚠ The SPI bus assignment is fixed by the wiring — never swap it. ADC = SPI0
  (GP2 SCK, GP3 MOSI, GP4 MISO), DAC = SPI1 (GP10 SCK, GP11 SDI).** In `src/main.cpp`
  that is `adcSpi(4, 3, 2)` and `dacSpi(12, 11, 10)` (the `MbedSPI` ctor is
  `(miso, mosi, sck)`). Getting these backwards points the ADC read at **GP12**, a pin
  connected to nothing, and every AD7193 reads back `0x00`.
  **Symptom: all 8 boards print `# AD7193 #n ID mismatch: 0x0` at boot, with or without
  daughterboards plugged in.** Cost a full afternoon on 2026-07-31; see the post-mortem
  below.

- **⚠ A committed-but-never-flashed change is a landmine. The board runs the *binary*,
  not the repo.** The bus swap above was committed 2026-07-25 as a debugging experiment
  ("move the ADC read onto fresh pins"), including a source comment instructing the reader
  to *rewire the daughterboard bus to match*. It was never flashed, so the Pico kept running
  the older good binary — and the **entire 2026-07-29 and 2026-07-30 validation campaigns
  ran fine on top of a source tree that could not work**. The fault appeared six days later
  on the next unrelated reflash, with nothing in that day's edits to blame. Rules that fall
  out of this:
  - **Never commit a pin-map or bus experiment to a working branch without flashing and
    testing it.** Keep it in the working tree, or on a branch you never merge.
  - **`*IDN?` is the only version fingerprint.** The boot banner is byte-identical across
    builds, so you cannot tell which binary is running by reading it — the pasted banner
    from a broken build looks exactly like a good one. Bump the `fw1.x` string in `*IDN?`
    on every behavioural change, and check it *before* trusting any other diagnosis.
  - When source and hardware disagree, **date the artifacts**:
    `ls -l .pio/build/pico/firmware.*` against `git log` on the suspect lines. A build
    timestamp newer than the last known-good session is the tell.

- **When N identical devices fail identically and simultaneously, it is never N failures.**
  Eight AD7193s reading `0x00` is one shared-path fault. The shared set here is: SPI0
  (GP2/3/4), ADC_EN (GP5), the address lines GP20–22, the 74HC138 and its G1 pin, and the
  3.3 V / 3.0 V rails. Enumerate that list and bisect it before ever suspecting the parts.
  Useful discriminators, cheapest first:
  - **`ERR?`** — the SN74LV165 chain is bit-banged on GP16/17/18 and touches neither SPI nor
    the decoders, so it shares only power and seating. Plausible data ⇒ boards are alive and
    the fault is in the bus/CS path; all-`0`/all-`1` ⇒ power or seating.
  - **`PING? b` immediately after boot is unreliable** — the boot zero/full-scale calibration
    is still running and `PING?` reports `0` on a perfectly good board while it is. Measured
    2026-07-31: still `0` at 1.5 s after `# READY`, reliably `1` from ~13 s after boot.
    **Allow ≥10 s past `# READY`** before trusting a `PING?`, or you will chase a ghost.
  - **`MISOPROBE b [ms]`** pulses ADC_EN in a tight loop for scoping GP4/GP2/GP5. **Its
    verdict is currently untrustworthy** — it reported "SPI0 read path dead" against a board
    that `PING?` and `MEASA?` both read correctly seconds later (2026-07-31, unresolved).
    Use it as a scope trigger, not as an oracle.


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
- **The divider ratio is nominal — the residual error is a low-voltage GAIN deficit, not an
  offset.** Bench-measured 2026-07-25 with the XTR200 *desoldered* and a Keithley 2410 forcing
  the sense node (2100-verified): `raw = V_node/6.0017 − 3.372 mV`. So the host's fixed **×6.0 is
  correct** — do not "correct" the divider, and do not let the GUI's "Sweep + cal" adjust the
  ratio (the earlier ÷5.990 figure was an artifact of measuring through the XTR200). Everything
  outside the AD7193 is eliminated by measurement (DAC 0.03 %, VREF 2.9998 V, load 999.8 Ω
  4-wire, AINCOM and SMU LO both 0 mV to local ground).
  **The "~3.4 mV additive offset" framing is wrong** (resolved 2026-07-29, §5A): the low-current
  sweep shows **two regimes** meeting at **node 44 mV / pin 7.3 mV** — below it
  `pin = node/11.729 − 0.157 mV` (intercept ≈ **zero**, but only *half* the correct gain), above
  it `pin = node/5.992 − 3.748 mV` (correct slope, r² = 1.0000000). The path reaches the knee
  ~3.6 mV short and then tracks correctly forever, so anything sampled only above 0.05 mA — every
  earlier dataset here — is indistinguishable from a perfect fixed offset. **Below the knee the
  reading is not trustworthy.**
  Implied mechanism: a current drawn from the ADC pin, ohmic (~17.4 kΩ) below ~3.9 mV and
  **saturating at ~225 nA** above it (225 nA × 16.67 kΩ = 3.75 mV) — the signature of a leaky
  junction / ESD structure, ~200× the AD7193's buffered input-leakage spec. An ohmmeter cannot
  see it, which is why the power-off checks looked textbook.
  **The buffered common-mode hypothesis is REFUTED**: its sharp prediction was a knee at
  pin = 250 mV, and the per-point offset runs flat straight through it (−3.708 mV at pin 5 mV →
  −3.780 at 250 mV → −3.750 at 494 mV). Note the diagnostic split: CHOP removes the +0.29 mV
  zero-point artifact (→0.001 mV) but does **nothing** to the 3.4 mV at signal.
  Decisive next test: DMM the **ADC pin itself** across the low-current points — if the pin shows
  the 11.73 → 5.99 knee it is board/ESD leakage (fixable upstream); if the pin is a clean 6:1 and
  only the code knees, it is inside the AD7193. The buffer A/B (§5's other test) is now one
  command, `BUF OFF` (fw1.3) — but it cannot be run through the divider; see the `BUF OFF`
  entry under the XTR200/sense gotchas.
  **Do not derive this offset by forcing R to read exactly 1 kΩ** — that single-point method
  assumes the resistor is 1000 Ω (it is 999.8), assumes commanded current equals actual current
  (XTR200 IOS is ±4 µA = ±0.4 % at 1 mA, per-channel), and cannot separate offset from gain;
  it yields a catch-all that differs at every current and channel. Full record, data, decisive
  tests and bench gotchas: **`docs/measure-path-offset.md`**.
- **⚠→✅ Driving the ADC pin directly collapses the error ~12× — the leakage mechanism is
  CONFIRMED and a buffer fixes it (2026-07-31).** Channel 0 desoldered, SMU forcing **100 mV
  straight onto the AD7193 pin** (bypassing the 6:1 divider entirely), `BUF OFF`: the residual
  offset is **≈0.3 mV**, against the **3.75 mV** seen through the divider. That is the sharp
  prediction of the §5 leakage model — a current drawn from the pin develops
  `I_leak × Z_source`, so removing the 16.7 kΩ source impedance must shrink the error roughly in
  proportion, and it does. It is **not** consistent with an error internal to the AD7193's
  conversion, which would be indifferent to source impedance.
  **Consequence: buffer the ADC input.** A unity-gain buffer between the divider and the pin
  presents ~milliohms instead of 16.7 kΩ, so the leak develops essentially no voltage. See the
  op-amp selection note below.
  Caveat on the 0.3 mV: single point, and it does not separate gain from offset. Two or three
  more points across 0–500 mV on a clean cal would pin that down — but the ~12× collapse is far
  larger than any plausible uncertainty in the one measurement, so the conclusion holds.
  *(An initial reading of this test suggested 0.8 mV; that was a measurement error, corrected
  same day. Use 0.3 mV.)*

- **⚠ Never run a calibration while an external source is forcing the input.** `CAL`, `GAIN`,
  `CHOP` and `BUF … CAL` all trigger an internal AD7193 calibration, which assumes the input is
  at zero (and, for full-scale, at scale). With an SMU on the pin the cal either **times out** —
  `CHOP OFF` burns **20 s** and reports both `# [AD7193] Zero-scale calibration timeout!` and
  `# Full-scale calibration timeout!` — or worse, "succeeds" and folds the forced voltage into
  the offset register with no warning at all (observed 2026-07-31: a zero-scale cal at a forced
  100 mV wrote `Offset=0x800004` in place of `0x80025E`). Ground the input first, or accept that
  the cal is meaningless. This is why `BUF` does not recalibrate by default.

- **`BUF ON` stalls conversions when the pin is below the buffered common-mode floor —
  observed 2026-07-31, not just predicted.** With an SMU at 100 mV on a desoldered channel and
  `BUF ON`, `MEAS?` does not return a wrong number: the conversion never completes, the driver's
  timeout fires after ~8 s and the reply is `0.000000`. `BUF OFF` on the same setup reads
  100.5 mV in 0.06 s. This is the AGND+250 mV buffered input limit (AINCOM is tied to AGND here)
  becoming a hard failure rather than an offset. Two practical consequences: a run that hammers
  `MEAS?` in this state looks like a hang (~8 s × every read, and once one host command overruns
  its deadline the replies land one behind and cascade), and any low-voltage work on the pin
  **must** be done with `BUF OFF`.

- **`BUF OFF` cannot be used through the on-board divider — verified 2026-07-31.** Issuing
  `BUF OFF` on a normally-wired channel makes the AD7193's zero-scale calibration **time out**
  (`# [AD7193] Zero-scale calibration timeout!`), and the channel then reads exactly `0.000000`
  where it read ~290 µV buffered. `BUF ON` recovers cleanly (`# Zero-cal done`). This is the
  expected consequence of the ~16.7 kΩ divider source impedance, not a fault — and it is positive
  confirmation that unbuffered mode is only meaningful with an SMU driving the ADC pin directly on
  a desoldered channel, which is exactly how §5's A/B must be run. Do not read the `0.000000` as a
  measurement.

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

