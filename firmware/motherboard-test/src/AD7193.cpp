/*
 * AD7193.cpp — Ported from working Pico SDK driver (example/ad7193.c)
 *
 * Key points:
 *  1. Status-register polling for data-ready (no digitalRead on MISO)
 *  2. 1 MHz SPI speed (matches working code)
 *  3. ID check uses LOWER nibble (0x0F mask, expect 0x02)
 *  4. Cached mode/config registers (matches working driver pattern)
 *
 * Chip-select via 74HC138 (see HC138.h): _selectDevice()/_deselectAll()
 * just drive the shared address lines + the ADC_EN enable. While enabled the
 * selected CS stays LOW for the whole transaction; deselecting raises CS,
 * which resets the AD7193 serial interface.
 *
 * Bus wiring (SPI0):
 *   GP2 = SCK   → AD7193 SCLK (all) + SN74LV595 SRCLK
 *   GP3 = MOSI  → AD7193 DIN  (all) + SN74LV595 SER
 *   GP4 = MISO  ← AD7193 DOUT (selected device)
 */

#include "AD7193.h"

// 1 MHz, Mode 3, MSB first — matches working Pico SDK driver
const SPISettings AD7193Driver::SPI_SETTINGS(1000000, MSBFIRST, SPI_MODE3);


AD7193Driver::AD7193Driver(HC138* select, SPIClass* spiPort, uint8_t numDevices)
    : _spi(spiPort), _select(select), _numDevices(numDevices), _verbose(false)
{
    if (_numDevices > AD7193_NUM_DEVICES) _numDevices = AD7193_NUM_DEVICES;
    if (_numDevices < 1) _numDevices = 1;
    for (uint8_t i = 0; i < AD7193_NUM_DEVICES; i++) {
        modeReg[i] = 0;
        confReg[i] = 0;
    }
}


// ============================================================================
// CS Management via 74HC138 decoder
// ============================================================================

void AD7193Driver::_selectDevice(uint8_t device) {
    if (device >= AD7193_NUM_DEVICES) return;
    _select->selectDevice(device);   // drives this device's CS LOW
    delayMicroseconds(2);            // settling time after CS assertion
}

void AD7193Driver::_deselectAll() {
    _select->deselect();             // all ADC CS HIGH → serial interface resets
    delayMicroseconds(2);
}


// ============================================================================
// Communications Register
// ============================================================================

uint8_t AD7193Driver::_commByte(uint8_t regAddr, bool read) {
    // WEN=0, R/W, ADDR[2:0], CREAD=0
    return (read ? AD7193_COMM_READ : AD7193_COMM_WRITE) | AD7193_COMM_ADDR(regAddr);
}


// ============================================================================
// Register Read/Write — matches working driver exactly
// ============================================================================

uint32_t AD7193Driver::readRegister(uint8_t device, uint8_t regAddr) {
    if (device >= AD7193_NUM_DEVICES || regAddr > 7) return 0;

    uint8_t size = AD7193_REG_SIZE[regAddr & 0x07];
    uint8_t comm = _commByte(regAddr, true);

    _selectDevice(device);

    _spi->beginTransaction(SPI_SETTINGS);

    // Send comm byte (tells ADC which register to read)
    _spi->transfer(comm);

    // Read register bytes MSB first
    uint32_t result = 0;
    for (uint8_t i = 0; i < size; i++) {
        result = (result << 8) | _spi->transfer(0x00);
    }

    _spi->endTransaction();
    _deselectAll();

    return result;
}

void AD7193Driver::writeRegister(uint8_t device, uint8_t regAddr, uint32_t value) {
    if (device >= AD7193_NUM_DEVICES || regAddr > 7) return;

    uint8_t size = AD7193_REG_SIZE[regAddr & 0x07];
    uint8_t comm = _commByte(regAddr, false);

    // Serialize value MSB first into tx_buf (matching working driver)
    uint8_t tx_buf[4];
    uint32_t v = value;
    for (int i = size - 1; i >= 0; i--) {
        tx_buf[i] = v & 0xFF;
        v >>= 8;
    }

    _selectDevice(device);

    _spi->beginTransaction(SPI_SETTINGS);
    _spi->transfer(comm);
    for (uint8_t i = 0; i < size; i++) {
        _spi->transfer(tx_buf[i]);
    }
    _spi->endTransaction();

    _deselectAll();
}


// ============================================================================
// Reset
// ============================================================================

void AD7193Driver::reset(uint8_t device) {
    if (device >= AD7193_NUM_DEVICES) return;

    // 5 bytes of 0xFF = 40 bits of 1s (matching working driver)
    _selectDevice(device);

    _spi->beginTransaction(SPI_SETTINGS);
    for (uint8_t i = 0; i < 5; i++) {
        _spi->transfer(0xFF);
    }
    _spi->endTransaction();

    _deselectAll();
    delay(5);
}

