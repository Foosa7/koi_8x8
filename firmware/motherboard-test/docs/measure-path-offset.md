# Measure-path offset investigation (the "10 mV error")

Engineering record for the residual measurement error on the Koi 8×8 sense path.
Last bench session: **2026-07-25**.

**Status: root cause NOT yet confirmed.** Every component outside the AD7193 has been
eliminated by direct measurement. One hypothesis remains standing and its decisive test
has not been run.

---

## 1. The symptom

The ADC under-reads by roughly **3.4 mV at the ADC pin** (≈ 20 mV heater-referred through
the 6:1 divider) whenever there is signal on the input. At zero input it does *not* under-read —
it reads **+0.29 mV**.

Originally this presented as a ~10 mV error and was mistaken for a 2 % gain error, because a
fixed offset is 2.2 % of a 0.163 V signal but 0.43 % of a 0.826 V signal. Percentage error that
shrinks as signal grows is the signature of an offset, not a gain term. **This confusion has
cost the project two separate debugging cycles — see §6.**

## 2. Bench rig (2026-07-25)

The XTR200 for the channel under test was **physically desoldered**, and the divider's 100k was
later lifted. This removes the entire drive path and lets a source meter force the sense node
directly, which is what made the measurement conclusive.

- **Keithley 2410 SMU** — forces the node voltage. HI on the divider input pad, LO on the board
  ground (bypass-cap ground, via two Dupont connectors).
- **Keithley 2100 DMM** — verifies the forced voltage.
- Load: **999.8 Ω**, 4-wire measured.

## 3. Data

### 3.1 SMU forcing the divider input (XTR200 removed, divider intact)

| applied (2100-verified) | SMU current | ADC raw |
| --- | --- | --- |
| 1.00050 V | — | 163.331 mV |
| 3.0014 V | 3.0162 mA | 496.720 mV |

Two-point fit:

```
D = (3.0014 − 1.00050) / (0.496720 − 0.163331) = 6.0017
k = 1.00050/6.0017 − 0.163331                  = 3.372 mV

raw = V_node / 6.0017 − 3.372 mV
```

This measurement **decided the offset-vs-gain question**. Predictions going in were 496.68 mV
(fixed offset) vs 490.00 mV (−2.2 % gain); the measurement landed at 496.720 — a 40 µV hit on a
6.7 mV fork.

Two consequences:

- **The divider is nominal to 0.03 %.** The host's fixed **×6.0 is correct** — do not "correct"
  it, and do not let the GUI's "Sweep + cal" adjust the divider ratio. The earlier ÷5.990 figure
  was an artifact of measuring through the XTR200.
- The entire residual is **one additive ~3.4 mV term**.

It is additive, not proportional to load current: `raw₂/raw₁ = 3.0412` against
`V₂/V₁ = 2.99990`. A load-current-proportional term (1.0 → 3.0 mA) would make those equal.
This rules out ground-return IR drop.

### 3.2 Zero input

| condition | reading |
| --- | --- |
| XTR200 disabled | 0.290 mV |
| XTR200 physically removed | 0.290 mV |
| 100k lifted (nothing on pin but 20k to GND) | ~0.3 mV |
| 100k lifted, **CHOP ON** | 0.001 mV |

The zero-point offset is therefore **entirely internal to the AD7193** — nothing external
contributes — and it is **choppable**. It matches the known gain-1 zero-scale calibration
artifact: `writeBoardConfig` runs the internal cal with `AD7193_CONF_CHAN(0)` selected, so the
shared offset register is trimmed for channel 0 and channels 1–7 carry that correction plus
their own mux-path offset (measured per board: b0 ~0.28, b2 ~0.72, b6 ~1.4 mV).

**The split between these two terms is the key diagnostic result:**

| term | choppable? | depends on input level? |
| --- | --- | --- |
| +0.29 mV at zero | **yes** (→ 0.001 mV) | no |
| −3.4 mV at signal | **no** | apparently yes |

