/*
 * main.cpp — Host-commanded 8×8 current driver (DAC80508 → XTR200 → AD7193)
 *
 * One Pico controls up to 8 daughterboards × 8 channels = 64 current-source
 * channels. The host PC speaks a line-based ASCII protocol over USB serial:
 * it loads per-channel current setpoints (mA), then reads back the voltage each
 * channel develops across its load (a TiN heater on the photonic chip). The
 * host derives resistance / power; the firmware only sources current + reports
 * voltage.
 *
 * Channel addressing is a flat global index g = board*8 + channel, 0..63.
 *
 * Chip-select for both banks comes from two 74HC138 decoders sharing the
 * address lines A0=GP20, A1=GP21, A2=GP22 with their own active-low enables:
 *   ADC_EN=GP5, DAC_EN=GP6. Decoder outputs are inverted (Y0=CS8 ... Y7=CS1),
 *   handled inside HC138::selectDevice().
 *
 * ADC bus (SPI0): GP2=SCK, GP3=MOSI, GP4=MISO   (shared with SN74LV595)
 * DAC bus (SPI1): GP10=SCK, GP11=SDI            (write-only, no MISO/SDO)
 * SN74LV595 XTR_OD front-end enables: shares SPI0, RCLK=GP7
 * SN74LV165 XTR200 ERRORFLAG readback: one per daughterboard, daisy-chained
 *   board1→…→board8, bit-banged on dedicated pins (NOT on SPI0):
 *   CP=GP16 (shift clock), Q7 of board 8 = GP17 (serial data), PL=GP18 (load)
 *
 * ──────────────────────────────────────────────────────────────────────────
 * HOST COMMAND PROTOCOL  (commands and replies are '\n'-terminated; every
 * command produces exactly one reply line so the host stays in sync)
 *
 * Two-way ack: every "OK" reply ECHOES its command name, so the host can
 * verify a reply belongs to the command it just sent and resync/retry if not.
 * Any line the firmware emits on its own (boot banner, cal progress, STREAM)
 * starts with '#' — an unprefixed line is always a direct command reply.
 *
 *   *IDN?               -> KOI,8x8,fw1.3
 *   *RST                -> OK *RST   (all currents 0, all front-ends disabled)
 *   ISETA i0 i1 .. i63  -> OK ISETA  (set all 64 currents in mA, g-order)
 *   ISET g mA           -> OK ISET <g>  (set one channel)
 *   MEASA?              -> v0,..,v63 (measure all 64; "nan" for unpopulated)
 *   MEASA? <mask>       -> v0,..,v63 (only boards whose bit is set in mask)
 *   MEAS? g             -> <volts>   (measure one channel; "nan" if absent)
 *   XTR <enmask>        -> OK XTR 0x..  (bit b = 1 enables board b's front-ends)
 *   AVG n               -> OK AVG <n>  (1..64 on-micro averages per measurement)
 *   RATE fs             -> OK RATE <fs>  (AD7193 filter word 1..1023; sets both
 *                          the scan and single-conversion filter rate)
 *   GAIN g              -> OK GAIN <g>  (AD7193 PGA gain 1|8|16|32|64|128;
 *                          folded into the reported voltage, triggers a recal)
 *   CHOP ON|OFF         -> OK CHOP ..  (chopper offset/drift cancellation; recal)
 *   FILTER SINC3|SINC4  -> OK FILTER .. (digital filter type: SINC3 faster settle,
 *                          SINC4 better rejection/noise — the default)
 *   REJ60 ON|OFF        -> OK REJ60 .. (simultaneous 50/60 Hz notch rejection)
 *   BIPOLAR ON|OFF      -> OK BIPOLAR .. (input polarity: OFF=unipolar 0..FS, the
 *                          default; ON=bipolar ±FS so small negatives don't clamp)
 *   BUF ON|OFF [CAL]    -> OK BUF ..    (AD7193 input buffer, fw1.3. ON is the
 *                          only setting valid for normal use — the 6:1 divider
 *                          is a ~16.7 kΩ source. OFF needs an SMU driving the
 *                          pin directly and is for the measure-path A/B in
 *                          docs/measure-path-offset.md §5. NO recal by default:
 *                          whenever BUF OFF is legitimately in use the input is
 *                          being forced to a non-zero voltage, which a
 *                          zero-scale cal would silently absorb. Add CAL, with
 *                          the input at 0 V, to refresh the offset.)
 *   ADC?                -> OK ADC rate=.. avg=.. gain=.. chop=.. filter=.. rej60=..
 *                          polarity=uni|bi buf=0|1  (live sampling settings)
 *   STREAM ON|OFF       -> OK STREAM  (periodic '# v0,..' dump for eyeballing)
 *   RESCAN              -> OK RESCAN active=0x.. new=0x..  (re-detect boards)
 *   PING? b             -> OK PING <b> 1|0  (board liveness: ID + one real ADC
 *                          conversion, non-mutating; 0 for an undetected board
 *                          — use RESCAN to adopt it. The ADC is the only
 *                          readback on a board, so a passing PING is the proxy
 *                          that the whole board (incl. the write-only DAC's
 *                          shared select path) is seated and powered.)
 *   DACINIT [b]         -> OK DACINIT 0x<mask>  (rewrite DAC config: soft
 *                          reset, external ref, REFDIV÷2 gain, reload
 *                          setpoints — board b or all populated. Recovery for
 *                          a DAC that browned out back to its internal 2.5 V
 *                          reference; mask = boards actually reinitialized.)
 *   CAL [b]             -> OK CAL 0x<mask>  (run AD7193 internal zero/full-scale
 *                          cal with the XTR200 front-ends forced off, board b or
 *                          all populated; mask = boards calibrated)
 *   CALCLR [b]          -> OK CALCLR 0x<mask>  (clear user cal: reset ADC to
 *                          factory offset/full-scale + rewrite config, board b or
 *                          all populated — A/B against CAL for the offset)
 *   ERR?                -> OK ERR 0x<16 hex>  (snapshot of all 64 XTR200
 *                          ERRORFLAG pins via the SN74LV165 chain; bit g =
 *                          RAW level at channel g's EF pin — the host applies
 *                          polarity. Bits of unpopulated boards are garbage:
 *                          a missing board breaks the chain, mask by the
 *                          active-board set.)
 *
 * Reported voltage is the RAW ADC-pin voltage (computed in double for full
 * 24-bit resolution). The host applies the known 6:1 input divider to recover
 * the true heater voltage (heater_V = reported_V × 6).
 * Errors reply "ERR <reason>".
 * ──────────────────────────────────────────────────────────────────────────
 */