void AD7193Driver::resetAll() {
    for (uint8_t i = 0; i < _numDevices; i++) {
        reset(i);
    }
}


// ============================================================================
// Init
// ============================================================================

bool AD7193Driver::begin() {
    _select->begin();
    _spi->begin();

    _deselectAll();
    delay(10);

    resetAll();
    delay(50);

    bool allOk = true;
    for (uint8_t i = 0; i < _numDevices; i++) {
        uint8_t id = readID(i);

        // ID check: LOWER nibble should be 0x02 (matching working driver)
        // Diagnostics are '#'-prefixed: the host protocol promises exactly one
        // unprefixed reply line per command, and these can print mid-session.
        if ((id & AD7193_ID_MASK) != AD7193_ID_VALUE) {
            Serial.print("# AD7193 #"); Serial.print(i);
            Serial.print(" ID mismatch: 0x"); Serial.println(id, HEX);
            allOk = false;
        } else {
            Serial.print("# AD7193 #"); Serial.print(i);
            Serial.print(" OK, ID=0x"); Serial.println(id, HEX);

            // Cache register defaults (matching working driver pattern)
            modeReg[i] = readRegister(i, AD7193_REG_MODE);
            confReg[i] = readRegister(i, AD7193_REG_CONF);
        }
    }

    return allOk;
}


// ============================================================================
// ID
// ============================================================================

uint8_t AD7193Driver::readID(uint8_t device) {
    return (uint8_t)readRegister(device, AD7193_REG_ID);
}


// ============================================================================
// Data Ready — Status Register Polling (matching working driver)
// ============================================================================

bool AD7193Driver::waitForReady(uint8_t device, uint32_t timeoutMs) {
    uint32_t start = millis();

    // Phase 1: Wait for RDY=1 (device has started processing).
    // This prevents catching a stale RDY=0 from a previous operation.
    // If the ADC is fast, we might miss the RDY=1 window, so timeout
    // quickly and fall through to phase 2.
    uint32_t busyTimeout = min(timeoutMs, (uint32_t)500);
    bool seenBusy = false;
    while ((millis() - start) < busyTimeout) {
        uint8_t status = (uint8_t)readRegister(device, AD7193_REG_STATUS);
        if (status & AD7193_STAT_RDY) {
            seenBusy = true;
            break;
        }
        delayMicroseconds(20);
    }

    if (!seenBusy && _verbose) {
        // We never saw RDY=1. Possible the operation is very fast
        // or the write didn't take effect. Log it for debug.
        Serial.print("# [WARN] Device #");
        Serial.print(device);
        Serial.println(" never showed RDY=1 (busy). Check mode register write.");
    }

    // Phase 2: Wait for RDY=0 (operation complete)
    while ((millis() - start) < timeoutMs) {
        uint8_t status = (uint8_t)readRegister(device, AD7193_REG_STATUS);
        if (!(status & AD7193_STAT_RDY)) {
            return true;
        }
        delayMicroseconds(50);
    }

    if (_verbose) {
        uint8_t finalStatus = (uint8_t)readRegister(device, AD7193_REG_STATUS);
        Serial.print("# [AD7193] Timeout on device #");
        Serial.print(device);
        Serial.print(" (STATUS=0x");
        Serial.print(finalStatus, HEX);
        Serial.println(")");
    }
    return false;
}


// ============================================================================
// Single Conversion — exact port of working driver's ad7193_single_conversion()
// ============================================================================

uint32_t AD7193Driver::singleConversion(uint8_t device, uint32_t channelMask) {
    if (device >= AD7193_NUM_DEVICES) return 0;

    // Set channel (matching working driver: modify cached conf, write it)
    confReg[device] = (confReg[device] & ~AD7193_CONF_CHAN_MASK) |
                      (channelMask & AD7193_CONF_CHAN_MASK);
    writeRegister(device, AD7193_REG_CONF, confReg[device]);

    // Start single conversion (matching working driver)
    uint32_t modeBackup = modeReg[device];
    modeReg[device] = (modeReg[device] & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_SINGLE;
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);

    // Small guard so RDY can assert HIGH (busy) before we start polling; then
    // waitForReady catches the busy→ready edge. A long blind delay here would
    // both waste time and (ironically) cause us to MISS the RDY=1 window on
    // fast filter rates, because the conversion can finish during the delay.
    delayMicroseconds(50);

    // Wait for conversion
    if (!waitForReady(device, 5000)) {
        if (_verbose) {
            Serial.print("# [AD7193] Single conversion timeout, device #");
            Serial.println(device);
        }
        modeReg[device] = modeBackup;
        return 0;
    }

    // Read data
    uint32_t data = readData(device);

    // Restore mode
    modeReg[device] = modeBackup;

    return data;
}


