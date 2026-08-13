# Channel characterization — accuracy, resolution, linearity

Bench record for the Koi 8×8 measurement and drive paths, taken against a
**Keithley 2100** 6.5-digit DMM automated over USB-TMC. Companion to
`measure-path-offset.md` (which covers the sense-path offset investigation
specifically).

**Rig, unless stated otherwise:** board 0, channel 0, load **1005.68 Ω**
(four-wire verified), 2100 connected across the load on a **fixed 10 V DCV
range** at 10 NPLC, 8 readings averaged per point. ADC at `RATE 96`, `AVG 4`,
gain 1, unipolar, SINC4, chopper off. Tools: `tools/keithley2100.py`,
`tools/vsweep_dmm.py`, `tools/resolution_test.py`.

### The reference load

Nominal 1 kΩ, **±1 % tolerance, 100 mW rating**; four-wire measured at
**1005.68 Ω** (+0.57 % of nominal, consistent with the tolerance).

- **Always use the measured 1005.68 Ω, never the 1 kΩ nominal.** The tolerance
  then drops out of every result. Assuming nominal would inject +0.57 % — larger
  than the drive gain error being measured. (Same trap as the single-point offset
  cal warned about in `measure-path-offset.md` §6.1.)
- **Power is never a concern here.** Full scale is 6.383 mA → **41.0 mW, 41 % of
  rating**. Reaching 100 mW needs 9.97 mA, above the firmware's 6.383 mA ceiling,
  so the part cannot be overrun through this instrument.
- **The tempco is NOT specified by the ±1 % tolerance** and is the parameter the
  self-heating arguments in §5A depend on. It is bounded from the data instead:
  - Explaining the whole measured INL as self-heating would require a tempco of
    only **1–4 ppm/°C** (Rth 850 → 200 °C/W). Thick film is 100–200 ppm/°C, metal
    film 50–100, carbon film −200 to −500 — so **self-heating is not the source of
    the INL.**
  - Fitting gain over restricted current ranges bounds where the heat does show
    up: −0.469 % (0.2–1.5 mA, 2.3 mW) → −0.481 % (0.2–6.4 mA, 41.2 mW). A real,
    monotonic trend with dissipation, but **only 0.012 % across an 18× power
    range**, i.e. ~2.5 % of the −0.48 % gain error.
  - Why it lands in gain and not INL: at 0.6 s dwell against a small resistor's
    ~10–100 s thermal time constant, a randomized sweep never lets the load reach
    its point-specific temperature — it sits near the run-average.
- **The four-wire value was measured cold**, and the load runs warmer during a
  sweep. Everything here uses the cold value (the right choice for consistency);
  that is the origin of the residual ~0.01–0.02 % on the gain figure.

> **Fix the DMM range for offset/accuracy work.** Autoranging splits a sweep
> across the 100 mV and 10 V range calibrations, and the difference between them
> lands directly in a fitted intercept of a few mV — exactly the quantity being
> measured.

## At a glance

| Quantity | Measured | Where |
| --- | --- | --- |
| Drive gain error | **−0.478 %** (`calSlope` 0.47 → **0.47226**) | §3 |
| Drive offset `I_OS` | +2.50 µA | §3 |
| Drive linearity (after gain+offset) | 0.0046 % FS | §3 |
| Sense residual, calibrated | **67 ppm FS** (max −423 µV) | §2 |
| Sense residual, nominal ×6 | 0.36 % FS | §2 |
| Resistance vs 4-wire truth | −0.4 % bias (drive), **±0.03–0.05 % scatter** | §5 |
| Measurement noise floor | **1.15 µV** at the ADC pin (RATE 96 / AVG 4) | §4 |
| Drive resolution | 1 LSB = **97.398 nA**, 65 536 codes | §5A |
| DNL / monotonicity | **±0.26 LSB / monotonic**, 0 non-monotonic in 96 | §5A |
| INL (randomized order) | **rms 1.01 LSB, max −2.24 LSB = 0.0034 % FS** | §5A |
| Effective resolution | **14.8 of 16 bits** | §5A |
| Usable sense range | **only above the node ≈44 mV knee** | `measure-path-offset.md` §5A |
| Usable `RATE` range | **≥16** (below that returns raw code 0) | §6.1 |
| Per-point settling | **0.8 s** converged; 0.3 s biases ~1.5 % | §5A |
| Fastest useful 2100 setting | **NPLC 1, autozero off** — 0.1 LSB in one reading | §5B |