#include <Arduino.h>
#include <SPI.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include "HC138.h"
#include "DAC80508.h"
#include "AD7193.h"
#include "XTR595.h"

// ── Shared 74HC138 chip-select ──────────────────────────
#define PIN_ADDR0     20
#define PIN_ADDR1     21
#define PIN_ADDR2     22
#define PIN_ADC_EN     5
#define PIN_DAC_EN     6
#define PIN_XTR_RCLK   7

// ── SN74LV165 XTR200 ERRORFLAG chain (bit-banged, dedicated pins) ──
#define PIN_ERR_CP    16   // shift clock (rising edge)
#define PIN_ERR_Q7    17   // serial data: Q7 of the LAST 165 in the chain (board 8)
#define PIN_ERR_PL    18   // parallel load, active low (CLK INH tied low on-board)

// ── System size ─────────────────────────────────────────
#define NUM_BOARDS     8
#define CH_PER_BOARD   8
#define TOTAL_CH       (NUM_BOARDS * CH_PER_BOARD)   // 64

// ── References ──────────────────────────────────────────
#define VREF       3.0f
#define ADC_VREF   3.0f

// NOTE: there is a 6:1 resistor divider (100k/20k) in front of each AD7193
// input. The firmware reports the RAW ADC-pin voltage; the host multiplies by
// 6 to get the heater voltage (e.g. 2.5 V heater → 416.7 mV at the ADC pin).

// Current → DAC-voltage calibration. Ideal XTR200 transconductance gives
// I[mA] = 10 · Vdac / 4.7kΩ  →  Vdac = 0.47 · I[mA]. Per-channel slope/offset
// are placeholders here; overwrite with Keithley-fit constants later.
#define CAL_SLOPE_DEFAULT   0.47f   // volts per mA
#define CAL_OFFSET_DEFAULT  0.00f   // volts

// ── Buses + decoders ────────────────────────────────────
HC138 adcSelect(PIN_ADDR0, PIN_ADDR1, PIN_ADDR2, PIN_ADC_EN);
HC138 dacSelect(PIN_ADDR0, PIN_ADDR1, PIN_ADDR2, PIN_DAC_EN);

//                            MISO  MOSI  SCK
// Must match the wiring: the AD7193 DOUT lines land on GP4, so the ADC read
// path is SPI0. Moving adcSpi to SPI1 (GP12 MISO, an unconnected pin) makes
// every device read back 0x00 — ID mismatch on all 8, boards present or not.
arduino::MbedSPI dacSpi(       12,   11,   10);   // DAC on SPI1, write-only (MISO unused)
arduino::MbedSPI adcSpi(        4,    3,    2);   // ADC + 595 share SPI0

DAC80508     dac(&dacSelect, &dacSpi, NUM_BOARDS, VREF);
AD7193Driver adc(&adcSelect, &adcSpi, NUM_BOARDS);
XTR595       xtr(&adcSpi, PIN_XTR_RCLK);

// ── Runtime state ───────────────────────────────────────
bool     boardActive[NUM_BOARDS];
float    setpoint_mA[TOTAL_CH];
float    calSlope[TOTAL_CH];
float    calOffset[TOTAL_CH];
uint8_t  avgCount = 4;            // on-micro averages per measurement (default 4)
uint16_t adcRate  = 16;          // AD7193 filter word (FS) used for fast scans.
                                 //   Default 16 (~0.9 s full-grid refresh, quieter
                                 //   than FS=8). Per-channel time ≈ 0.83 ms × FS
                                 //   (SINC4 settling, ~4 conv periods — NOT 1/ODR):
                                 //   FS=8 ≈ 8 ms/ch, FS=240 ≈ 0.2 s/ch. Higher
                                 //   FS = slower/cleaner. ODR = 4.92MHz/(1024·FS).
// AD7193 sampling settings (host-tunable; applied to every populated board by
// writeBoardConfig, so they survive RESCAN/reset). CHOP + REJ60 + SINC3 trade
// speed for noise/offset; gain is the PGA field (folded into codeToVolts so the
// reported voltage stays the true ADC-pin voltage regardless of gain).
uint8_t  adcGain  = AD7193_CONF_GAIN_1;  // encoded CONF gain field (not the ×N)
bool     adcChop  = false;       // CHOP: chopper offset/drift cancellation
bool     adcSinc3 = false;       // filter: false = SINC4 (default), true = SINC3
bool     adcRej60 = false;       // simultaneous 50/60 Hz notch rejection
bool     adcBipolar = false;     // false = unipolar (0..FS), true = bipolar (±FS)
// AD7193 input buffer. ON is the only setting valid for normal use here: the
// 6:1 sense divider presents ~16.7 kΩ, and unbuffered mode needs a low-impedance
// source driving the pin directly (an SMU on a desoldered channel). OFF exists
// for the measure-path A/B in docs/measure-path-offset.md §5 — with BUF on, the
// absolute input range is AGND+250 mV..AVDD−250 mV and that applies to AINCOM,
// which is tied to AGND here, so the part is out of spec by construction.
bool     adcBuf = true;
uint8_t  xtrState = 0xFF;        // OD bits: 1 = disabled, 0 = enabled