CHOP cancels a fixed internal offset. It does not touch the 3.4 mV. Whatever causes the 3.4 mV
is therefore *not* a fixed internal offset but a level-dependent input-stage error.

### 3.3 Supporting measurements

| quantity | measured | spec/nominal | error |
| --- | --- | --- | --- |
| VREF | 2.9998 V | 3.0000 (hardcoded `ADC_VREF`) | 0.007 % |
| DAC @ 0.470 V cmd | 0.47014 / 0.47040 / 0.47030 | 0.470 | +0.03…0.085 % |
| load resistor (4-wire) | 999.8 Ω | 1 kΩ | −0.02 % |
| divider pin→GND | 16.691 kΩ | 20k‖(100k+1k) = 16.694 | ✓ |
| divider pin→node | 17.354 kΩ | 100k‖(20k+1k) = 17.355 | ✓ |
| AINCOM → local ground | 0 mV | 0 | ✓ |
| SMU LO → local ground | 0 mV | 0 | ✓ |

DAC scatter is 0.26 mV ≈ 5.7 LSB (1 LSB = 45.8 µV), i.e. offset scatter rather than
quantization. Referred through the 0.47 V/mA transconductance that is 0.85 µA worst case —
well inside the ±4 µA IOS scatter and not significant.

## 4. Eliminated by measurement

| suspect | verdict | evidence |
| --- | --- | --- |
| drive path / XTR200 | out | chip physically desoldered, error persists |
| DAC accuracy | out | 0.03 % at 0.470 V |
| voltage reference | out | 2.9998 V (0.007 %) |
| divider ratio | out | 6.0017 from SMU fit; ohmmeter agrees |
| load resistor tolerance | out | 999.8 Ω 4-wire |
| solder joints / divider integrity | out | 16.691 / 17.354 kΩ textbook |
| PCB ground / PDN | out | padne FEM, <0.3 µV |
| AINCOM ground reference | out | 0 mV to local ground |
| SMU LO reference offset | out | LO on board ground, 0 mV |
| ground-return IR drop | out | additive, not current-proportional (§3.1) |
| AD7193 internal offset | out | CHOP no-op at signal; measured floor tens of µV |
| aliased noise / oscillation | out | reading is rock-stable |
| input filter cap (absent) | real but not causal | stable reading rules out AC rectification |

## 5. Buffered common-mode range — **REFUTED 2026-07-29**

> **This hypothesis is dead.** Its sharp prediction — a knee at pin = 250 mV (node 1.5 V,
> ~1.5 mA) — was tested directly by a 16-point in-situ sweep and does not exist: the
> per-point offset runs flat through 250 mV (−3.708 mV at pin 5 mV → −3.780 at pin 250 mV
> → −3.750 at pin 494 mV). **There *is* a knee, but it is at pin 7.3 mV, not 250 mV**, and
> it is a gain change rather than an offset. See §5A. The section below is kept for the
> record of what was ruled out and why.

**UNCONFIRMED. This is the only mechanism not yet eliminated.**

With `BUF` enabled, the AD719x family restricts the *absolute* input voltage range to
approximately **AGND + 250 mV to AVDD − 250 mV**, and that applies to **AINCOM as well as AIN**.

On this board **AINCOM is tied to AGND (0 V)** — permanently ~250 mV below the valid range,
and it has been since bring-up, on all 8 boards. An input stage operating without headroom
produces millivolts of offset, is not covered by the offset spec, and is not cancelled by
chopping because it is not a fixed internal offset.

What it explains:

- additive and roughly constant across pin = 167 mV and 500 mV
- unaffected by CHOP
- AINCOM measuring 0 mV to local ground (*that is the problem, not the disproof*)
- ~0 at zero input — the internal zero-scale cal runs at zero and absorbs the error at that
  point; as AIN rises the error changes and a residual is left
- offset apparently larger at low current — below node 1.5 V the pin is under 250 mV so *both*
  inputs are out of range; above it only AINCOM is

