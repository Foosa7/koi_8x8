/*
 * XTR595.cpp — SN74LV595 driving XTR_OD_1..8
 */
#include "XTR595.h"

// 595 captures SER on the SRCLK rising edge. Use MODE3 (not MODE0) to MATCH the
// AD7193 on the shared SPI0 bus and the original working SN74LV595 CS code: the
// 595 has no CS and shifts on every SRCLK edge, so a mismatched idle level
// (MODE0 idles SCK low vs the ADC's MODE3 idle high) injects a spurious shift
// edge on every ADC<->595 transition and corrupts the register.
const SPISettings XTR595::SPI_SETTINGS(1000000, MSBFIRST, SPI_MODE3);

XTR595::XTR595(SPIClass* spi, uint8_t rclkPin)
    : _spi(spi), _rclk(rclkPin), _state(0x00) {}

void XTR595::begin() {
    pinMode(_rclk, OUTPUT);
    digitalWrite(_rclk, LOW);
    _state = 0xFF;
    _shift();   // OD pins HIGH = all XTR200 outputs DISABLED (safe power-on)
}

void XTR595::setOutputs(uint8_t mask) {
    _state = mask;
    _shift();
}

void XTR595::setChannel(uint8_t ch, bool on) {
    if (ch > 7) return;
    if (on) _state |=  (uint8_t)(1 << ch);
    else    _state &= (uint8_t)~(1 << ch);
    _shift();
}

void XTR595::_shift() {
    // MSB-first byte: bit7 lands on QH (XTR_OD_8), bit0 on QA (XTR_OD_1).
    _spi->beginTransaction(SPI_SETTINGS);
    _spi->transfer(_state);
    _spi->endTransaction();

    // Latch shift register -> output register on RCLK rising edge.
    digitalWrite(_rclk, HIGH);
    delayMicroseconds(1);
    digitalWrite(_rclk, LOW);
}
