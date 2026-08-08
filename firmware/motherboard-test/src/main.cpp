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
 * XTR200 EF error flags: private bit-banged chain, CP=GP16, Q7=GP17, PL=GP18
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
 *   *IDN?               -> KOI,8x8,fw1.1
 *   *RST                -> OK *RST   (all currents 0, all front-ends disabled)
 *   ISETA i0 i1 .. i63  -> OK ISETA  (set all 64 currents in mA, g-order)
 *   ISET g mA           -> OK ISET <g>  (set one channel)
 *   MEASA?              -> v0,..,v63 (measure all 64; "nan" for unpopulated)
 *   MEASA? <mask>       -> v0,..,v63 (only boards whose bit is set in mask)
 *   MEAS? g             -> <volts>   (measure one channel; "nan" if absent)
 *   XTR <enmask>        -> OK XTR 0x..  (bit b = 1 enables board b's front-ends)
 *   AVG n               -> OK AVG <n>  (1..64 on-micro averages per measurement)
 *   RATE fs             -> OK RATE <fs>  (AD7193 filter word 1..1023)
 *   STREAM ON|OFF       -> OK STREAM  (periodic '# v0,..' dump for eyeballing)
 *   RESCAN              -> OK RESCAN active=0x.. new=0x..  (re-detect boards)
 *   FAULT?              -> OK FAULT latch=0x.. now=0x.. valid=0x..
 *                          64-bit masks in g-order (bit g = channel g) of the
 *                          XTR200 EF flags: `latch` is sticky since boot or the
 *                          last FAULTCLR, `now` is the live read. `valid` is a
 *                          per-BOARD mask of which slots the EF chain can be
 *                          trusted for (see efValidMask).
 *   FAULTCLR            -> OK FAULTCLR  (clear the sticky latch and re-arm the
 *                          channels; setpoints stay 0 — the host must re-ISET)
 *   FAULTEN ON|OFF      -> OK FAULTEN ..  (auto-zero a channel on fault;
 *                          default ON. OFF only reports, for bench debugging.)
 *   PING? b             -> OK PING <b> 1|0  (board liveness: ID + one real ADC
 *                          conversion, non-mutating; 0 for an undetected board
 *                          — use RESCAN to adopt it. The ADC is the only
 *                          readback on a board, so a passing PING is the proxy
 *                          that the whole board (incl. the write-only DAC's
 *                          shared select path) is seated and powered.)
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
#include "EFChain.h"

// ── Shared 74HC138 chip-select ──────────────────────────
#define PIN_ADDR0     20
#define PIN_ADDR1     21
#define PIN_ADDR2     22
#define PIN_ADC_EN     5
#define PIN_DAC_EN     6
#define PIN_XTR_RCLK   7

// ── XTR200 EF chain (SN74LV165 ×8, private 3-wire bus) ──
// Taken from hardware/motherboard/production/netlist.ipc, where CP/Q7_8/PL land
// on Pico header pins 21/22/24. Every other net in that file agrees with the
// pin numbers above, so these are almost certainly right — but that netlist
// also shows GP7 unconnected while XTR_RCLK demonstrably works there, so it
// lags the built board somewhere. Verify these three on the bench before
// trusting a clean FAULT? reading.
#define PIN_EF_CP     16
#define PIN_EF_Q7     17
#define PIN_EF_PL     18

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
arduino::MbedSPI dacSpi(       12,   11,   10);   // DAC, write-only (MISO unused)
arduino::MbedSPI adcSpi(        4,    3,    2);   // ADC + 595 share this

DAC80508     dac(&dacSelect, &dacSpi, NUM_BOARDS, VREF);
AD7193Driver adc(&adcSelect, &adcSpi, NUM_BOARDS);
XTR595       xtr(&adcSpi, PIN_XTR_RCLK);
EFChain      efChain(PIN_EF_PL, PIN_EF_CP, PIN_EF_Q7);

// ── Runtime state ───────────────────────────────────────
bool     boardActive[NUM_BOARDS];
float    setpoint_mA[TOTAL_CH];
float    calSlope[TOTAL_CH];
float    calOffset[TOTAL_CH];
uint8_t  avgCount = 1;            // on-micro averages per measurement
uint16_t adcRate  = 8;           // AD7193 filter word (FS) used for fast scans
                                 //   FS=8 ≈ 11 ms/ch; lower=faster/noisier
                                 //   (RATE 4 ≈ 5.6 ms/ch), higher=cleaner.
uint8_t  xtrState = 0xFF;        // OD bits: 1 = disabled, 0 = enabled

// ── XTR200 fault state ──────────────────────────────────
// efNow   : live EF read, bit ch = channel ch asserting EF right now
// efLatch : sticky since boot / last FAULTCLR. A latched channel is FORCED to
//           0 mA by writeChannel() until cleared, so a fault cannot be undone
//           by the host simply re-sending a setpoint.
uint8_t  efNow[NUM_BOARDS];
uint8_t  efLatch[NUM_BOARDS];
bool     faultTrip = true;       // auto-zero on fault (FAULTEN)
uint32_t faultLast = 0;
#define  FAULT_POLL_MS  20

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

static void writeChannel(uint8_t g) {
    uint8_t b  = g / CH_PER_BOARD;
    uint8_t ch = g % CH_PER_BOARD;             // physical channel
    if (!boardActive[b]) return;
    // A latched fault pins the channel at 0 mA regardless of the setpoint, so a
    // host that keeps re-sending ISET cannot re-energise a broken heater. Only
    // FAULTCLR re-arms it.
    uint16_t code = (efLatch[b] & (1u << ch)) ? 0 : mAToCode(g, setpoint_mA[g]);
    dac.setDAC(b, DAC_CH_FOR_PHYS[ch], code);
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
    uint32_t raw = (uint32_t)(acc / n);
    // 24-bit unipolar, gain 1: V = raw / 2^24 × VREF.
    return ((double)raw / 16777216.0) * (double)ADC_VREF;
}

static inline double codeToVolts(uint32_t raw) {
    // 24-bit unipolar, gain 1: V = raw / 2^24 × VREF. Double keeps full bits.
    return ((double)raw / 16777216.0) * (double)ADC_VREF;
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
                                             CH_PER_BOARD, codes);
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
    // Pseudo-differential, REFIN2, unipolar, gain 1, channel AIN1.
    // BUF is deliberately NOT set: the internal buffer needs AGND+250 mV of
    // headroom, and behind the 6:1 sense divider a low-current channel sits
    // below that — which is what produced the ~3 mV offset. External OPA2333
    // followers (U13-U16) buffer instead. See CLAUDE.md.
    adc.confReg[b] = AD7193_CONF_PSEUDO | AD7193_CONF_REFSEL | AD7193_CONF_REFDET |
                     AD7193_CONF_UNIPOLAR | AD7193_CONF_GAIN_1 |
                     AD7193_CONF_CHAN(0);
    adc.writeRegister(b, AD7193_REG_CONF, adc.confReg[b]);

    // Internal clock (no crystal on these boards), idle, rate 96.
    adc.modeReg[b] = AD7193_MODE_IDLE | AD7193_MODE_CLKSRC_INT | AD7193_MODE_RATE(96);
    adc.writeRegister(b, AD7193_REG_MODE, adc.modeReg[b]);
}