**Sharp prediction: a knee at pin = 250 mV**, i.e. node = 1.5 V, i.e. ~1.5 mA into 1 kΩ.
Error falling steeply below it and flattening above ⇒ confirmed.

### Decisive test (not yet run)

With the 100k lifted, drive the ADC pin directly from the SMU (its output impedance dominates
the 20k, which draws only 25 µA at 500 mV):

| forced at pin | ADC clean | ADC is the problem |
| --- | --- | --- |
| 167.000 mV | 167.3 mV | ~163.6 mV |
| 500.000 mV | 500.3 mV | ~496.6 mV |

Then repeat with **`AD7193_CONF_BUF` off**. Unbuffered range is AGND − 50 mV to AVDD + 50 mV,
so 0 V AINCOM becomes legal. Offset collapsing to tens of µV confirms the hypothesis.

Because the SMU drives the pin directly, unbuffered mode has no source-impedance penalty —
this is the only clean way to A/B the buffer. Driving unbuffered from the 16.7 kΩ divider
would introduce a switched-cap gain error and confound the result.

**If steps above are ambiguous**, the definitive discriminator changes the common mode and
nothing else: lift AINCOM off ground, hold it at ~500 mV from a bench supply, put AIN at
~1000 mV. Same 500 mV differential, both pins now inside the buffered range. Offset vanishing
⇒ common-mode range, full stop.

### If confirmed, the fix is a board change

Not trivial, and worth knowing before committing:

- **Cannot simply bias AINCOM up** to 250 mV — the signal is 0–500 mV ground-referenced and
  unipolar mode needs AIN ≥ AINCOM. That would require level-shifting the whole measurement.
- **Run unbuffered behind a low-offset op-amp buffer** (chopper-stabilized, e.g. OPA333 at
  ~10 µV offset) — keeps the 0 V reference and gives unbuffered mode the low source impedance
  it needs.
- **Re-scale the divider** low enough to drive unbuffered mode directly — simplest electrically,
  but steals proportionally more current from the heater node.

Also note: an out-of-range input stage will drift with temperature and vary part to part, so
a calibrated constant measured today is not guaranteed to hold tomorrow. That is the argument
against simply subtracting 3.372 mV and moving on.

## 5A. 2026-07-29 — it is a LOW-VOLTAGE GAIN DEFICIT, not an offset

**Rig:** channel 0, XTR200 *installed*, 1005.68 Ω load (4-wire), Keithley 2100 across the load
**automated** over USBTMC (`tools/keithley2100.py`), swept by `tools/vsweep_dmm.py`. Fixed 10 V
DCV range, 10 NPLC, 8 readings/point; ADC at RATE 96 / AVG 4 / gain 1 / SINC4 / chop off.
Automation is what made this findable — it costs nothing to take 16 points × 8 readings, and
the structure only shows up if you sample below 0.05 mA, which no hand-taken dataset ever did.

### The measurement

| cmd mA | node mV | pin mV | incremental ratio | implied offset mV |
| ---: | ---: | ---: | ---: | ---: |
| 0.000 | 2.808 | 0.185 | — | −0.284 |
| 0.005 | 7.180 | 0.484 | 14.59 | −0.714 |
| 0.010 | 12.222 | 0.886 | 12.55 | −1.154 |
| 0.015 | 17.161 | 1.296 | 12.05 | −1.569 |
| 0.020 | 22.096 | 1.711 | 11.89 | −1.977 |
| 0.025 | 27.090 | 2.134 | 11.81 | −2.388 |
| 0.030 | 32.012 | 2.556 | 11.65 | −2.787 |
| 0.040 | 41.994 | 3.456 | 11.10 | −3.553 |
| 0.050 | 51.828 | 4.945 | **6.60** | −3.705 |
| 0.075 | 76.696 | 9.076 | 6.02 | −3.725 |
| 0.100 | 101.553 | 13.209 | 6.01 | −3.741 |
| 0.200 | 200.752 | 29.731 | 6.00 | −3.776 |
| 1.000 | 994.923 | 162.242 | 5.993 | −3.817 |
| 2.000 | 1987.582 | 327.953 | 5.990 | −3.786 |
| 3.000 | 2980.190 | 493.659 | 5.990 | −3.753 |

