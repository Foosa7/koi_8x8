/*
 * HC138.cpp — 74HC138 3-to-8 decoder chip-select helper
 */
#include "HC138.h"

HC138::HC138(uint8_t a0Pin, uint8_t a1Pin, uint8_t a2Pin, uint8_t enPin)
    : _a0(a0Pin), _a1(a1Pin), _a2(a2Pin), _en(enPin) {}

void HC138::begin() {
    pinMode(_a0, OUTPUT);
    pinMode(_a1, OUTPUT);
    pinMode(_a2, OUTPUT);
    pinMode(_en, OUTPUT);
    digitalWrite(_en, _enActiveLow ? HIGH : LOW);   // idle => decoder disabled
    _setAddress(0);
}

void HC138::setEnableActiveLow(bool v) {
    _enActiveLow = v;
    digitalWrite(_en, _enActiveLow ? HIGH : LOW);   // idle = disabled
}

void HC138::_setAddress(uint8_t y) {
    digitalWrite(_a0, (y >> 0) & 0x01);
    digitalWrite(_a1, (y >> 1) & 0x01);
    digitalWrite(_a2, (y >> 2) & 0x01);
}

void HC138::selectDevice(uint8_t d) {
    // Inverted wiring: device d (CS d+1) is on decoder output Y(7-d).
    _setAddress(7 - (d & 0x07));
    digitalWrite(_en, _enActiveLow ? LOW : HIGH);   // assert this decoder
}

void HC138::deselect() {
    digitalWrite(_en, _enActiveLow ? HIGH : LOW);
}
