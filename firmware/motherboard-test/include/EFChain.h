/*
 * EFChain.h — XTR200 error-flag readback via the daisy-chained SN74LV165s
 *
 * Each daughterboard carries a SN74LV165 (U8) whose eight parallel inputs are
 * the eight XTR200 EF pins. The motherboard chains all eight of them into one
 * 64-bit shift register with a shared load and clock:
 *
 *     J1.Q7 -> J2.DS -> J3.DS -> ... -> J8.DS,   J8.Q7 -> Pico (Q7_8)
 *
 * so the board nearest the Pico (J8 = board 7) clocks its bits out first.
 *
 * This is a private 3-wire bus — CP/PL/Q7_8 are their own Pico GPIOs, NOT the
 * shared SPI0 pins — so reading it cannot disturb the AD7193s or the SN74LV595.
 * It is bit-banged for that reason; there is no SPI peripheral on these pins.
 *
 * EF is ACTIVE LOW and open-drain with only a 4 uA internal pull-up (there are
 * no external pull-ups on the daughterboard), so the line is high-impedance
 * when idle. read() returns ACTIVE-HIGH fault bits: bit ch set = channel ch is
 * asserting EF.
 *
 * EF is a shared flag — per XTR200 datasheet Table 6-1 it means ANY of:
 * output open, output saturation, SET-pin short, supply undervoltage (<8 V),
 * or die temperature >150 C. Open-circuit detection additionally requires
 * VIN > 350 mV (with RSET = 4.7 k that is IOUT > ~0.75 mA) and VSP > 10 V.
 * Below ~0.75 mA an open load will NOT raise EF.
 *
 * CHAIN GAP CAVEAT: an unpopulated slot removes that board's '165 and breaks
 * the chain. Boards downstream of the gap (higher index, nearer the Pico) still
 * read correctly; everything upstream of it shifts in garbage. The caller must
 * mask accordingly — see efValidMask() in main.cpp.
 */
#ifndef EFCHAIN_H
#define EFCHAIN_H

#include <Arduino.h>

class EFChain {
public:
    /**
     * @param plPin  SH/LD (PL, active-low parallel load), shared by all boards
     * @param cpPin  CLK (CP), shared by all boards
     * @param q7Pin  chain serial output (Q7_8), from the board nearest the Pico
     */
    EFChain(uint8_t plPin, uint8_t cpPin, uint8_t q7Pin);

    void begin();

    /**
     * Latch and shift the whole 64-bit chain.
     *
     * @param out        array of at least `numBoards` bytes. out[b] bit ch = 1
     *                   means board b, physical channel ch is asserting EF.
     * @param numBoards  how many board slots to decode (normally NUM_BOARDS).
     *
     * All 64 bits are always clocked so the chain ends in a known state, even
     * when the caller cares about fewer boards.
     */
    void read(uint8_t* out, uint8_t numBoards);

private:
    uint8_t _pl, _cp, _q7;
};

#endif // EFCHAIN_H
