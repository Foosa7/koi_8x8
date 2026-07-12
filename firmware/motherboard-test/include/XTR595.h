/*
 * XTR595.h — SN74LV595 driving XTR_OD_1..8 (XTR200 front-end output-drive)
 *
 * QA=XTR_OD_1 ... QH=XTR_OD_8. NOTE the XTR200 OD pin is active-LOW-enable:
 * OD HIGH = output DISABLED (power-on default, internal pullup), OD LOW =
 * output ENABLED. So bit=1 disables a board's front end, bit=0 enables it.
 * The 595 shares the ADC
 * SPI bus (SPI0: SER=GP3, SRCLK=GP2); only RCLK (latch) is a dedicated GPIO.
 *
 * Note: because SER/SRCLK are shared with the AD7193s, ordinary ADC traffic
 * also clocks bytes through the 595's shift register — that is harmless, since
 * the outputs (QA..QH) only update on the RCLK pulse in _shift(). Re-shift via
 * setOutputs()/setChannel() whenever you need to (re)assert a known state.
 */
#ifndef XTR595_H
#define XTR595_H

#include <Arduino.h>
#include <SPI.h>

class XTR595 {
public:
    /**
     * @param spi      SPI bus the 595 shares with the ADCs (SPI0)
     * @param rclkPin  GPIO for the 595 RCLK/latch
     */
    XTR595(SPIClass* spi, uint8_t rclkPin);

    void begin();

    /** mask bit0 = XTR_OD_1 (QA) ... bit7 = XTR_OD_8 (QH).
     *  bit=1 -> OD HIGH = output disabled; bit=0 -> OD LOW = output enabled. */
    void setOutputs(uint8_t mask);

    /** Set a single channel (0..7 => XTR_OD_1..8) on/off. */
    void setChannel(uint8_t ch, bool on);

    uint8_t state() const { return _state; }

private:
    SPIClass* _spi;
    uint8_t   _rclk;
    uint8_t   _state;
    void _shift();
    static const SPISettings SPI_SETTINGS;
};

#endif // XTR595_H