bool     streamOn = false;
uint32_t streamInterval = 1000;
uint32_t streamLast = 0;

// ── Line buffer ─────────────────────────────────────────
#define LINE_MAX 768             // enough for "ISETA " + 64 floats
static char     lineBuf[LINE_MAX];
static uint16_t lineLen = 0;

// Assembled MEASA/STREAM reply (64 values × up to ~14 chars). Built fully in
// RAM, then emitted in a single write so the host sees one atomic line.
static char     measBuf[1024];

// ════════════════════════════════════════════════════════
// Helpers
// ════════════════════════════════════════════════════════
static uint16_t mAToCode(uint8_t g, float mA) {
    float v = calSlope[g] * mA + calOffset[g];
    if (v < 0.0f) v = 0.0f;
    if (v > VREF) v = VREF;
    return DAC80508::voltageToCode(v, VREF);
}

// Per-board DAC-output scramble.
// The AD7193 inputs are wired sequentially (ADC input n senses physical channel
// n), so the read path is identity. The DAC80508 outputs, however, are permuted
// on the daughterboard: out0→ch1, out1→ch3, out2→ch5, out3→ch7, out4→ch6,
// out5→ch4, out6→ch2, out7→ch0. To source current into physical channel p we
// drive the DAC output that lands on p — the inverse map below. (All
// daughterboards share the layout, so one table serves all.)
static const uint8_t DAC_CH_FOR_PHYS[CH_PER_BOARD] = {7, 0, 6, 1, 5, 2, 4, 3};

// Forward map (inverse of DAC_CH_FOR_PHYS): DAC-output index → physical channel,
// i.e. out0→ch1, out1→ch3, … out7→ch0. The XTR200 front ends — and their
// ERRORFLAG pins on the SN74LV165 chain — are laid out in this same DAC-output
// order, so readErrorChain() uses it to place each flag on its physical channel.
static const uint8_t PHYS_FOR_DAC[CH_PER_BOARD] = {1, 3, 5, 7, 6, 4, 2, 0};

static void writeChannel(uint8_t g) {
    uint8_t b  = g / CH_PER_BOARD;
    uint8_t ch = g % CH_PER_BOARD;             // physical channel
    if (!boardActive[b]) return;
    dac.setDAC(b, DAC_CH_FOR_PHYS[ch], mAToCode(g, setpoint_mA[g]));
}

// Decode the CONF gain field into its numeric PGA gain (×1..×128).
static inline uint16_t adcGainValue() {
    switch (adcGain) {
        case AD7193_CONF_GAIN_8:   return 8;
        case AD7193_CONF_GAIN_16:  return 16;
        case AD7193_CONF_GAIN_32:  return 32;
        case AD7193_CONF_GAIN_64:  return 64;
        case AD7193_CONF_GAIN_128: return 128;
        default:                   return 1;   // AD7193_CONF_GAIN_1
    }
}

// Non-channel MODE flags common to every mode write (idle/single/scan): internal
// clock (mandatory — no crystal) plus the host-tunable SINC3 / REJ60 filter bits.
static inline uint32_t adcModeFlags() {
    uint32_t m = AD7193_MODE_CLKSRC_INT;
    if (adcSinc3) m |= AD7193_MODE_SINC3;
    if (adcRej60) m |= AD7193_MODE_REJ60;
    return m;
}

static inline double codeToVolts(uint32_t raw) {
    // V_in referred to the ADC pin (÷gain so the host's 6:1 divider stays gain-
    // independent). Double keeps full 24-bit precision. Unipolar: 0..FS maps
    // 0..2^24. Bipolar: ±FS maps 0..2^24 with midscale (2^23) = 0 V, so small
    // negative offsets read as negative instead of clamping at 0.
    double g = (double)adcGainValue();
    if (adcBipolar)
        return (((double)raw / 8388608.0) - 1.0) * (double)ADC_VREF / g;
    return ((double)raw / 16777216.0) * (double)ADC_VREF / g;
}

// Measure one channel: average avgCount single conversions and return the RAW
// ADC-pin voltage. Done in double because a 24-bit code exceeds a 32-bit
// float's mantissa — float would silently drop the bottom bits. The host
// applies the 6:1 divider to recover the heater voltage.
static double measureChannel(uint8_t g) {
    uint8_t b  = g / CH_PER_BOARD;
    uint8_t ch = g % CH_PER_BOARD;             // physical channel == ADC input
    uint8_t n  = avgCount < 1 ? 1 : avgCount;
    uint64_t acc = 0;
    for (uint8_t i = 0; i < n; i++) {
        acc += adc.singleConversion(b, AD7193_CONF_CHAN(ch));
    }
    return codeToVolts((uint32_t)(acc / n));
}

// Measure all 64 channels into `out` as a CSV line, THEN return — nothing is
// printed during the (slow) measurement, so the caller emits the whole reply in
// one burst. Each populated board is read with ONE continuous-sequencer pass
// (all 8 channels), which avoids the per-channel single-conversion restart
// overhead. `bmask` bit b gates board b; avgCount passes are averaged.
static void buildMeasureLine(char* out, size_t cap, uint8_t bmask) {
    double vals[TOTAL_CH];
    bool   ok[TOTAL_CH];
    for (uint16_t g = 0; g < TOTAL_CH; g++) ok[g] = false;

    uint8_t n = avgCount < 1 ? 1 : avgCount;
    for (uint8_t b = 0; b < NUM_BOARDS; b++) {
        if (!boardActive[b] || !(bmask & (1 << b))) continue;

        uint64_t acc[CH_PER_BOARD]  = {0};
        uint8_t  good[CH_PER_BOARD] = {0};
        for (uint8_t a = 0; a < n; a++) {
            uint32_t codes[CH_PER_BOARD] = {0};
            uint8_t got = adc.scanContinuous(b, adc.confReg[b], adcRate,
                                             CH_PER_BOARD, codes, adcModeFlags());
            for (uint8_t ch = 0; ch < CH_PER_BOARD; ch++) {
                if (got & (1u << ch)) { acc[ch] += codes[ch]; good[ch]++; }
            }
        }
        for (uint8_t ch = 0; ch < CH_PER_BOARD; ch++) {
            // ADC input == physical channel == g-channel (sequential wiring).
            uint16_t g = b * CH_PER_BOARD + ch;
            if (good[ch]) { vals[g] = codeToVolts(acc[ch] / good[ch]); ok[g] = true; }
        }
    }

    size_t p = 0;
    for (uint16_t g = 0; g < TOTAL_CH; g++) {
        if (g && p < cap - 1) out[p++] = ',';
        if (ok[g]) {
            String num(vals[g], 6);                  // double → string, 6 dp
            const char* s = num.c_str();
            while (*s && p < cap - 1) out[p++] = *s++;
        } else {
            const char* s = "nan";
            while (*s && p < cap - 1) out[p++] = *s++;
        }
    }
    out[p] = '\0';
}

