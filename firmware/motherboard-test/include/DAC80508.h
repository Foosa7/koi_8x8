/*
 * DAC80508.h — DAC80508ZCRTER Driver (Arduino, RP2040), parallel + write-only
 *
 * Up to 8 DAC80508 devices share SPI1 in PARALLEL (not daisy-chained). Each is
 * addressed individually through a 74HC138 decoder (see HC138.h) on the DAC_EN
 * enable. The RTER part has NO SDO (that pin is repurposed as CLR, tied high),
 * so the bus is WRITE-ONLY — no register reads, no device-ID/CRC read-back.
 *
 * SPI Frame (24-bit, MSB first):
 *   Byte 0: [R/W | 0 | 0 | 0 | A3 | A2 | A1 | A0]   (R/W=0 for write)
 *   Byte 1: [D15..D8]
 *   Byte 2: [D7..D0]
 * Data latches into the device on the CS rising edge (decoder deselect).
 */

#ifndef DAC80508_H
#define DAC80508_H

#include <Arduino.h>
#include <SPI.h>
#include "HC138.h"

// ── Maximum number of devices a 74HC138 bank can address ─
#ifndef DAC80508_MAX_DEVICES
#define DAC80508_MAX_DEVICES 8
#endif

// ── Register Addresses ──────────────────────────────────
#define DAC80508_REG_NOP       0x00
#define DAC80508_REG_DEVICEID  0x01
#define DAC80508_REG_SYNC      0x02
#define DAC80508_REG_CONFIG    0x03
#define DAC80508_REG_GAIN      0x04
#define DAC80508_REG_TRIGGER   0x05
#define DAC80508_REG_BRDCAST   0x06
#define DAC80508_REG_STATUS    0x07
#define DAC80508_REG_DAC0      0x08
#define DAC80508_REG_DAC1      0x09
#define DAC80508_REG_DAC2      0x0A
#define DAC80508_REG_DAC3      0x0B
#define DAC80508_REG_DAC4      0x0C
#define DAC80508_REG_DAC5      0x0D
#define DAC80508_REG_DAC6      0x0E
#define DAC80508_REG_DAC7      0x0F

// ── CONFIG Register Bits (address 0x03) ─────────────────
#define DAC80508_CFG_ALM_SEL    (1 << 13)  // Alarm select: 0=CRC, 1=REF
#define DAC80508_CFG_ALM_EN     (1 << 12)  // Alarm enable on SDO/ALARM pin
#define DAC80508_CFG_CRC_EN     (1 << 11)  // CRC enable
#define DAC80508_CFG_FSDO       (1 << 10)  // SDO on rising edge (1) vs falling (0)
#define DAC80508_CFG_DSDO       (1 <<  9)  // Disable SDO (1=hi-Z)
#define DAC80508_CFG_REF_PWDWN  (1 <<  8)  // Power down internal reference

// Per-channel power-down bits (CONFIG register bits 7:0)
#define DAC80508_CFG_DAC7_PWDWN (1 << 7)
#define DAC80508_CFG_DAC6_PWDWN (1 << 6)
#define DAC80508_CFG_DAC5_PWDWN (1 << 5)
#define DAC80508_CFG_DAC4_PWDWN (1 << 4)
#define DAC80508_CFG_DAC3_PWDWN (1 << 3)
#define DAC80508_CFG_DAC2_PWDWN (1 << 2)
#define DAC80508_CFG_DAC1_PWDWN (1 << 1)
#define DAC80508_CFG_DAC0_PWDWN (1 << 0)

// ── GAIN Register Bits (address 0x04) ───────────────────
// Bit    8: REFDIV-EN — 0=no division, 1=divide reference by 2
// Bits 7:0: BUFF[7:0]-GAIN — 0=1x, 1=2x per channel
#define DAC80508_GAIN_REFDIV    (1 << 8)
#define DAC80508_GAIN_BUFF(ch)  (1 << (ch))
#define DAC80508_GAIN_ALL_2X    0x01FF  // REFDIV÷2 + 2× gain all channels

// ── TRIGGER Register Bits (address 0x05) ────────────────
#define DAC80508_TRIG_SOFT_RST  (0x000A)   // Software reset (write 0x000A)
#define DAC80508_TRIG_LDAC(ch)  (1 << (ch)) // Software LDAC for channel

// ── SYNC Register Bits (address 0x02) ───────────────────
// Bits 15:8: DAC[7:0]-BRDCAST-EN (1=broadcast register affects channel)
// Bits  7:0: DAC[7:0]-SYNC-EN   (1=synchronous mode, 0=async/immediate)
#define DAC80508_BRDCAST_EN(ch) (1 << (8 + (ch)))
#define DAC80508_SYNC_EN(ch)    (1 << (ch))
#define DAC80508_BRDCAST_ALL    0xFF00  // Enable broadcast for all channels

// ── STATUS Register Bits (address 0x07, read-only) ──────
#define DAC80508_STATUS_CRC_ALM  (1 << 1)  // CRC alarm
#define DAC80508_STATUS_REF_ALM  (1 << 0)  // Reference alarm

// ── Command byte helper (write only — part has no SDO) ──
#define DAC80508_CMD_WRITE(addr) ((addr) & 0x0F)

// ═════════════════════════════════════════════════════════
class DAC80508 {
public:
    /**
     * @param select      74HC138 selector for the DAC bank
     * @param spi         SPIClass instance for the DAC bus (SPI1)
     * @param numDevices  Populated DAC80508 count (1..8)
     * @param vref        External reference voltage (default 3.0V)
     */
    DAC80508(HC138 *select, SPIClass *spi, uint8_t numDevices = 8,
             float vref = 3.0f);

    /**
     * Initialize SPI and apply reset / SYNC / CONFIG / GAIN defaults to every
     * device. Write-only — returns true unconditionally (no readback to verify).
     */
    bool begin();

    /**
     * Re-run one device's full configuration mid-session: soft reset, then
     * SYNC / CONFIG (external ref, double-written) / GAIN. Recovery path for a
     * DAC that browned out back to its internal-2.5V-reference defaults — the
     * bus is write-only, so rewriting is the only possible fix. Outputs are at
     * zero scale afterwards; the caller reloads the channel setpoints.
     */
    void reinit(uint8_t device);

    /** Write a register on a single device (0 = decoder CS1). */
    void writeRegister(uint8_t device, uint8_t addr, uint16_t data);

    /** Write the same register value to every populated device (sequentially). */
    void writeRegisterAll(uint8_t addr, uint16_t data);

    /**
     * Set a DAC channel output (raw 16-bit code).
     * @param device  Device index (0-based)
     * @param channel DAC channel (0-7)
     * @param value   16-bit DAC code
     */
    void setDAC(uint8_t device, uint8_t channel, uint16_t value);

    /**
     * Set a DAC channel output voltage (0 to VREF).
     */
    void setVoltage(uint8_t device, uint8_t channel, float voltage);

    /** Convert 16-bit DAC code to voltage. */
    static float codeToVoltage(uint16_t code, float vref);

    /** Convert voltage to 16-bit DAC code. */
    static uint16_t voltageToCode(float voltage, float vref);

private:
    HC138    *_select;
    SPIClass *_spi;
    uint8_t   _numDevices;
    float     _vref;

    // Send one 24-bit write frame to the selected device.
    void _write(uint8_t device, uint8_t cmdByte, uint16_t data);
};

#endif // DAC80508_H
