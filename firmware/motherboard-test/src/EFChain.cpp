/*
 * EFChain.cpp — XTR200 error-flag readback via the daisy-chained SN74LV165s
 */
#include "EFChain.h"

// A '165 presents stage H at QH after a load, then shifts G, F, E, D, C, B, A
// toward the output. The daughterboard wires the EF pins to those inputs in a
// scrambled order (verified against the netlist):
//
//     A(11)=U1  B(12)=U3  C(13)=U5  D(14)=U0
//     E(3) =U4  F(4) =U7  G(5) =U2  H(6) =U6
//
// so the n-th bit clocked out of a given board belongs to physical channel:
static const uint8_t EF_SHIFT_TO_CH[8] = { 6, 2, 7, 4, 0, 5, 3, 1 };

// The chain runs board0(J1) -> board1(J2) -> ... -> board7(J8) -> Pico, so the
// board nearest the Pico clocks out first.
static const uint8_t EF_SHIFT_TO_BOARD[8] = { 7, 6, 5, 4, 3, 2, 1, 0 };

// Half-period / setup padding. The '165 is good well past 1 MHz, but this bus
// crosses eight edge connectors, so keep the edges unhurried — a full 64-bit
// read still costs well under a millisecond.
#define EF_T_US 1

EFChain::EFChain(uint8_t plPin, uint8_t cpPin, uint8_t q7Pin)
    : _pl(plPin), _cp(cpPin), _q7(q7Pin) {}

void EFChain::begin() {
    pinMode(_pl, OUTPUT);
    pinMode(_cp, OUTPUT);
    pinMode(_q7, INPUT);
    digitalWrite(_pl, HIGH);    // HIGH = shift mode (not loading)
    digitalWrite(_cp, LOW);     // idle low; the '165 shifts on the RISING edge
}

void EFChain::read(uint8_t* out, uint8_t numBoards) {
    for (uint8_t b = 0; b < numBoards; b++) out[b] = 0;

    // SH/LD low loads all 64 EF pins asynchronously — one simultaneous snapshot
    // across every board, which is why a transient fault cannot be missed on
    // some boards and caught on others.
    digitalWrite(_pl, LOW);
    delayMicroseconds(EF_T_US);
    digitalWrite(_pl, HIGH);
    delayMicroseconds(EF_T_US);

    // After the load QH already presents the first bit, so read *then* clock.
    for (uint8_t i = 0; i < 64; i++) {
        uint8_t slot = i / 8;                    // which '165 in the chain
        uint8_t b    = EF_SHIFT_TO_BOARD[slot];

        // EF is active LOW: a low bit is an asserted fault.
        if (b < numBoards && digitalRead(_q7) == LOW)
            out[b] |= (uint8_t)(1u << EF_SHIFT_TO_CH[i % 8]);

        digitalWrite(_cp, HIGH);                 // rising edge shifts
        delayMicroseconds(EF_T_US);
        digitalWrite(_cp, LOW);
        delayMicroseconds(EF_T_US);
    }
}