// Write the ADC config + mode registers (no internal calibration). Splitting
// this out lets detection configure a board cheaply before confirming it with a
// conversion — without paying the (5 s-timeout-per-call) internal cal on a board
// that may be absent. The internal clock select is mandatory: with the default
// (external-crystal) clock bits and no crystal, RDY never asserts.
static void writeBoardConfig(uint8_t b) {
    // Pseudo-differential, REFIN2, channel AIN1; gain, CHOP, polarity
    // (uni/bipolar) and the input buffer from the host-tunable settings.
    adc.confReg[b] = AD7193_CONF_PSEUDO | AD7193_CONF_REFSEL | AD7193_CONF_REFDET |
                     (adcGain & AD7193_CONF_GAIN_MASK) | AD7193_CONF_CHAN(0);
    if (adcBuf)      adc.confReg[b] |= AD7193_CONF_BUF;
    if (!adcBipolar) adc.confReg[b] |= AD7193_CONF_UNIPOLAR;
    if (adcChop)     adc.confReg[b] |= AD7193_CONF_CHOP;
    adc.writeRegister(b, AD7193_REG_CONF, adc.confReg[b]);

    // Idle, internal clock + filter flags, filter word = adcRate. Using adcRate
    // (not a fixed 96) keeps MEAS?'s singleConversion in sync with MEASA?'s scan.
    adc.modeReg[b] = AD7193_MODE_IDLE | adcModeFlags() | AD7193_MODE_RATE(adcRate);
    adc.writeRegister(b, AD7193_REG_MODE, adc.modeReg[b]);
}

static void configureBoard(uint8_t b) {
    writeBoardConfig(b);
    // Force ALL XTR200 front-ends OFF across the AD7193 internal zero/full-scale
    // calibration, then restore. An enabled front-end sources its offset current
    // into the load during the cal, which biases the zero-scale point — that
    // stray offset is exactly what we were chasing. At boot xtrState is already
    // 0xFF (disabled); on a runtime RESCAN it holds the live enables, so saving
    // and restoring it leaves the enable state untouched.
    uint8_t savedXtr = xtrState;
    xtr.setOutputs(0xFF);                 // OD all high = all front-ends disabled
    adc.calibrateInternalZero(b);
    adc.calibrateInternalFull(b);
    xtr.setOutputs(savedXtr);             // restore prior enable state
}

// Re-apply the current ADC sampling settings to every populated board after a
// live setting change. `recal` runs the full internal zero/full-scale cal
// (configureBoard) — needed when CHOP changes; rate/filter changes just
// rewrite CONF+MODE (writeBoardConfig), no recal required.
static void reapplyAdcSettings(bool recal) {
    for (uint8_t b = 0; b < NUM_BOARDS; b++) {
        if (!boardActive[b]) continue;
        if (recal) configureBoard(b);
        else       writeBoardConfig(b);
    }
}

// Re-apply settings + run a ZERO-SCALE-ONLY cal on every populated board — used
// on a gain change. The AD7193's offset register is gain-dependent: a stale
// gain-1 offset applied at a higher gain over-subtracts and clamps the unipolar
// result to 0. Internal zero-scale cal is valid at any gain, so re-run it at the
// new gain; the full-scale coefficient (valid only at gain 1) is left untouched
// from boot — running full-scale cal here would corrupt it. Front-ends forced
// off across the cal so no XTR200 offset current biases the zero point.
static void recalZeroScaleActive() {
    uint8_t savedXtr = xtrState;
    xtr.setOutputs(0xFF);                 // OD all high = all front-ends disabled
    for (uint8_t b = 0; b < NUM_BOARDS; b++) {
        if (!boardActive[b]) continue;
        writeBoardConfig(b);              // apply the new gain first
        adc.calibrateInternalZero(b);     // offset cal at the new gain
    }
    xtr.setOutputs(savedXtr);             // restore prior enable state
}

// ── Board presence detection ────────────────────────────
// The DAC has no readback, so the AD7193 is our only "is this board here?"
// signal — and a bare ID-nibble read is not enough: an absent board floats the
// shared MISO and can read a stray 0x02, while a present board with a flaky
// (HASL) contact can read garbage or be left SPI-desynced. So presence is
// confirmed by ACTUALLY READING THE ADC: the board must both report the ID
// nibble AND complete a real conversion (probeConversion → RDY toggles instead
// of timing out). Taking a moment per board is fine and intended.
#define DETECT_ATTEMPTS   4      // resync+confirm tries before giving up
#define DETECT_RETRY_MS   40     // settle between tries (rides out a contact glitch)

// Non-destructive confirm: assumes the board is already configured (internal
// clock). Cheap — used to re-verify an already-active board without disturbing
// it. ID first so a truly-absent board fails fast instead of waiting out the
// conversion timeout.
static bool confirmPresent(uint8_t b) {
    if ((adc.readID(b) & AD7193_ID_MASK) != AD7193_ID_VALUE) return false;
    return adc.probeConversion(b, AD7193_CONF_CHAN(0));
}

