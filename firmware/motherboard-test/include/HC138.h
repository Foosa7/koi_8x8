/*
 * HC138.h — 74HC138 3-to-8 decoder chip-select helper
 *
 * The board uses two 74HC138 decoders that SHARE the A0/A1/A2 address lines
 * (GP20/GP21/GP22); each has its own active-low enable wired to ~E0 (G2A):
 * ADC_EN=GP5, DAC_EN=GP6. Outputs are wired inverted for both banks:
 *   Y0 -> CS8, Y1 -> CS7, ... Y7 -> CS1
 * so 0-based device index d (chip CS(d+1)) maps to decoder output Y(7-d).
 *
 * Assert only ONE decoder enable at a time. Set the address before asserting
 * the enable so no wrong CS glitches low.
 */
#ifndef HC138_H
#define HC138_H

#include <Arduino.h>

class HC138 {
public:
    /**
     * @param a0Pin,a1Pin,a2Pin  shared address GPIOs (A0=LSB)
     * @param enPin              active-low enable for this decoder (~E0/G2A)
     */
    HC138(uint8_t a0Pin, uint8_t a1Pin, uint8_t a2Pin, uint8_t enPin);

    void begin();

    /** Select 0-based device index d; drives CS(d+1) LOW until deselect(). */
    void selectDevice(uint8_t d);

    /** Deassert the enable — all of this decoder's CS lines go HIGH. */
    void deselect();

    /** DEBUG: flip enable polarity (true = drive LOW to enable). */
    void setEnableActiveLow(bool v);

private:
    uint8_t _a0, _a1, _a2, _en;
    bool    _enActiveLow = true;
    void _setAddress(uint8_t y);
};

#endif // HC138_H