static void configureBoard(uint8_t b) {
    writeBoardConfig(b);
    adc.calibrateInternalZero(b);
    adc.calibrateInternalFull(b);
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

// ── XTR200 fault polling ────────────────────────────────
// Which board slots the EF chain can be trusted for. The '165s are daisy-
// chained board0 -> board1 -> ... -> board7 -> Pico, and an unpopulated slot
// removes that board's '165 and breaks the chain. Boards DOWNSTREAM of the
// highest gap (higher index, nearer the Pico) still clock out their own bits
// correctly; everything upstream of it shifts in garbage from a floating DS.
static uint8_t efValidMask() {
    int gap = -1;
    for (int b = 0; b < NUM_BOARDS; b++) if (!boardActive[b]) gap = b;
    uint8_t m = 0;
    for (int b = gap + 1; b < NUM_BOARDS; b++) m |= (uint8_t)(1u << b);
    return m;
}

// Read the chain, latch new faults, and zero the offending channels.
//
// Boards whose front ends are disabled (OD high) are skipped: with the output
// in high-Z the XTR200 cannot reach the commanded current and legitimately
// raises EF, which would otherwise latch a fault on every channel of an idle
// board. An ENABLED board sitting at 0 mA reads clean, because open-circuit
// detection needs VIN > 350 mV to arm in the first place.
static void pollFaults() {
    uint8_t raw[NUM_BOARDS];
    efChain.read(raw, NUM_BOARDS);

    uint8_t valid = efValidMask();
    for (uint8_t b = 0; b < NUM_BOARDS; b++) {
        bool enabled = !(xtrState & (1u << b));
        if (!(valid & (1u << b)) || !enabled) { efNow[b] = 0; continue; }

        efNow[b] = raw[b];
        uint8_t newly = (uint8_t)(raw[b] & ~efLatch[b]);
        efLatch[b] |= raw[b];
        if (!faultTrip || !newly) continue;

        for (uint8_t ch = 0; ch < CH_PER_BOARD; ch++) {
            if (!(newly & (1u << ch))) continue;
            uint16_t g = b * CH_PER_BOARD + ch;
            setpoint_mA[g] = 0.0f;
            writeChannel((uint8_t)g);     // efLatch is already set -> forces 0
        }
    }
}

// Pack a per-board bitmap into one 64-bit g-ordered mask (bit g = channel g).
static uint64_t efPack(const uint8_t* per) {
    uint64_t m = 0;
    for (uint8_t b = 0; b < NUM_BOARDS; b++)
        m |= (uint64_t)per[b] << (b * CH_PER_BOARD);
    return m;
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
        reply("KOI,8x8,fw1.1");

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
        for (uint8_t b = 0; b < NUM_BOARDS; b++) {
            if (!boardActive[b]) continue;      // keep MEAS? single-conv in sync
            adc.modeReg[b] = (adc.modeReg[b] & ~0x3FFUL) | AD7193_MODE_RATE(fs);
            adc.writeRegister(b, AD7193_REG_MODE, adc.modeReg[b]);
        }
        char buf[20];
        snprintf(buf, sizeof(buf), "OK RATE %d", fs);
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

    } else if (strcasecmp(cmd, "FAULT?") == 0) {
        pollFaults();                       // answer from a fresh read
        // Printed as two 32-bit halves: newlib-nano's printf drops %ll support,
        // so a single %016llX would emit garbage here.
        uint64_t l = efPack(efLatch), n = efPack(efNow);
        char buf[80];
        snprintf(buf, sizeof(buf),
                 "OK FAULT latch=0x%08lX%08lX now=0x%08lX%08lX valid=0x%02X",
                 (unsigned long)(l >> 32), (unsigned long)(l & 0xFFFFFFFFUL),
                 (unsigned long)(n >> 32), (unsigned long)(n & 0xFFFFFFFFUL),
                 efValidMask());
        reply(buf);

    } else if (strcasecmp(cmd, "FAULTCLR") == 0) {
        // Re-arm every channel. Setpoints were zeroed when the fault tripped
        // and are deliberately NOT restored — the host must re-ISET, so nothing
        // comes back on current by surprise.
        for (uint8_t b = 0; b < NUM_BOARDS; b++) { efLatch[b] = 0; efNow[b] = 0; }
        for (uint16_t g = 0; g < TOTAL_CH; g++) writeChannel((uint8_t)g);
        reply("OK FAULTCLR");

    } else if (strcasecmp(cmd, "FAULTEN") == 0) {
        char* a = strtok_r(NULL, " ,\t", &save);
        if      (a && strcasecmp(a, "ON")  == 0) { faultTrip = true;  reply("OK FAULTEN ON"); }
        else if (a && strcasecmp(a, "OFF") == 0) { faultTrip = false; reply("OK FAULTEN OFF"); }
        else replyErr("usage: FAULTEN ON|OFF");

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
    for (uint8_t b = 0; b < NUM_BOARDS; b++) { efNow[b] = 0; efLatch[b] = 0; }

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

    // Configure + calibrate each confirmed-present ADC.
    for (uint8_t b = 0; b < NUM_BOARDS; b++)
        if (boardActive[b]) configureBoard(b);

    // Front-ends start disabled; load 0 mA everywhere before enabling.
    xtr.begin();                                  // OD all HIGH = disabled
    efChain.begin();
    for (uint16_t g = 0; g < TOTAL_CH; g++) writeChannel(g);

    // Enable populated boards' front-ends (still 0 mA, so safe).
    uint8_t enMask = 0;
    for (uint8_t b = 0; b < NUM_BOARDS; b++)
        if (boardActive[b]) enMask |= (uint8_t)(1 << b);
    setEnableMask(enMask);

    // First EF read, after the front ends settle. At 0 mA on an enabled board
    // this should be all-clear; anything set here is a real standing fault (or
    // a miswired chain), so report it rather than silently latching.
    delay(10);
    pollFaults();
    uint64_t f = efPack(efLatch);
    if (f) {
        char fb[48];
        snprintf(fb, sizeof(fb), "# EF FAULT at boot: 0x%08lX%08lX",
                 (unsigned long)(f >> 32), (unsigned long)(f & 0xFFFFFFFFUL));
        Serial.println(fb);
    }
    Serial.print("# EF chain valid boards: 0x");
    Serial.println(efValidMask(), HEX);

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

    // Background fault sweep. Cheap (~0.5 ms of bit-banging on a private bus,
    // so it disturbs neither SPI bank) and fast enough that a channel is cut
    // within ~20 ms of the XTR200 raising EF.
    if (millis() - faultLast >= FAULT_POLL_MS) {
        faultLast = millis();
        pollFaults();
    }

    // Optional eyeball stream (debug only; prefixed '#' so the host can ignore).
    if (streamOn && (millis() - streamLast >= streamInterval)) {
        streamLast = millis();
        buildMeasureLine(measBuf, sizeof(measBuf), 0xFF);
        Serial.print("# ");
        Serial.println(measBuf);
    }
}