// Robust detect: resync the serial interface, (re)write config, then confirm
// with a real conversion — retried a few times so a momentary glitch doesn't
// drop a present board. Leaves config written but NOT calibrated; the caller
// runs configureBoard() on a board this returns true for.
static bool detectBoard(uint8_t b) {
    for (uint8_t t = 0; t < DETECT_ATTEMPTS; t++) {
        adc.reset(b);                 // clock 40 ones → resync serial interface
        writeBoardConfig(b);          // internal clock etc. (RDY hangs without it)
        if (confirmPresent(b)) return true;
        delay(DETECT_RETRY_MS);
    }
    return false;
}

// enMask bit b = 1 -> board b front-ends ENABLED (OD driven low).
static void setEnableMask(uint8_t enMask) {
    xtrState = (uint8_t)(~enMask);   // enabled -> OD low (0), disabled -> OD high (1)
    xtr.setOutputs(xtrState);
}

// Snapshot all 64 XTR200 ERRORFLAG pins via the daisy-chained SN74LV165s
// (one per daughterboard, 8 EF inputs each; board1's Q7 feeds board2's SER,
// …, board8's Q7 is GP17). Returns RAW input levels, bit g = channel g's EF
// pin — polarity is the host's problem. Shift order: after the PL pulse the
// first bit on Q7 is board 8 input H, then G..A, then board 7 streams through.
// The 165 inputs sit in the same DAC-output order as the front ends, so each
// arriving slot is a DAC-output index — remapped to its physical channel via
// PHYS_FOR_DAC so a fault lands on the same channel the DAC drive / GUI use
// (chain position = daughterboard slot). A missing daughterboard breaks the
// chain, so bits for boards wired UPSTREAM of a gap are garbage — the host
// masks by the active-board set.
static uint64_t readErrorChain() {
    digitalWrite(PIN_ERR_PL, LOW);         // latch the 64 flag inputs
    delayMicroseconds(1);
    digitalWrite(PIN_ERR_PL, HIGH);
    delayMicroseconds(1);
    uint64_t v = 0;
    for (int b = NUM_BOARDS - 1; b >= 0; b--) {
        for (int slot = CH_PER_BOARD - 1; slot >= 0; slot--) {
            int g = b * CH_PER_BOARD + PHYS_FOR_DAC[slot];   // slot = DAC-output idx
            if (digitalRead(PIN_ERR_Q7)) v |= (1ULL << g);
            digitalWrite(PIN_ERR_CP, HIGH); // rising edge shifts the next bit in
            delayMicroseconds(1);
            digitalWrite(PIN_ERR_CP, LOW);
            delayMicroseconds(1);
        }
    }
    return v;
}

static void reply(const char* s)    { Serial.println(s); }
static void replyErr(const char* s) { Serial.print("ERR "); Serial.println(s); }