Two clean linear regimes:

| regime | span | fit | r² |
| --- | --- | --- | --- |
| **low** | 0.005–0.04 mA (node ≤ 42 mV) | `pin = node/11.729 − 0.157 mV` | 0.99954 |
| **high** | 0.05–3.0 mA | `pin = node/5.992 − 3.748 mV` | **1.0000000** |

They cross at **node 44.0 mV / pin 7.3 mV** (~43.7 µA).

### What it means

**The famous −3.4 mV additive offset is not additive and is not an offset.** The low regime's
intercept is **−0.157 mV, i.e. essentially zero** — at small signals the path has no offset,
only about *half* the correct gain (11.73 vs 5.99). The sense path therefore reaches the knee
already ~3.6 mV short, and above the knee it tracks with the *correct* slope forever. To anyone
sampling only above 0.05 mA — which is every prior dataset in this document — that is
indistinguishable from a perfect fixed offset.

*Honesty about the arithmetic:* "high intercept = low line extrapolated through the crossover"
closes to 0.0004 mV, but that is tautological — the crossover was derived from the two fits.
The non-circular content is **low intercept ≈ 0 vs high intercept = −3.75 mV**. That is what
says the offset is *generated by* the gain transition rather than existing on its own.

### Implied mechanism

A current drawn from the ADC pin that is **ohmic (~17.4 kΩ) below ~3.9 mV and saturates at
~225 nA** above it (225 nA × (100k‖20k = 16.67 kΩ) = 3.75 mV). Resistive-then-constant-current
is the signature of a leaky junction / ESD structure. **225 nA is ~200× the AD7193's buffered
input-leakage spec**, so if it is the ADC's own input it is out of spec; it could equally be a
board leakage path onto the ~16.7 kΩ node. Note this also explains why the power-off ohmmeter
checks looked textbook (16.691 kΩ pin→GND): an ohmmeter cannot see a 225 nA saturating leak.

### Decisive next test

DMM the **ADC pin itself** while sweeping the same low-current points. If the *pin voltage*
shows the 11.73 → 5.99 knee, the current is being drawn at the node → board/ESD leakage, fixable
upstream. If the pin tracks a clean 6:1 and only the reported code shows the knee, it is inside
the AD7193. (Caveat from §7: hanging a meter on this unfiltered node perturbs it — take the
comparison with the meter attached for *all* points so the perturbation is common-mode.)

### Also settled this session

- **Not a unipolar clamp** (the obvious suspect): 0 mA reads 0.000182 V unipolar vs 0.000183 V
  bipolar — identical, and bipolar can represent negatives. Cross-check at 1 mA: 0.1622400 vs
  0.1622383.
- **The ÷5.990 "through-XTR200 artifact" is ~1.86 Ω of series resistance** in the load path
  between the divider tap and the DMM clip point: `6.0017/(1 + Rs/1005.68) = 5.9906` → Rs = 1.86 Ω.
  The divider sees `V_dmm + I·Rs`, proportional to current, so it lands entirely in the fitted
  ratio. **The host's fixed ×6.0 remains correct.** Consequence: an in-situ V-vs-V sweep can
  never measure the true divider ratio — it will always read ~5.991. Use the desoldered/back-drive
  rig for ratio, in-situ only for offset/linearity.

## 6. Methodology warnings

### 6.1 Do not derive the offset by forcing R to read exactly 1 kΩ

The tempting in-situ method — command a known current, compute R, adjust an offset until
R = 1000 Ω — assumes three things that are false:

1. **The resistor is 1000 Ω.** It is 999.8 Ω (4-wire).
2. **The commanded current is the actual current.** XTR200 IOS is ±4 µA measured — **±0.4 % at
   1 mA** — and is per-channel.