**Everything here is one channel on one board (board 0, channel 0).** The
per-channel `I_OS` distribution, the sense knee, and the noise floor all still
need repeating across all 64 channels before any of it is published.

---

## 1. Compliance

No compliance limit over the full firmware range. Swept to 6.3 mA (6.26 V across
the load); incremental `dV/dI` stayed flat at 992.2–992.7 V/A and the XTR200
ERRORFLAG stayed clear at every point. The full 0–6.383 mA span is usable into a
~1 kΩ load.

## 2. Sense accuracy (Koi vs 2100)

69-point sweep, 0–6.3 mA. Fit over the 63 points above the low-signal knee
(§4 of `measure-path-offset.md`):

```
V_node = (V_ADC + 3.810 mV) × 5.9909
```

| quantity | value |
| --- | --- |
| residual after 2-parameter cal | max **−423 µV**, rms **110 µV** |
| — as fraction of 6.255 V full scale | **67 ppm** |
| residual using nominal ×6, no offset | max **+22.4 mV** = **0.36 % FS** |

So the sense path is worth ~70 ppm once calibrated, and ~0.4 % uncalibrated.

**Ratio caveat:** the in-situ fit returns 5.99, not the 6.0017 measured by
back-driving with the current source removed. This is *not* a divider error —
~1.86 Ω of series resistance in the load path between the divider tap and the
DMM connection carries the load current, so the divider sees `V_DMM + I·Rs`, a
current-proportional term that lands entirely in the fitted ratio. An in-situ
voltage-vs-voltage sweep can never measure the true ratio; use a back-drive
measurement for that, and in-situ sweeps for offset and linearity only.

## 3. Drive accuracy (2100 + the known resistor as current reference)