// ════════════════════════════════════════════════════════
// Command dispatch
// ════════════════════════════════════════════════════════
static void processLine(char* line) {
    char* save;
    char* cmd = strtok_r(line, " \t", &save);
    if (!cmd) return;

    if (strcasecmp(cmd, "*IDN?") == 0) {
        reply("KOI,8x8,fw1.3");

    } else if (strcasecmp(cmd, "*RST") == 0) {
        for (uint16_t g = 0; g < TOTAL_CH; g++) { setpoint_mA[g] = 0.0f; writeChannel(g); }
        setEnableMask(0x00);                 // all front-ends disabled
        reply("OK *RST");

    } else if (strcasecmp(cmd, "ISETA") == 0) {
        float tmp[TOTAL_CH];
        uint16_t n = 0;
        char* tok;
        while ((tok = strtok_r(NULL, " ,\t", &save)) != NULL) {
            if (n >= TOTAL_CH) { n = 0xFFFF; break; }   // too many → flag error
            tmp[n++] = atof(tok);
        }
        if (n != TOTAL_CH) { replyErr("ISETA needs 64 values"); return; }
        for (uint16_t g = 0; g < TOTAL_CH; g++) { setpoint_mA[g] = tmp[g]; writeChannel(g); }
        reply("OK ISETA");

    } else if (strcasecmp(cmd, "ISET") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        char* v = strtok_r(NULL, " ,\t", &save);
        if (!a || !v) { replyErr("usage: ISET <g> <mA>"); return; }
        int g = atoi(a);
        if (g < 0 || g >= TOTAL_CH) { replyErr("bad channel"); return; }
        setpoint_mA[g] = atof(v);
        writeChannel((uint8_t)g);
        char buf[20];
        snprintf(buf, sizeof(buf), "OK ISET %d", g);
        reply(buf);

    } else if (strcasecmp(cmd, "MEASA?") == 0) {
        char* m = strtok_r(NULL, " ,\t", &save);
        uint8_t bmask = m ? (uint8_t)strtol(m, NULL, 0) : 0xFF;
        buildMeasureLine(measBuf, sizeof(measBuf), bmask);  // measure all (silent)
        Serial.println(measBuf);                            // emit in one burst

    } else if (strcasecmp(cmd, "MEAS?") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: MEAS? <g>"); return; }
        int g = atoi(a);
        if (g < 0 || g >= TOTAL_CH) { replyErr("bad channel"); return; }
        if (!boardActive[g / CH_PER_BOARD]) { reply("nan"); return; }
        Serial.println(measureChannel((uint8_t)g), 6);

    } else if (strcasecmp(cmd, "XTR") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: XTR <enmask>"); return; }
        uint8_t en = (uint8_t)strtol(a, NULL, 0);
        setEnableMask(en);
        char buf[20];
        snprintf(buf, sizeof(buf), "OK XTR 0x%02X", en);
        reply(buf);

    } else if (strcasecmp(cmd, "AVG") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: AVG <n>"); return; }
        int n = atoi(a);
        if (n < 1 || n > 64) { replyErr("AVG 1..64"); return; }
        avgCount = (uint8_t)n;
        char buf[20];
        snprintf(buf, sizeof(buf), "OK AVG %d", n);
        reply(buf);

    } else if (strcasecmp(cmd, "RATE") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: RATE <fs>"); return; }
        int fs = atoi(a);
        if (fs < 1 || fs > 1023) { replyErr("RATE 1..1023"); return; }
        adcRate = (uint16_t)fs;                 // used by the continuous scan
        reapplyAdcSettings(false);              // keep MEAS? single-conv in sync
        char buf[20];
        snprintf(buf, sizeof(buf), "OK RATE %d", fs);
        reply(buf);

    } else if (strcasecmp(cmd, "GAIN") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: GAIN <1|8|16|32|64|128>"); return; }
        int g = atoi(a);
        uint8_t code;
        switch (g) {
            case 1:   code = AD7193_CONF_GAIN_1;   break;
            case 8:   code = AD7193_CONF_GAIN_8;   break;
            case 16:  code = AD7193_CONF_GAIN_16;  break;
            case 32:  code = AD7193_CONF_GAIN_32;  break;
            case 64:  code = AD7193_CONF_GAIN_64;  break;
            case 128: code = AD7193_CONF_GAIN_128; break;
            default:  replyErr("GAIN 1|8|16|32|64|128"); return;
        }
        adcGain = code;
        // Zero-scale re-cal at the new gain: the offset register is gain-
        // dependent, and a stale gain-1 offset applied at a higher gain drives
        // the unipolar reading to 0. This is the fast cal only (no full-scale,
        // which is invalid above gain 1 — the boot gain-1 coeff is kept).
        recalZeroScaleActive();
        char buf[20];
        snprintf(buf, sizeof(buf), "OK GAIN %d", g);
        reply(buf);

    } else if (strcasecmp(cmd, "CHOP") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "ON")  == 0) adcChop = true;
        else if (a && strcasecmp(a, "OFF") == 0) adcChop = false;
        else { replyErr("usage: CHOP ON|OFF"); return; }
        reapplyAdcSettings(true);               // chop changes the cal → recalibrate
        reply(adcChop ? "OK CHOP ON" : "OK CHOP OFF");

    } else if (strcasecmp(cmd, "FILTER") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "SINC4") == 0) adcSinc3 = false;
        else if (a && strcasecmp(a, "SINC3") == 0) adcSinc3 = true;
        else { replyErr("usage: FILTER SINC3|SINC4"); return; }
        reapplyAdcSettings(false);
        reply(adcSinc3 ? "OK FILTER SINC3" : "OK FILTER SINC4");

    } else if (strcasecmp(cmd, "REJ60") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "ON")  == 0) adcRej60 = true;
        else if (a && strcasecmp(a, "OFF") == 0) adcRej60 = false;
        else { replyErr("usage: REJ60 ON|OFF"); return; }
        reapplyAdcSettings(false);
        reply(adcRej60 ? "OK REJ60 ON" : "OK REJ60 OFF");

    } else if (strcasecmp(cmd, "BIPOLAR") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "ON")  == 0) adcBipolar = true;
        else if (a && strcasecmp(a, "OFF") == 0) adcBipolar = false;
        else { replyErr("usage: BIPOLAR ON|OFF"); return; }
        // No recal — polarity just reinterprets the code midpoint; the offset
        // register from the last (gain) cal still applies, so a small residual
        // now reads as a signed value instead of clamping at 0.
        reapplyAdcSettings(false);
        reply(adcBipolar ? "OK BIPOLAR ON" : "OK BIPOLAR OFF");

    } else if (strcasecmp(cmd, "BUF") == 0) {
        // BUF ON|OFF [CAL]
        //
        // Switching the buffer moves the offset, so a zero-scale recal is
        // wanted in principle — but it is NOT run by default, and that is
        // deliberate. Unbuffered mode is only valid with an external source
        // driving the ADC pin directly, so whenever BUF OFF is legitimately in
        // use there is a non-zero forced voltage on the input and a ZERO-scale
        // cal is meaningless: it either times out, or "succeeds" and silently
        // folds the forced voltage into the offset register. Both were observed
        // on 2026-07-31 with an SMU at 100 mV. Pass the explicit CAL argument
        // once the input is actually at zero.
        char* a = strtok_r(NULL, " ,\t", &save);
        char* c = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "ON")  == 0) adcBuf = true;
        else if (a && strcasecmp(a, "OFF") == 0) adcBuf = false;
        else { replyErr("usage: BUF ON|OFF [CAL]"); return; }
        bool wantCal = (c && strcasecmp(c, "CAL") == 0);
        if (c && !wantCal) { replyErr("usage: BUF ON|OFF [CAL]"); return; }

        if (wantCal && adcBipolar) {
            // Cal-in-bipolar is known to wedge this part at code 0.
            reapplyAdcSettings(false);
            Serial.println("# [BUF] CAL requested but bipolar is active — recal "
                           "SKIPPED (cal-in-bipolar wedges the ADC). BIPOLAR OFF "
                           "first, then BUF <state> CAL.");
        } else if (wantCal) {
            recalZeroScaleActive();
        } else {
            reapplyAdcSettings(false);
            Serial.println("# [BUF] config rewritten, offset NOT recalibrated. "
                           "The offset register still holds the value from the "
                           "previous buffer setting. With the input at 0 V, run "
                           "'BUF <state> CAL' (or CAL) to refresh it.");
        }
        reply(adcBuf ? "OK BUF ON" : "OK BUF OFF");

    } else if (strcasecmp(cmd, "ADC?") == 0) {
        char buf[104];
        snprintf(buf, sizeof(buf),
                 "OK ADC rate=%u avg=%u gain=%u chop=%d filter=%s rej60=%d polarity=%s buf=%d",
                 (unsigned)adcRate, (unsigned)avgCount, (unsigned)adcGainValue(),
                 adcChop ? 1 : 0, adcSinc3 ? "SINC3" : "SINC4", adcRej60 ? 1 : 0,
                 adcBipolar ? "bi" : "uni", adcBuf ? 1 : 0);
        reply(buf);

    } else if (strcasecmp(cmd, "STREAM") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "ON")  == 0) { streamOn = true;  reply("OK STREAM ON"); }
        else if (a && strcasecmp(a, "OFF") == 0) { streamOn = false; reply("OK STREAM OFF"); }
        else replyErr("usage: STREAM ON|OFF");

    } else if (strcasecmp(cmd, "RESCAN") == 0) {
        // Re-detect boards at runtime, confirming each with a real conversion.
        // An already-active board is first re-verified NON-destructively
        // (confirmPresent: ID + one conversion, no reset) so a healthy board
        // keeps running undisturbed. Only a board that fails that check — or an
        // inactive position — gets the full detectBoard() resync (reset clocks
        // 40 ones, which also wipes config), and any board found that way is
        // reconfigured (ADC config + internal cal) and has its setpoints
        // reloaded. Boards that fail even after resync are dropped.
        uint8_t activeMask = 0, newlyFound = 0;
        for (uint8_t b = 0; b < NUM_BOARDS; b++) {
            bool wasActive = boardActive[b];
            bool present;
            if (wasActive && confirmPresent(b)) {
                present = true;                      // healthy → leave untouched
            } else {
                present = detectBoard(b);            // resync + confirm by conversion
                if (present) {
                    configureBoard(b);               // reset wiped config → reconfigure
                    for (uint8_t ch = 0; ch < CH_PER_BOARD; ch++)
                        writeChannel(b * CH_PER_BOARD + ch);   // reload setpoints
                }
            }
            boardActive[b] = present;
            if (present)               activeMask |= (uint8_t)(1 << b);
            if (present && !wasActive) newlyFound |= (uint8_t)(1 << b);
        }
        // Enable any newly-found boards' front-ends (safe: setpoints are 0),
        // OR'd into the current enable mask so existing enables are preserved.
        if (newlyFound) setEnableMask((uint8_t)(~xtrState) | newlyFound);

        char buf[48];
        snprintf(buf, sizeof(buf), "OK RESCAN active=0x%02X new=0x%02X",
                 activeMask, newlyFound);
        reply(buf);

    } else if (strcasecmp(cmd, "PING?") == 0) {
        // Board liveness check, non-mutating: re-verify an active board with
        // the ADC (ID nibble + one real conversion — the only readback a board
        // has). An undetected board replies 0 without being probed; RESCAN is
        // the way to adopt it (PING? must never reset/reconfigure anything).
        char* a = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: PING? <board>"); return; }
        int b = atoi(a);
        if (b < 0 || b >= NUM_BOARDS) { replyErr("bad board"); return; }
        bool alive = boardActive[b] && confirmPresent((uint8_t)b);
        char buf[24];
        snprintf(buf, sizeof(buf), "OK PING %d %d", b, alive ? 1 : 0);
        reply(buf);

    } else if (strcasecmp(cmd, "DACINIT") == 0) {
        // Recover a DAC that lost its config mid-session (brownout → back to
        // the internal 2.5 V reference; the write-only bus can't detect this,
        // so the host infers it — e.g. a channel reading >1 V at 0 mA — and
        // asks for a rewrite). Soft-resets the DAC(s), re-selects external
        // ref + REFDIV÷2 gain, reloads the live setpoints. Optional board
        // arg; default = every populated board (~120 ms each).
        char* a = strtok_r(NULL, " ,\t", &save);
        int b0 = 0, b1 = NUM_BOARDS - 1;
        if (a) {
            int b = atoi(a);
            if (b < 0 || b >= NUM_BOARDS) { replyErr("bad board"); return; }
            b0 = b1 = b;
        }
        uint8_t mask = 0;
        for (int b = b0; b <= b1; b++) {
            if (!boardActive[b]) continue;
            dac.reinit((uint8_t)b);
            for (uint8_t ch = 0; ch < CH_PER_BOARD; ch++)
                writeChannel((uint8_t)(b * CH_PER_BOARD + ch));
            mask |= (uint8_t)(1 << b);
        }
        char buf[24];
        snprintf(buf, sizeof(buf), "OK DACINIT 0x%02X", mask);
        reply(buf);

    } else if (strcasecmp(cmd, "CAL") == 0) {
        // Run the AD7193 internal zero/full-scale calibration on demand (board b
        // or all populated). configureBoard() forces the XTR200 front-ends off
        // across the cal so no offset current biases the zero-scale point, then
        // restores the prior enable state — same path used at boot/RESCAN.
        char* a = strtok_r(NULL, " ,\t", &save);
        int b0 = 0, b1 = NUM_BOARDS - 1;
        if (a) {
            int b = atoi(a);
            if (b < 0 || b >= NUM_BOARDS) { replyErr("bad board"); return; }
            b0 = b1 = b;
        }
        uint8_t mask = 0;
        for (int b = b0; b <= b1; b++) {
            if (!boardActive[b]) continue;
            configureBoard((uint8_t)b);
            mask |= (uint8_t)(1 << b);
        }
        char buf[24];
        snprintf(buf, sizeof(buf), "OK CAL 0x%02X", mask);
        reply(buf);

    } else if (strcasecmp(cmd, "CALCLR") == 0) {
        // Clear the user-run calibration: reset() reloads the AD7193's factory
        // offset/full-scale registers (undoing any internal cal), then rewrite
        // config/mode so RDY still works. Front-ends and DAC setpoints untouched.
        // Use with CAL to A/B the calibrated vs uncalibrated zero-scale offset.
        char* a = strtok_r(NULL, " ,\t", &save);
        int b0 = 0, b1 = NUM_BOARDS - 1;
        if (a) {
            int b = atoi(a);
            if (b < 0 || b >= NUM_BOARDS) { replyErr("bad board"); return; }
            b0 = b1 = b;
        }
        uint8_t mask = 0;
        for (int b = b0; b <= b1; b++) {
            if (!boardActive[b]) continue;
            adc.reset((uint8_t)b);            // → factory offset/full-scale defaults
            writeBoardConfig((uint8_t)b);     // internal clock etc. (RDY hangs without)
            mask |= (uint8_t)(1 << b);
        }
        char buf[24];
        snprintf(buf, sizeof(buf), "OK CALCLR 0x%02X", mask);
        reply(buf);

    } else if (strcasecmp(cmd, "MISOPROBE") == 0) {
        // Hardware bring-up: repeatedly read a board's ID register while pulsing
        // ADC_EN, so GP4 (MISO)/GP2 (SCK)/GP5 (ADC_EN) can be scoped. Deliberately
        // NOT gated on boardActive — the point is to probe when detection failed.
        // Args: MISOPROBE <board> [durationMs]  (default 3000, capped 8000).
        // The cap is the host's, not the hardware's: KoiLink.command() gives up
        // after 10 s, and on timeout it drains and resends once — so a longer
        // probe doesn't just time out, it silently runs a SECOND time. Repeat
        // the command for a longer scope session.
        char* a = strtok_r(NULL, " ,\t", &save);
        char* d = strtok_r(NULL, " ,\t", &save);
        if (!a) { replyErr("usage: MISOPROBE <board> [durationMs]"); return; }
        int b = atoi(a);
        if (b < 0 || b >= NUM_BOARDS) { replyErr("bad board"); return; }
        uint32_t ms = d ? (uint32_t)strtoul(d, NULL, 0) : 3000u;
        if (ms > 8000u) ms = 8000u;
        adc.misoProbe((uint8_t)b, ms);       // '#'-framed diagnostics only
        char buf[36];
        snprintf(buf, sizeof(buf), "OK MISOPROBE %d %lu", b, (unsigned long)ms);
        reply(buf);

    } else if (strcasecmp(cmd, "ERR?") == 0) {
        uint64_t raw = readErrorChain();
        char buf[28];
        // %llX is unreliable under Mbed's newlib-nano printf — print as two
        // 32-bit halves.
        snprintf(buf, sizeof(buf), "OK ERR 0x%08lX%08lX",
                 (unsigned long)(raw >> 32), (unsigned long)(raw & 0xFFFFFFFFul));
        reply(buf);

    } else {
        replyErr("unknown command");
    }
}