// ============================================================================
// Presence probe — like singleConversion, but reports whether the conversion
// actually completed (true) vs timed out (false), instead of the data.
// ============================================================================

bool AD7193Driver::probeConversion(uint8_t device, uint32_t channelMask,
                                   uint32_t timeoutMs) {
    if (device >= AD7193_NUM_DEVICES) return false;

    confReg[device] = (confReg[device] & ~AD7193_CONF_CHAN_MASK) |
                      (channelMask & AD7193_CONF_CHAN_MASK);
    writeRegister(device, AD7193_REG_CONF, confReg[device]);

    uint32_t modeBackup = modeReg[device];
    modeReg[device] = (modeReg[device] & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_SINGLE;
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);

    delayMicroseconds(50);                 // let RDY assert busy before polling

    bool ok = waitForReady(device, timeoutMs);
    if (ok) readData(device);              // drain DATA so later reads stay aligned

    modeReg[device] = modeBackup;
    return ok;
}


// ============================================================================
// Continuous-conversion sequencer scan (fast multi-channel readout)
// ============================================================================

uint8_t AD7193Driver::scanContinuous(uint8_t device, uint32_t baseConf,
                                     uint16_t rate, uint8_t nChan,
                                     uint32_t* codes) {
    if (device >= AD7193_NUM_DEVICES || nChan == 0 || nChan > 8) return 0;

    // Enable the nChan lowest channels (AIN1..AINn) for the sequencer.
    uint32_t chanMask = 0;
    for (uint8_t c = 0; c < nChan; c++) chanMask |= AD7193_CONF_CHAN(c);
    confReg[device] = (baseConf & ~AD7193_CONF_CHAN_MASK) | chanMask;
    writeRegister(device, AD7193_REG_CONF, confReg[device]);

    // Continuous conversion + DAT_STA (status appended to each DATA read so the
    // sample is self-identifying), internal clock, requested filter rate.
    modeReg[device] = AD7193_MODE_CONT | AD7193_MODE_DAT_STA |
                      AD7193_MODE_CLKSRC_INT | AD7193_MODE_RATE(rate);
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);

    uint8_t gotMask = 0;
    uint8_t reads = 0;
    const uint8_t maxReads = nChan * 3;     // margin for a missed/duplicate tag
    // Per-sample timeout sized to the conversion (~1.15 ms × FS) with margin, so
    // a flaky/dead board that hangs RDY costs ~this per scan, not a fixed 1 s.
    const uint32_t toMs = 50UL + 2UL * rate;

    while (gotMask != ((1u << nChan) - 1) && reads < maxReads) {
        reads++;
        if (!waitForReady(device, toMs)) break;

        // Read DATA (3 bytes) + the appended STATUS byte (DAT_STA on).
        _selectDevice(device);
        _spi->beginTransaction(SPI_SETTINGS);
        _spi->transfer(_commByte(AD7193_REG_DATA, true));
        uint32_t d = (uint32_t)_spi->transfer(0x00) << 16;
        d |= (uint32_t)_spi->transfer(0x00) << 8;
        d |= (uint32_t)_spi->transfer(0x00);
        uint8_t st = _spi->transfer(0x00);
        _spi->endTransaction();
        _deselectAll();

        uint8_t ch = st & AD7193_STAT_CH_MASK;
        if (ch < nChan && !(gotMask & (1u << ch))) {
            codes[ch] = d;
            gotMask |= (1u << ch);
        }
    }

    // Stop converting and clear DAT_STA so later 3-byte DATA reads stay aligned.
    modeReg[device] = AD7193_MODE_IDLE | AD7193_MODE_CLKSRC_INT |
                      AD7193_MODE_RATE(rate);
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);

    return gotMask;
}


// ============================================================================
// Read Data Register
// ============================================================================

uint32_t AD7193Driver::readData(uint8_t device) {
    return readRegister(device, AD7193_REG_DATA);
}


// ============================================================================
// Continuous Conversion
// ============================================================================