Actual sourced current derived as `I = V_DMM/1005.68 + V_DMM/120 kΩ` (load plus
the divider's share), fit against commanded current:

```
I_actual = 0.995218 × I_cmd + 2.496 µA
```

| quantity | value |
| --- | --- |
| **gain error** | **−0.478 %** → `calSlope` 0.47 seed should become **0.47226** |
| **XTR200 `I_OS`** | **+2.496 µA** (datasheet ±10 µA max; bench scatter ±4 µA) |
| **linearity residual** after gain+offset | max **0.30 µA** = **0.0046 % of 6.383 mA FS** |

The XTR200 itself is excellent — essentially all of the drive error is the
uncalibrated scale factor, confirming the "slope is a shared constant, offset is
per-channel" model. `calSlope`/`calOffset` remain at the ideal seed in firmware.

## 4. Measurement resolution (noise floor)

Channel 0 at 1.0 mA, 30 repeated reads per setting, 5 reads discarded after each
setting change (see the gotcha below).

| RATE | AVG | t/read | 1σ at ADC pin | 1σ at load | equivalent current | 2100's own 1σ |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 4 | 55 ms | 3.42 µV | 20.5 µV | 20 nA | 8.25 µV |
| 96 | 1 | 81 ms | 2.16 µV | 12.9 µV | 12.9 nA | 7.35 µV |
| 96 | 4 | 320 ms | **1.15 µV** | 6.9 µV | 6.8 nA | 6.44 µV |
| 96 | 16 | 1277 ms | 1.74 µV | 10.5 µV | 10.4 nA | 7.97 µV |
| 240 | 4 | 796 ms | 1.28 µV | 7.7 µV | 7.7 nA | 4.72 µV |

Timing matches the `0.83 ms × FS × AVG` model exactly at every point.

**Past RATE 96 / AVG 4 you are at the floor.** RATE 96/AVG 16 measured *worse*
than 96/AVG 4, and with n = 30 the σ estimate itself carries ~13 % uncertainty,
so 1.1–1.7 µV is one flat floor rather than a trend. Spending 4× the time buys
nothing here. Note also that the Koi's pin-referred noise is *below* the 2100's
own spread over the same reads.

## 5. Resistance accuracy vs the 1.00568 kΩ reference

R computed from the Koi alone (calibrated sense model, commanded current,
divider correction applied):

| range | R from Koi | vs four-wire truth |
| --- | --- | --- |
| I ≥ 0.5 mA | 1001.94 ± 0.87 Ω | −0.372 % |
| I ≥ 1.0 mA | 1001.72 ± 0.49 Ω | −0.393 % |
| I ≥ 2.0 mA | 1001.53 ± 0.27 Ω | −0.412 % |

**The −0.4 % bias is the drive gain error of §3, not the sense path** — R is
computed from *commanded* current, which runs 0.478 % high. The meaningful
figure of merit is the **scatter, ±0.27–0.49 Ω (0.03–0.05 %)**, which is what
remains once `calSlope` is trimmed.

---

## 5A. Current-source resolution and linearity — 2100 only, ADC not used

`tools/dac_char.py`. The on-board ADC takes no part in this measurement; current
is derived entirely from the 2100's voltage across the four-wire load,
`I_source = V/R + V/120 kΩ`. The firmware maps current to code as
`code = round(mA · calSlope/VREF · 65535)`, so an exact code is commanded by
inverting that — which lets the DAC be stepped one LSB at a time.

### Scale

| quantity | value |
| --- | --- |
| DAC | 16-bit, **65 536 codes** over 0–6.3830 mA |
| **1 LSB** | **97.398 nA** = 97.95 µV across 1005.68 Ω |
| 2100 noise (10 NPLC, 10 reads) | 7.44 µV = **0.076 LSB** |

The reference resolves ~13 steps within one DAC LSB, so **every single code is
individually resolvable** and the DAC's LSB — not the measurement — sets the
resolution. Points measured: full range 128 points at 50.26 µA (516 LSB) spacing;
single-code sweeps of 33 codes (±16 LSB) centred on 1.0, 3.0 and 5.0 mA
(codes 10267, 30801, 51336).

### Accuracy (full range, 128 points)

```
I_actual = 0.995231 × I_cmd + 2.519 µA        gain −0.477 %,  I_OS +2.52 µA
```

This independently reproduces the ADC-inclusive sweep of §3
(0.995218, +2.496 µA) to 1.3 × 10⁻⁵ in gain and 23 nA in offset — two separate
sweeps, so the drive numbers are solid.

### Linearity

| quantity | value |
| --- | --- |
| **INL** | max −3.73 LSB, rms 1.46 LSB = 0.0057 % FS — **UPPER BOUND ONLY, thermally contaminated, see below** |
| effective resolution from INL | 14.1 of 16 bits — **do not quote, same contamination** |
| **DNL** | max **±0.26 LSB** across all three centres (±0.4 LSB worst over all later runs) |
| **monotonicity** | **0 non-monotonic steps in 96** — monotonic everywhere measured, every run |
| smallest observed step | +72.3 nA (0.74 LSB), at the 5 mA centre |

### INL — measure it in RANDOMIZED code order (`tools/inl_random.py`)

**Resolved.** A monotonic ramp gives a contaminated INL; randomized order with an
interleaved drift reference gives a defensible one, and the residual is *not*
thermal.

| method | INL max | rms | % FS |
| --- | --- | --- | --- |
| monotonic 0→6.38 mA ramp | −3.73 LSB | 1.46 | 0.0057 % |
| **randomized + drift-corrected** | **−2.24 LSB** | **1.01** | **0.0034 %** |

The ramp overstates INL by ~60 %. Method: 48 codes visited in random order, a
fixed reference point re-measured every 4 points, each datum corrected by the
reference interpolated to its timestamp. The interleaved reference drifted only
**13–25 ppm** across each ~60–110 s pass, versus the ~6-minute
monotonically-heating ramp it replaces.

Three independent random orders (2 passes each):

| seed | INL max | rms | gain |
| --- | --- | --- | --- |
| 1 | −2.40 LSB | 1.03 | −0.485 % |
| 7 | −2.07 LSB | 0.99 | −0.481 % |
| 13 | −2.24 LSB | 1.01 | −0.481 % |

**Quote the rms: 1.01 ± 0.02 LSB.** It is stable to 2 % across seeds, whereas the
max is an extremum statistic and scatters over 2.07–2.40 LSB. Gain reproduces to
−0.482 ± 0.002 %.

**The remaining INL is genuine converter nonlinearity, not load heating.**
Tested rather than assumed. Note the correct predictor is **I³, not I²**: heating
goes as `ΔT ∝ I²R`, but the reported *current* is `V/R_assumed`, so the current
error is `I·αΔT ∝ I³`.

| predictor | R² | INL after removing that term |
| --- | --- | --- |
| I (linear) | 0.000 | −2.24 LSB, rms 1.01 |
| I² | 0.055 | −1.96 LSB, rms 0.98 |
| **I³ (correct thermal term)** | **0.148** | −2.04 LSB, rms 0.93 |

Even the correct I³ predictor explains only ~15 % of the variance and removing it
changes the rms by 8 %. The measured shape is an **arch peaking near mid-scale**
(−2.00 LSB at 0.2 mA, +1.16 at 3.36 mA, −0.84 at 5.7 mA) — a concave-down
parabola, whereas a detrended cubic is S-shaped, which is why the fit is poor.

The decisive argument is the tempco bound above: explaining this INL thermally
needs 1–4 ppm/°C, which no ±1 % resistor achieves.

**Pass-to-pass repeatability is 0.13 LSB per code**, so −2.40 LSB is ~18× the
noise floor. Gain came out −0.485 %, reproducing the −0.478 % and −0.477 % of §3
and §5A from two other sweeps.

| quantity | value |
| --- | --- |
| **INL** | **rms 1.01 LSB, max −2.24 LSB = 0.0034 % FS** |
| **effective resolution** | **14.8 of 16 bits** (from max INL) |
| INL repeatability (floor on the claim) | 0.13–0.16 LSB per code |

### Always check the DMM error queue during acquisition

`inl_random.py` queries `SYST:ERR?` after every point and separately flags short
bursts and overload readings. This is not optional bookkeeping — it is what
distinguishes a corrupted run from a good one, and none of the earlier tools did
it (only `vsweep_dmm.py`, and only once after config).

In practice about **one `-113 "Undefined header"` per ~130 measurements** appears
at a random point. It is an artifact of the USBTMC stall recovery: recovering
re-issues commands while a `READ?` may still be pending, and the instrument logs
the garbled fragment. `reopen()` now sends `*CLS` so recovery artifacts are not
misattributed to the following measurement, which reduces but does not eliminate
them.

**These do not corrupt data**, and that is checked rather than assumed: they fire
only in the SCPI-queue category, never in the overload or short-burst checks; the
stored CSV has no out-of-range values and exactly the expected point count; and
the INL reproduces across three independent random orders. Treat an overload or
short-burst flag as fatal to a run; treat a lone `-113` as cosmetic.

> **Always randomize.** With a self-heating load, code order and thermal state
> are confounded in any monotonic sweep. Randomization plus an interleaved
> reference is cheap — the run above is ~2 minutes — and it is the difference
> between a 0.0057 % number and a 0.0037 % one.

### ⚠ Local step size measurements ARE contaminated by load self-heating

Separately from the INL above: an earlier version of this section reported local
step sizes of 98.27 / 96.75 / 94.84 nA at the 1 / 3 / 5 mA centres, argued they
were ~20σ significant, and attributed them to converter INL. **That attribution
was wrong.** The measurements are significant, but the cause is the load resistor
heating, not the DAC — these single-code sweeps were taken in monotonic order and
reached each centre by ramping up from the previous one.

Re-measured at the 5 mA centre after a **60 s soak at 5 mA**, varying only the
per-point settle time:

| settle | mean step | note |
| --- | --- | --- |
| 0.30 s | 96.65 nA | |
| 0.80 s | **97.88 nA** | converged |
| 1.50 s | **98.00 nA** | agrees with 0.80 s to 0.12 nA |
| 0.30 s (repeat) | 96.21 nA | repeatable low bias |

Two separate effects:

1. **0.3 s settle is insufficient**; it biases the mean step low by ~1.5 %.
   0.8 s is converged (0.8 s and 1.5 s agree well inside the ~0.22 nA
   statistical uncertainty).
2. **Thermal state dominates.** The soaked 5 mA step is **97.9 nA**, against
   94.84 nA originally. The original figure was taken by ramping up from the
   3 mA centre with the load still warming — that 3 nA (3 %) gap is the load
   drifting, not the converter.

**This is why the 128-point monotonic INL sweep read −3.73 LSB against the
randomized measurement's −2.40 LSB** (see above): it ramped 0 → 6.38 mA over
~6 minutes with dissipation rising 0 → 40 mW. For scale, a metal-film resistor at
~50 ppm/°C warming a few °C drifts ~250 ppm.

**What is unaffected:** DNL (±0.24 to ±0.4 LSB across every run) and
**monotonicity — 0 non-monotonic steps out of 96, reproduced in every run at
every centre and every settle time**. Both are differential measurements between
adjacent codes taken seconds apart, so slow thermal drift largely cancels.

**Per-centre local step sizes have not been re-measured in randomized order.**
The INL that superseded them was, so there is no outstanding number that depends
on these; treat the 98.27 / 96.75 / 94.84 figures as withdrawn rather than
corrected.

---

## 5B. How fast the 2100 can actually go

Driven by "run it as fast as the meter supports and see what breaks". Measured at
1 mA, 200-reading bursts, 1 LSB = 97.95 µV at the load:

| NPLC req. | autozero | readings/s | 1σ | 1σ / LSB | time to reach 0.1 LSB |
| ---: | --- | ---: | ---: | ---: | ---: |
| 0.02 | off | 304.5 | 66.01 µV | 0.674 | 0.149 s |
| 0.02 | on | 434.1 | 69.88 µV | 0.713 | 0.117 s |
| **1** | **off** | **39.9** | **9.80 µV** | **0.100** | **0.025 s** |
| 1 | on | 34.9 | 11.37 µV | 0.116 | 0.039 s |
| 10 | off | 4.4 | 10.68 µV | 0.109 | 0.271 s |
| 10 | on | 3.2 | 9.24 µV | 0.094 | 0.274 s |

**NPLC 1 with autozero off is the optimum.** A *single* reading already gives
0.100 LSB. NPLC 10 is 10× slower for identical noise — it buys nothing here.
NPLC 0.02 is genuinely fast (up to 434 readings/s) but noisy enough (0.7 LSB)
that reaching 0.1 LSB needs ~45 averaged readings, making it 6× slower overall
than just using NPLC 1.

At 0.025 s/point this is **~80× faster** than the 2.0 s/point (10 reads × NPLC 10)
used for the §5A campaign, at the same precision. **The DMM was never the
bottleneck — settling is** (§5A).

### Use burst reads, not one `READ?` per value

At low NPLC the USB round-trip, not integration, limits `read()` throughput.
`Keithley2100.read_burst(n)` sets `SAMP:COUN n` and fetches all n in one
transfer. Two traps found implementing it:

- **A burst reply spans several USBTMC messages.** A single `os.read()` returned
  16 of 100 requested values, truncated mid-number. Replies carry no trailing
  newline (USBTMC marks end-of-message out of band) and a short read does not
  imply the last message, so the only reliable termination is **counting
  values** — `read_values()` does this.
- **An unread remainder poisons the next session.** The leftover ~1.4 kB sat in
  the instrument's output buffer and was parsed as commands on the next
  connection, surfacing as spurious `-108 "Parameter not allowed"` errors that
  looked like a config bug. Always drain the full reply.

### The 2100 silently coerces NPLC

Requested vs. what `SENS:VOLT:DC:NPLC?` reports back, **with no SCPI error**:

| requested | 0.02 | 0.06 | 0.2 | 1 | 10 | 100 |
| --- | --- | --- | --- | --- | --- | --- |
| reported | 1 | 10 | 10 | 1 | 10 | 10 |

`NPLC 0.2` really integrates at 10, which is why a 200-reading burst at
"NPLC 0.2" took 60 s — identical to NPLC 10. Timing behaviour did *not* always
match the readback either (the 0.02 request ran at 300+ readings/s, far faster
than NPLC 1 permits), so **trust measured throughput over either the request or
the readback**, and always report the achieved rate.

---

## 6. Firmware and bench gotchas found during this campaign

### 6.1 `RATE` below 16 is broken — do not use, and do not quote the FS=8 tables

At `RATE` 4, 8 and 12 **both** readout paths return raw code 0 — `MEAS?` gives
exactly `0.0000000` and `MEASA?` gives `nan` — on a signal known good at
0.16224 V. `RATE` 16, 24, 32 and 96 all read correctly. `AVG` is irrelevant and
both paths fail together, so it is the ADC configuration at low filter words,
not the read path.

| RATE | `MEAS?` | `MEASA?` |
| --- | --- | --- |
| 4, 8, 12 | `0.0000000` | `nan` |
| 16, 24, 32, 96 | 0.16224 ✓ | 0.16224 ✓ |

**Consequence:** CLAUDE.md's "Measured timing vs RATE" table lists FS=8 (600 Hz
ODR, 0.45 s full grid) as bench-measured, and its entire `AVG n` noise table was
taken at FS=8. **Neither is reproducible as written.** Re-measure before quoting
either, in the paper or anywhere else.

