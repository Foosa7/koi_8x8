/*
 * AD7193.h — Driver for up to 8 AD7193 ADCs on a shared SPI bus
 *
 * Ported from working Pico SDK driver (example/ad7193.c).
 * Chip-select comes from a 74HC138 decoder (see HC138.h), not a per-device
 * GPIO. Data-ready is polled via the status register (no digitalRead on MISO,
 * no SPI.end()/begin() cycling).
 */

#ifndef AD7193_H
#define AD7193_H

#include <Arduino.h>
#include <SPI.h>
#include "HC138.h"

// ============================================================================
// Register Addresses
// ============================================================================
#define AD7193_REG_COMM         0x00
#define AD7193_REG_STATUS       0x00
#define AD7193_REG_MODE         0x01
#define AD7193_REG_CONF         0x02
#define AD7193_REG_DATA         0x03
#define AD7193_REG_ID           0x04
#define AD7193_REG_GPOCON       0x05
#define AD7193_REG_OFFSET       0x06
#define AD7193_REG_FULLSCALE    0x07

// ============================================================================
// Communications Register
// ============================================================================
#define AD7193_COMM_WEN         (1 << 7)
#define AD7193_COMM_READ        (1 << 6)
#define AD7193_COMM_WRITE       (0 << 6)
#define AD7193_COMM_ADDR(x)     (((x) & 0x07) << 3)
#define AD7193_COMM_CREAD       (1 << 2)

// ============================================================================
// Status Register
// ============================================================================
#define AD7193_STAT_RDY         (1 << 7)
#define AD7193_STAT_ERR         (1 << 6)
#define AD7193_STAT_NOREF       (1 << 5)
#define AD7193_STAT_PARITY      (1 << 4)
#define AD7193_STAT_CH_MASK     0x0F

// ============================================================================
// Mode Register (24-bit)
// ============================================================================
#define AD7193_MODE_CONT        (0UL << 21)
#define AD7193_MODE_SINGLE      (1UL << 21)
#define AD7193_MODE_IDLE        (2UL << 21)
#define AD7193_MODE_PWRDN       (3UL << 21)
#define AD7193_MODE_CAL_INT_Z   (4UL << 21)
#define AD7193_MODE_CAL_INT_F   (5UL << 21)
#define AD7193_MODE_CAL_SYS_Z   (6UL << 21)
#define AD7193_MODE_CAL_SYS_F   (7UL << 21)
#define AD7193_MODE_SEL_MASK    (7UL << 21)

#define AD7193_MODE_DAT_STA     (1UL << 20)

// Clock source select, MODE bits CLK1:CLK0 (19:18). Per the AD7193 datasheet:
//   00 = external crystal (MCLK1/MCLK2)   01 = external clock on MCLK1
//   10 = internal 4.92 MHz (MCLK2 tri-st) 11 = internal 4.92 MHz (out on MCLK2)
// These boards have no crystal — use _INT. (Do NOT use 0<<18 for "internal".)
#define AD7193_MODE_CLKSRC_XTAL    (0UL << 18)
#define AD7193_MODE_CLKSRC_EXT     (1UL << 18)
#define AD7193_MODE_CLKSRC_INT     (2UL << 18)
#define AD7193_MODE_CLKSRC_INT_OUT (3UL << 18)

#define AD7193_MODE_SINC3       (1UL << 15)
#define AD7193_MODE_SCYCLE      (1UL << 11)
#define AD7193_MODE_REJ60       (1UL << 10)

#define AD7193_MODE_RATE(x)     ((x) & 0x3FF)

// ============================================================================
// Configuration Register (24-bit)
// ============================================================================
#define AD7193_CONF_CHOP        (1UL << 23)
#define AD7193_CONF_PSEUDO      (1UL << 18)
#define AD7193_CONF_CHAN(x)     (1UL << (8 + (x)))
#define AD7193_CONF_CHAN_MASK   (0x3FFUL << 8)

#define AD7193_CH_AIN1          (1UL << 8)
#define AD7193_CH_AIN2          (1UL << 9)
#define AD7193_CH_AIN3          (1UL << 10)
#define AD7193_CH_AIN4          (1UL << 11)
#define AD7193_CH_AIN5          (1UL << 12)
#define AD7193_CH_AIN6          (1UL << 13)
#define AD7193_CH_AIN7          (1UL << 14)
#define AD7193_CH_AIN8          (1UL << 15)
#define AD7193_CH_TEMP          (1UL << 16)
#define AD7193_CH_SHORT         (1UL << 17)

#define AD7193_CONF_BUF         (1UL << 4)
#define AD7193_CONF_UNIPOLAR    (1UL << 3)
#define AD7193_CONF_BURN        (1UL << 7)
#define AD7193_CONF_REFSEL      (1UL << 20)  // Reference select: 0=REFIN1, 1=REFIN2
#define AD7193_CONF_REFDET      (1UL << 6)

#define AD7193_CONF_GAIN_1      0x00
#define AD7193_CONF_GAIN_8      0x03
#define AD7193_CONF_GAIN_16     0x04
#define AD7193_CONF_GAIN_32     0x05
#define AD7193_CONF_GAIN_64     0x06
#define AD7193_CONF_GAIN_128    0x07
#define AD7193_CONF_GAIN_MASK   0x07