3. **A single point can separate offset from gain.** It cannot; at one current they are
   algebraically indistinguishable.

The resulting number is not a measure-side offset. It is a catch-all absorbing drive error +
load tolerance + measure error, and it will differ at every current and on every channel.
**This plausibly explains the reported "offset varies pin to pin and with current"** — IOS is
per-channel (pin-to-pin), and single-point fitting against a non-offset error gives a different
answer at every current by construction. Those observations may be artifacts of the method.

**Instead, measure the three paths independently:**

- **Measure path** — force known voltages on the pin with the SMU (50, 100, 167, 250, 350,
  500 mV), read the ADC, fit. No resistor, no current, no XTR, no assumptions. Reveals whether
  the error is offset, gain, or curved, and directly tests the 250 mV knee.
- **Divider** — already known independently (6.0017, plus the ohmmeter readings).
- **Drive path** — characterize separately by *measuring* current rather than assuming it.

If an in-situ calibration on assembled channels is unavoidable, **sweep and fit** (intercept =
offset, slope = gain) rather than single-point — this needs no assumed R, and is already the
recommended method for the current-side offset table.

### 6.2 Absolute mV vs percent

Before concluding "the offset changes with current", check whether it is changing in absolute
mV or as a percentage of signal. A constant 3.4 mV reads as 8.1 % at 0.25 mA, 2.0 % at 1 mA and
0.68 % at 3 mA. This is the error that made the term look like a 2 % gain error and it has
recurred twice.

## 7. Bench gotchas from this campaign

- **Back-driving the divider pad draws the load current.** 1 V into 1 kΩ‖120 kΩ ≈ 1.01 mA;
  3 V ≈ 3.03 mA. Set SMU compliance accordingly (5 mA for the 3 V point). A compliance trip
  turns the SMU into a current source and the node never reaches the set voltage, making any
  reading meaningless.
- **With the XTR200 installed, forcing >~1–2 V on its output pin makes an ESD clamp conduct.**
  Measured >5 mA at 3 V against 3.03 mA expected. Provably nonlinear: a linear parallel path
  could not go from ≤0.49 mA at 1 V to >1.97 mA at 3 V. This is a **bench artifact only** — in
  normal operation the XTR200 sources current and nothing back-drives that pin. Remove the chip
  to take back-drive points.
- **Rework hazard.** An improperly resoldered XTR200 read **67.228 mV at zero command** —
  ~407 µA of output current, ~40× the ±10 µA datasheet IOS max, equivalent to ~190 mV of input
  offset. A bad joint on ground, an input, or the set resistor makes the part obey a bogus
  input. This is not leakage. After any rework, re-verify the divider against the known-good
  baselines (pin→GND 16.691 kΩ, pin→node 17.354 kΩ) before trusting any offset number.
- **A DMM lead left floating in air reads a few mV of ambient pickup.** It is not a
  measurement. One such reading nearly sent this investigation down a false path because it
  happened to land near the value being hunted.
- **Probing the unfiltered ~16.7 kΩ sense node perturbs it** (a DMM on the pin once shifted it
  ~30 mV and the ADC followed). There is no input filter cap on this design. Adding ~0.1 µF C0G
  at each input is worthwhile for probe immunity and EMI — BUF is on and the divider's 16.7 kΩ
  is series R, so no DC gain error, τ ≈ 1.7 ms — but it is **not** the cause of the 3.4 mV.

## 8. Open questions

1. **Does the ADC alone show the 3.4 mV?** (§5 decisive test — not yet run.)
2. **Does BUF off remove it?**
3. **Is there a knee at pin = 250 mV?** Requires the low-end sweep, which also settles §6.2.
4. **Is the pin-to-pin variation real, or the §6.1 method artifact?** Cannot be answered from
   measurements that involve XTR200s and loads. Nail one channel with SMU + ADC only first.
5. **Forced 0.000 V with the SMU connected** was never taken. It closes the loop on whether the
   zero point is genuinely different or just a different circuit configuration.