The firmware still replies `OK RATE <n>` for these values, so a bad setting is
silently accepted and only surfaces as zero readings. **Treat RATE 16 as the
practical minimum.**

### 6.2 A `RATE` change needs settling

The first conversions after a filter-rate change are invalid — observed as
all-zero codes and, in one run, a 0.13 V spread while settling. Discard ~5 reads
after changing `RATE` before measuring or timing.

### 6.3 Raw code 0 looks different in each polarity

A wedged ADC returns raw code `0x000000`, which reads as **`0.0` in unipolar**
and **`−3.0 V` (−VREF) in bipolar**. Check for both; a sweep over a wedged ADC
still fits a perfectly plausible straight line.

### 6.4 Two ways to wedge the ADC

- **`CAL` while `BIPOLAR ON`** wedges it immediately. Do not recalibrate in
  bipolar — bipolar sweeps are accurate *without* a recal, and adding one
  destroys them. (The tempting analogy with `GAIN`'s required zero-scale recal
  does not hold.)
- **Toggling `XTR` mid-session** desyncs it: readings survive but come back with
  a bogus ~−676 mV offset and a ~6.5 incremental ratio. Set the front-end state
  once at session start and leave it.

Both recover on reopening the serial port, which resets the Pico.

### 6.5 `MEASA?` can exceed the host's command deadline

At `RATE 96 × AVG 16` a single-board `MEASA?` takes ~10.2 s and blows
`KoiLink.command()`'s 10 s deadline, returning `None`. Use `MEAS? g` for
high-AVG single-channel work.