void AD7193Driver::startContinuousConversion(uint8_t device, uint32_t channelMask,
                                              uint8_t gain, bool unipolar,
                                              uint16_t rate) {
    if (device >= AD7193_NUM_DEVICES) return;

    // Configure channels, gain, buffer
    confReg[device] &= ~(AD7193_CONF_CHAN_MASK | AD7193_CONF_GAIN_MASK);
    confReg[device] |= (channelMask & AD7193_CONF_CHAN_MASK);
    confReg[device] |= AD7193_CONF_BUF;
    confReg[device] |= AD7193_CONF_PSEUDO;
    confReg[device] |= (gain & AD7193_CONF_GAIN_MASK);
    if (unipolar) {
        confReg[device] |= AD7193_CONF_UNIPOLAR;
    } else {
        confReg[device] &= ~AD7193_CONF_UNIPOLAR;
    }
    writeRegister(device, AD7193_REG_CONF, confReg[device]);

    // Set continuous mode with status appended
    modeReg[device] = (modeReg[device] & ~(AD7193_MODE_SEL_MASK | 0x3FF));
    modeReg[device] |= AD7193_MODE_CONT | AD7193_MODE_DAT_STA | AD7193_MODE_RATE(rate);
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);
}


uint8_t AD7193Driver::readAllDevices(uint32_t data[AD7193_NUM_DEVICES],
                                      uint8_t status[AD7193_NUM_DEVICES]) {
    uint8_t count = 0;

    for (uint8_t i = 0; i < _numDevices; i++) {
        data[i] = 0;
        status[i] = 0xFF;

        // Check status register
        uint8_t st = (uint8_t)readRegister(i, AD7193_REG_STATUS);
        if (!(st & AD7193_STAT_RDY)) {
            // Data ready — read 4 bytes (data + status when DAT_STA enabled)
            _selectDevice(i);

            _spi->beginTransaction(SPI_SETTINGS);
            _spi->transfer(_commByte(AD7193_REG_DATA, true));

            uint32_t raw = 0;
            raw |= (uint32_t)_spi->transfer(0x00) << 24;
            raw |= (uint32_t)_spi->transfer(0x00) << 16;
            raw |= (uint32_t)_spi->transfer(0x00) << 8;
            raw |= (uint32_t)_spi->transfer(0x00);

            _spi->endTransaction();
            _deselectAll();

            data[i] = (raw >> 8) & 0xFFFFFF;
            status[i] = raw & 0xFF;
            count++;
        }
    }

    return count;
}


// ============================================================================
// Calibration — matches working driver
// ============================================================================

void AD7193Driver::calibrateInternalZero(uint8_t device) {
    if (device >= AD7193_NUM_DEVICES) return;

    modeReg[device] = (modeReg[device] & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_CAL_INT_Z;
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);

    delay(10);  // Let ADC assert RDY=1 before polling

    if (!waitForReady(device, 10000)) {
        Serial.println("# [AD7193] Zero-scale calibration timeout!");
        return;
    }
    Serial.print("# Zero-cal done. Offset=0x");
    Serial.println(readRegister(device, AD7193_REG_OFFSET), HEX);
}


void AD7193Driver::calibrateInternalFull(uint8_t device) {
    if (device >= AD7193_NUM_DEVICES) return;

    modeReg[device] = (modeReg[device] & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_CAL_INT_F;
    writeRegister(device, AD7193_REG_MODE, modeReg[device]);

    delay(10);  // Let ADC assert RDY=1 before polling

    if (!waitForReady(device, 10000)) {
        Serial.println("# [AD7193] Full-scale calibration timeout!");
        return;
    }
    Serial.print("# Full-cal done. FS=0x");
    Serial.println(readRegister(device, AD7193_REG_FULLSCALE), HEX);
}


// ============================================================================
// Diagnostic: Scan all 8 decoder (74HC138) positions
// ============================================================================

void AD7193Driver::diagnosticScan() {
    Serial.println("\n# [DIAG] Scanning all 8 decoder positions...");
    Serial.println("#   Idx  CS    ID      Result");
    Serial.println("#   ---  ----  ------  ------");

    for (uint8_t d = 0; d < AD7193_NUM_DEVICES; d++) {
        _select->selectDevice(d);
        delayMicroseconds(5);

        _spi->beginTransaction(SPI_SETTINGS);
        _spi->transfer(_commByte(AD7193_REG_ID, true));
        uint8_t id = _spi->transfer(0x00);
        _spi->endTransaction();

        _select->deselect();

        Serial.print("#   ");
        Serial.print(d);
        Serial.print("    CS");
        Serial.print(d + 1);
        Serial.print("   0x");
        Serial.print(id, HEX);

        bool valid = ((id & AD7193_ID_MASK) == AD7193_ID_VALUE);
        Serial.println(valid ? "    << AD7193 FOUND" : "");
    }
    Serial.println();
}


// ============================================================================
// Voltage — matches working driver formula
// ============================================================================

float AD7193Driver::codeToVoltage(uint32_t raw, float vref, uint8_t gain, bool unipolar) {
    if (gain == 0) gain = 1;

    if (unipolar) {
        return ((float)raw / 16777216.0f) * (vref / (float)gain);
    } else {
        return (((float)raw / 8388608.0f) - 1.0f) * (vref / (float)gain);
    }
}
