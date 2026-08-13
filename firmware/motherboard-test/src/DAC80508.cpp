/*
 * DAC80508.cpp — DAC80508ZCRTER parallel, write-only driver
 *
 * Each device is individually selected via the DAC 74HC138 decoder. A write is
 * a single 24-bit frame clocked while the device's CS is LOW; the data latches
 * on the CS rising edge when the decoder is deselected.
 */
#include "DAC80508.h"

// SPI settings: Mode 1 (CPOL=0, CPHA=1), MSB first, 1 MHz
static const SPISettings DAC_SPI_SETTINGS(1000000, MSBFIRST, SPI_MODE1);

// ═════════════════════════════════════════════════════════
// Constructor
// ═════════════════════════════════════════════════════════
DAC80508::DAC80508(HC138 *select, SPIClass *spi, uint8_t numDevices, float vref)
    : _select(select), _spi(spi), _numDevices(numDevices), _vref(vref) {
    if (_numDevices > DAC80508_MAX_DEVICES)
        _numDevices = DAC80508_MAX_DEVICES;
    if (_numDevices < 1)
        _numDevices = 1;
}

// ═════════════════════════════════════════════════════════
// Single 24-bit write frame to the selected device
// ═════════════════════════════════════════════════════════
void DAC80508::_write(uint8_t device, uint8_t cmdByte, uint16_t data) {
    if (device >= _numDevices) return;

    _select->selectDevice(device);   // CS LOW
    _spi->beginTransaction(DAC_SPI_SETTINGS);
    _spi->transfer(cmdByte);
    _spi->transfer((data >> 8) & 0xFF);
    _spi->transfer(data & 0xFF);
    _spi->endTransaction();
    _select->deselect();             // CS rising edge latches the frame
    delayMicroseconds(1);
}

// ═════════════════════════════════════════════════════════
// Initialize
// ═════════════════════════════════════════════════════════
bool DAC80508::begin() {
    _select->begin();
    _spi->begin();

    // Wait for DAC power-up
    delay(50);

    // ── Software reset every device ──────────────────────
    Serial.println("# [DAC] Software reset...");
    writeRegisterAll(DAC80508_REG_TRIGGER, DAC80508_TRIG_SOFT_RST);
    delay(100);   // allow reset to fully complete before configuring

    // ── SYNC: broadcast enabled, async (outputs update immediately) ──
    Serial.println("# [DAC] SYNC = 0xFF00 (broadcast ON, async mode)");
    writeRegisterAll(DAC80508_REG_SYNC, DAC80508_BRDCAST_ALL);
    delay(5);

    // ── CONFIG: external reference, all channels ON ──────
    // Write-only part — assert twice with margin so REF_PWDWN (external ref)
    // reliably takes (a single marginal frame otherwise leaves it on the
    // internal 2.5V reference).
    Serial.println("# [DAC] CONFIG = ext ref, all channels ON");
    writeRegisterAll(DAC80508_REG_CONFIG, DAC80508_CFG_REF_PWDWN);
    delay(5);
    writeRegisterAll(DAC80508_REG_CONFIG, DAC80508_CFG_REF_PWDWN);
    delay(5);

    // ── GAIN: REFDIV÷2 + 2× buffer (matches the TI reference) ──
    // With a 3.0V external ref on a 3.3V VDD, REFDIV÷2 keeps the reference
    // buffer input in range (1.5V); the 2× buffer restores the full 0-VREF
    // span, so net Vout = VREF × code/65535.
    Serial.println("# [DAC] GAIN = refdiv/2 + 2x buffer (full 0-VREF span)");
    writeRegisterAll(DAC80508_REG_GAIN, DAC80508_GAIN_ALL_2X);
    delay(5);

    // ── Zero all outputs ─────────────────────────────────
    writeRegisterAll(DAC80508_REG_BRDCAST, 0x0000);
    delay(5);

    Serial.println("# [DAC] Initialization complete (write-only, not verified).");
    return true;
}

// ═════════════════════════════════════════════════════════
// Mid-session reconfigure of one device (see header)
// ═════════════════════════════════════════════════════════
void DAC80508::reinit(uint8_t device) {
    writeRegister(device, DAC80508_REG_TRIGGER, DAC80508_TRIG_SOFT_RST);
    delay(100);   // same post-reset margin begin() uses
    writeRegister(device, DAC80508_REG_SYNC, DAC80508_BRDCAST_ALL);
    delay(5);
    // External ref, double-written so REF_PWDWN reliably takes (write-only bus).
    writeRegister(device, DAC80508_REG_CONFIG, DAC80508_CFG_REF_PWDWN);
    delay(5);
    writeRegister(device, DAC80508_REG_CONFIG, DAC80508_CFG_REF_PWDWN);
    delay(5);
    writeRegister(device, DAC80508_REG_GAIN, DAC80508_GAIN_ALL_2X);
    delay(5);
}

// ═════════════════════════════════════════════════════════
// Register writes
// ═════════════════════════════════════════════════════════
void DAC80508::writeRegister(uint8_t device, uint8_t addr, uint16_t data) {
    _write(device, DAC80508_CMD_WRITE(addr), data);
}

void DAC80508::writeRegisterAll(uint8_t addr, uint16_t data) {
    for (uint8_t d = 0; d < _numDevices; d++) {
        _write(d, DAC80508_CMD_WRITE(addr), data);
    }
}

// ═════════════════════════════════════════════════════════
// Channel helpers
// ═════════════════════════════════════════════════════════
void DAC80508::setDAC(uint8_t device, uint8_t channel, uint16_t value) {
    if (channel > 7) return;
    writeRegister(device, DAC80508_REG_DAC0 + channel, value);
}

void DAC80508::setVoltage(uint8_t device, uint8_t channel, float voltage) {
    setDAC(device, channel, voltageToCode(voltage, _vref));
}

// ═════════════════════════════════════════════════════════
// Conversion helpers
// ═════════════════════════════════════════════════════════
float DAC80508::codeToVoltage(uint16_t code, float vref) {
    return (vref * (float)code) / 65535.0f;
}

uint16_t DAC80508::voltageToCode(float voltage, float vref) {
    if (voltage <= 0.0f) return 0;
    if (voltage >= vref) return 0xFFFF;
    return (uint16_t)((voltage / vref) * 65535.0f + 0.5f);
}