// ============================================================================
// ID Register — LOWER nibble is 0x02 for AD7193 (not upper nibble!)
// ============================================================================
#define AD7193_ID_MASK          0x0F
#define AD7193_ID_VALUE         0x02

// ============================================================================
// Register sizes
// ============================================================================
static const uint8_t AD7193_REG_SIZE[] = {
    1, 3, 3, 3, 1, 1, 3, 3
};

// Max devices a 74HC138 bank can address; register caches are sized to this.
#define AD7193_NUM_DEVICES      8

// ============================================================================
// AD7193 Multi-Device Driver (SN74LV595 CS management)
// ============================================================================
class AD7193Driver {
public:
    /**
     * @param select      74HC138 selector for the ADC bank
     * @param spiPort     MbedSPI instance on the ADC bus
     * @param numDevices  Populated AD7193 count (1..8)
     */
    AD7193Driver(HC138* select, SPIClass* spiPort, uint8_t numDevices = 8);

    bool begin();

    void reset(uint8_t device);
    void resetAll();

    uint32_t readRegister(uint8_t device, uint8_t regAddr);
    void writeRegister(uint8_t device, uint8_t regAddr, uint32_t value);

    uint8_t readID(uint8_t device);

    /** Poll status register for RDY bit. */
    bool waitForReady(uint8_t device, uint32_t timeoutMs = 5000);

    /** Single conversion on a channel (matching working driver's approach). */
    uint32_t singleConversion(uint8_t device, uint32_t channelMask);

    /**
     * Presence probe: start one single conversion and report whether it
     * actually COMPLETED (RDY toggled busy→ready within `timeoutMs`) rather
     * than returning the code. Unlike singleConversion (which returns 0 both
     * for a genuine ~0 reading and a timeout), this distinguishes a board that
     * is truly present and clocking from an absent/floating/desynced one (which
     * times out, STATUS=0x80). Requires the mode register to already select the
     * internal clock (else RDY never asserts). The conversion result is read out
     * (to keep DATA alignment) and discarded.
     */
    bool probeConversion(uint8_t device, uint32_t channelMask,
                         uint32_t timeoutMs = 1000);

    /**
     * Scan the `nChan` lowest channels (AIN1..AINn) of a board in ONE
     * continuous-conversion sequencer pass. Enables those channels, runs the
     * part in continuous mode with DAT_STA so every sample is tagged with its
     * channel, reads one sample per channel, then returns to idle. Fills
     * codes[ch] (24-bit) for each channel read and returns a bitmask of the
     * channels successfully captured. Much faster than nChan single
     * conversions — it pays the filter settling per channel but NOT the
     * per-conversion stop/restart overhead of single mode. `baseConf` supplies
     * the non-channel CONF bits (pseudo/ref/unipolar/buf/gain/chop); the channel
     * bits are replaced internally. `rate` is the filter word (FS). `modeFlags`
     * supplies the clock source and any SINC3/REJ60 filter bits (if no clock-
     * source bit is set it defaults to the internal clock).
     */
    uint8_t scanContinuous(uint8_t device, uint32_t baseConf, uint16_t rate,
                           uint8_t nChan, uint32_t* codes, uint32_t modeFlags = 0);

    /** Read data register (3 bytes). */
    uint32_t readData(uint8_t device);

    void calibrateInternalZero(uint8_t device);
    void calibrateInternalFull(uint8_t device);

    /** Hardware bring-up probe for scoping the SPI0 read path (MISO=GP4).
     *  For `durationMs`, repeatedly select `device` (ADC_EN pulses low), clock a
     *  real ID-register read, and capture the returned bytes — giving a steady,
     *  scope-triggerable waveform on ADC_EN / SCK / MISO. Prints a '#'-framed
     *  summary (iterations, OR of all MISO bytes, non-zero count, first samples)
     *  so a floating/stuck MISO (all 0x00, occasional 0xFF) is visible without a
     *  scope too. Diagnostic only — does not touch config/cal. */
    void misoProbe(uint8_t device, uint32_t durationMs);

    /** Enable/disable runtime diagnostic prints (WARN/timeout). Default OFF so
     *  they never corrupt the host command protocol's one-line replies. */
    void setVerbose(bool v) { _verbose = v; }

    static float codeToVoltage(uint32_t raw, float vref, uint8_t gain, bool unipolar);

    // Cached register values per device (matching working driver)
    uint32_t modeReg[AD7193_NUM_DEVICES];
    uint32_t confReg[AD7193_NUM_DEVICES];

private:
    SPIClass* _spi;
    HC138*    _select;
    uint8_t   _numDevices;
    bool      _verbose;   // runtime diagnostic prints (default false)

    // SPI settings: Mode 3, 1 MHz (matching working driver speed)
    static const SPISettings SPI_SETTINGS;

    void _selectDevice(uint8_t device);
    void _deselectAll();
    uint8_t _commByte(uint8_t regAddr, bool read);
};

#endif