// ════════════════════════════════════════════════════════
void setup() {
    Serial.begin(115200);
    while (!Serial) delay(10);
    delay(2000);

    for (uint16_t g = 0; g < TOTAL_CH; g++) {
        setpoint_mA[g] = 0.0f;
        calSlope[g]    = CAL_SLOPE_DEFAULT;
        calOffset[g]   = CAL_OFFSET_DEFAULT;
    }

    // SN74LV165 error-flag chain: PL idles high (shift mode), CP idles low.
    pinMode(PIN_ERR_CP, OUTPUT); digitalWrite(PIN_ERR_CP, LOW);
    pinMode(PIN_ERR_PL, OUTPUT); digitalWrite(PIN_ERR_PL, HIGH);
    pinMode(PIN_ERR_Q7, INPUT);

    Serial.println();
    Serial.println("# KOI 8x8 current driver — host command interface");

    // Bus / device bring-up (these print their own diagnostics).
    dac.begin();
    adc.begin();

    // Detect populated daughterboards: each must report the AD7193 ID nibble AND
    // complete a real conversion (detectBoard resyncs + retries). A bare ID read
    // alone is unreliable — an absent position floats the shared MISO and can
    // read a stray 0x02. This takes a moment per board, which is intended.
    Serial.print("# active boards:");
    for (uint8_t b = 0; b < NUM_BOARDS; b++) {
        boardActive[b] = detectBoard(b);
        if (boardActive[b]) { Serial.print(' '); Serial.print(b); }
    }
    Serial.println();

    // Front-ends start disabled (OD all HIGH) BEFORE any ADC calibration, so the
    // AD7193 internal zero/full-scale cal runs with no XTR200 current flowing
    // into the load. configureBoard() also enforces this per-board (for RESCAN),
    // but bringing the 595 up first makes the boot cal explicitly clean.
    xtr.begin();                                  // OD all HIGH = disabled

    // Configure + calibrate each confirmed-present ADC (front-ends off).
    for (uint8_t b = 0; b < NUM_BOARDS; b++)
        if (boardActive[b]) configureBoard(b);

    // Load 0 mA everywhere before enabling the front-ends.
    for (uint16_t g = 0; g < TOTAL_CH; g++) writeChannel(g);

    // Enable populated boards' front-ends (still 0 mA, so safe).
    uint8_t enMask = 0;
    for (uint8_t b = 0; b < NUM_BOARDS; b++)
        if (boardActive[b]) enMask |= (uint8_t)(1 << b);
    setEnableMask(enMask);

    Serial.println("# READY");   // '#' so a host connecting mid-boot skips it
}

void loop() {
    // Drain any pending command bytes (non-blocking, one line at a time).
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (lineLen > 0) {
                lineBuf[lineLen] = '\0';
                processLine(lineBuf);
                lineLen = 0;
            }
        } else if (lineLen < LINE_MAX - 1) {
            lineBuf[lineLen++] = c;
        } else {
            lineLen = 0;
            replyErr("line too long");
        }
    }

    // Optional eyeball stream (debug only; prefixed '#' so the host can ignore).
    if (streamOn && (millis() - streamLast >= streamInterval)) {
        streamLast = millis();
        buildMeasureLine(measBuf, sizeof(measBuf), 0xFF);
        Serial.print("# ");
        Serial.println(measBuf);
    }
}
