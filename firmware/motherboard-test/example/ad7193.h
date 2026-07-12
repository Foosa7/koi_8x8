#ifndef AD7193_H
#define AD7193_H

#include "hardware/spi.h"
#include <stdint.h>
#include <stdbool.h>

// ============================================================================
// AD7193 Register Addresses (3-bit, used in Communications Register)
// ============================================================================
#define AD7193_REG_COMM       0x00  // Communications Register (WO, 8-bit)
#define AD7193_REG_STATUS     0x00  // Status Register (RO, 8-bit) - same addr as COMM
#define AD7193_REG_MODE       0x01  // Mode Register (RW, 24-bit)
#define AD7193_REG_CONF       0x02  // Configuration Register (RW, 24-bit)
#define AD7193_REG_DATA       0x03  // Data Register (RO, 24-bit or 32-bit with status)
#define AD7193_REG_ID         0x04  // ID Register (RO, 8-bit)
#define AD7193_REG_GPOCON     0x05  // GPOCON Register (RW, 8-bit)
#define AD7193_REG_OFFSET     0x06  // Offset Register (RW, 24-bit)
#define AD7193_REG_FULLSCALE  0x07  // Full-Scale Register (RW, 24-bit)

// ============================================================================
// Communications Register Bits (8-bit, Write Only)
// ============================================================================
#define AD7193_COMM_WEN       (1 << 7)  // Write Enable (must be 0 to start write)
#define AD7193_COMM_WRITE     (0 << 6)  // Write operation
#define AD7193_COMM_READ      (1 << 6)  // Read operation
#define AD7193_COMM_ADDR(x)   (((x) & 0x07) << 3)  // Register address bits
#define AD7193_COMM_CREAD     (1 << 2)  // Continuous read of data register

// ============================================================================
// Status Register Bits (8-bit, Read Only)
// ============================================================================
#define AD7193_STAT_RDY       (1 << 7)  // Ready bit (0 = conversion complete)
#define AD7193_STAT_ERR       (1 << 6)  // Error bit
#define AD7193_STAT_NOREF     (1 << 5)  // No external reference
#define AD7193_STAT_PARITY    (1 << 4)  // Parity check
#define AD7193_STAT_CH_MASK   0x0F      // Channel bits [3:0]

// ============================================================================
// Mode Register Bits (24-bit, Read/Write)
// ============================================================================
// Mode Selection [23:21]
#define AD7193_MODE_CONT       (0UL << 21)  // Continuous Conversion Mode (default)
#define AD7193_MODE_SINGLE     (1UL << 21)  // Single Conversion Mode
#define AD7193_MODE_IDLE       (2UL << 21)  // Idle Mode
#define AD7193_MODE_PWRDN      (3UL << 21)  // Power-Down Mode
#define AD7193_MODE_CAL_INT_Z  (4UL << 21)  // Internal Zero-Scale Calibration
#define AD7193_MODE_CAL_INT_F  (5UL << 21)  // Internal Full-Scale Calibration
#define AD7193_MODE_CAL_SYS_Z  (6UL << 21)  // System Zero-Scale Calibration
#define AD7193_MODE_CAL_SYS_F  (7UL << 21)  // System Full-Scale Calibration
#define AD7193_MODE_SEL_MASK   (7UL << 21)

// Status on Data Read [20]
#define AD7193_MODE_DAT_STA    (1UL << 20)  // Append Status Register to Data

// Clock Source [19:18]
#define AD7193_MODE_CLKSRC_INT       (0UL << 18)  // Internal 4.92 MHz (not available at MCLK2)
#define AD7193_MODE_CLKSRC_INT_OUT   (1UL << 18)  // Internal 4.92 MHz (available at MCLK2)
#define AD7193_MODE_CLKSRC_EXT       (2UL << 18)  // External clock on MCLK2
#define AD7193_MODE_CLKSRC_EXT_DIV2  (3UL << 18)  // External clock on MCLK2 / 2

// Averaging [17:16] (Sinc3 filter only, when CHOP=0)
#define AD7193_MODE_AVG_NONE   (0UL << 16)
#define AD7193_MODE_AVG_2      (1UL << 16)
#define AD7193_MODE_AVG_8      (2UL << 16)
#define AD7193_MODE_AVG_16     (3UL << 16)

// SINC3 [15]
#define AD7193_MODE_SINC3      (1UL << 15)  // SINC3 filter (0 = SINC4)

// ACX [14] — AC excitation enable (for bridge sensors)
#define AD7193_MODE_ACX        (1UL << 14)

// ENPAR [13]
#define AD7193_MODE_ENPAR      (1UL << 13)  // Parity enable

// CLK_DIV [12]
#define AD7193_MODE_CLK_DIV    (1UL << 12)  // Clock divide by 2

// SINGLE [11]
#define AD7193_MODE_SCYCLE     (1UL << 11)  // Single cycle conversion

// REJ60 [10]
#define AD7193_MODE_REJ60      (1UL << 10)  // 50/60 Hz notch (1 = enable)

// Filter Output Data Rate Select [9:0]
#define AD7193_MODE_RATE(x)    ((x) & 0x3FF)

// ============================================================================
// Configuration Register Bits (24-bit, Read/Write)
// ============================================================================
// CHOP [23]
#define AD7193_CONF_CHOP       (1UL << 23)  // CHOP enable

// Channel Select [17:8] — 10 channel bits
// Pseudo-differential channels (each AIN vs AINCOM):
#define AD7193_CONF_CHAN(x)    (1UL << (8 + (x)))  // Enable channel x (0-7)
#define AD7193_CONF_CHAN_MASK  (0x3FFUL << 8)

// Individual channel defines:
#define AD7193_CH_AIN1     (1UL << 8)   // AIN1(+) / AIN2(-)  or AIN1 - AINCOM
#define AD7193_CH_AIN2     (1UL << 9)   // AIN3(+) / AIN4(-)  or AIN2 - AINCOM
#define AD7193_CH_AIN3     (1UL << 10)  // AIN5(+) / AIN6(-)  or AIN3 - AINCOM
#define AD7193_CH_AIN4     (1UL << 11)  // AIN7(+) / AIN8(-)  or AIN4 - AINCOM
#define AD7193_CH_AIN5     (1UL << 12)  // AIN5 - AINCOM (pseudo-diff)
#define AD7193_CH_AIN6     (1UL << 13)  // AIN6 - AINCOM (pseudo-diff)
#define AD7193_CH_AIN7     (1UL << 14)  // AIN7 - AINCOM (pseudo-diff)
#define AD7193_CH_AIN8     (1UL << 15)  // AIN8 - AINCOM (pseudo-diff)
#define AD7193_CH_TEMP     (1UL << 16)  // Temperature sensor
#define AD7193_CH_SHORT    (1UL << 17)  // Short (AIN2-AIN2 for noise test)

// BUF [4]
#define AD7193_CONF_BUF        (1UL << 4)   // Enable internal buffer

// Unipolar/Bipolar [3]
#define AD7193_CONF_UNIPOLAR   (1UL << 3)   // Unipolar mode (0 = bipolar)

// Pseudo-differential / Fully-differential [18]
#define AD7193_CONF_PSEUDO     (1UL << 18)  // Pseudo-differential mode

// REF Select [20]
#define AD7193_CONF_REFSEL     (1UL << 20)  // Reference select (0=REFIN1, 1=REFIN2)

// Burn [7]
#define AD7193_CONF_BURN       (1UL << 7)   // Burnout current enable

// REFDET [6]
#define AD7193_CONF_REFDET     (1UL << 6)   // Reference detect enable

// Gain Select [2:0]
#define AD7193_CONF_GAIN_1     0x00
#define AD7193_CONF_GAIN_8     0x03
#define AD7193_CONF_GAIN_16    0x04
#define AD7193_CONF_GAIN_32    0x05
#define AD7193_CONF_GAIN_64    0x06
#define AD7193_CONF_GAIN_128   0x07
#define AD7193_CONF_GAIN_MASK  0x07

// ============================================================================
// GPOCON Register Bits (8-bit, Read/Write)
// ============================================================================
#define AD7193_GPOCON_BPDSW    (1 << 6)  // Bridge power-down switch
#define AD7193_GPOCON_GP32EN   (1 << 5)  // Enable P3/P2 as GPO
#define AD7193_GPOCON_GP10EN   (1 << 4)  // Enable P1/P0 as GPO
#define AD7193_GPOCON_P3DAT    (1 << 3)  // P3 data
#define AD7193_GPOCON_P2DAT    (1 << 2)  // P2 data
#define AD7193_GPOCON_P1DAT    (1 << 1)  // P1 data
#define AD7193_GPOCON_P0DAT    (1 << 0)  // P0 data

// ============================================================================
// ID Register
// ============================================================================
#define AD7193_ID_MASK         0x0F      // Lower nibble = device family
#define AD7193_ID_VALUE        0x02      // Expected: 0xX2

// ============================================================================
// Register sizes (in bytes)
// ============================================================================
static const uint8_t ad7193_reg_size[] = {
    1,  // Communications / Status (0x00)
    3,  // Mode Register (0x01)
    3,  // Configuration Register (0x02)
    3,  // Data Register (0x03) — can be 4 with DAT_STA
    1,  // ID Register (0x04)
    1,  // GPOCON Register (0x05)
    3,  // Offset Register (0x06)
    3,  // Full-Scale Register (0x07)
};

// ============================================================================
// AD7193 Driver Configuration Structure
// ============================================================================
typedef struct {
    spi_inst_t *spi;       // SPI instance (spi0 or spi1)
    uint pin_cs;           // Chip Select pin
    uint pin_sck;          // Clock pin
    uint pin_mosi;         // MOSI (DIN) pin
    uint pin_miso;         // MISO (DOUT/RDY) pin
    uint32_t spi_freq;     // SPI clock frequency in Hz
} ad7193_config_t;

// ============================================================================
// AD7193 Device Handle
// ============================================================================
typedef struct {
    ad7193_config_t config;
    uint32_t mode_reg;     // Cached mode register value
    uint32_t conf_reg;     // Cached configuration register value
    uint8_t  gpocon_reg;   // Cached GPOCON register value
    bool     data_sta;     // Whether DAT_STA is enabled (status appended to data)
} ad7193_dev_t;

// ============================================================================
// API Functions
// ============================================================================

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize the AD7193 device.
 *
 * Configures SPI pins and performs a software reset, then verifies
 * the device ID register.
 *
 * @param dev     Pointer to device handle.
 * @param config  Configuration parameters.
 * @return 0 on success, -1 on failure (e.g. wrong ID).
 */
int ad7193_init(ad7193_dev_t *dev, const ad7193_config_t *config);

/**
 * @brief Perform a software reset of the AD7193.
 *
 * Sends 40 consecutive 1s (5 bytes of 0xFF) to reset the serial interface.
 */
void ad7193_reset(ad7193_dev_t *dev);

/**
 * @brief Read a register from the AD7193.
 *
 * @param dev       Pointer to device handle.
 * @param reg_addr  Register address (0-7).
 * @return Register value (8, 24, or 32 bits depending on register).
 */
uint32_t ad7193_read_reg(ad7193_dev_t *dev, uint8_t reg_addr);

/**
 * @brief Write a register on the AD7193.
 *
 * @param dev       Pointer to device handle.
 * @param reg_addr  Register address (0-7).
 * @param value     Value to write.
 */
void ad7193_write_reg(ad7193_dev_t *dev, uint8_t reg_addr, uint32_t value);

/**
 * @brief Read the device ID register.
 *
 * @param dev  Pointer to device handle.
 * @return ID register value. Lower nibble should be 0x02 for AD7193.
 */
uint8_t ad7193_get_id(ad7193_dev_t *dev);

/**
 * @brief Set the operating mode.
 *
 * @param dev   Pointer to device handle.
 * @param mode  Mode bits (e.g. AD7193_MODE_CONT, AD7193_MODE_SINGLE).
 */
void ad7193_set_mode(ad7193_dev_t *dev, uint32_t mode);

/**
 * @brief Configure channel selection.
 *
 * @param dev          Pointer to device handle.
 * @param channel_mask Bitmask of channels to enable (AD7193_CH_AIN1..8, AD7193_CH_TEMP, etc.).
 */
void ad7193_set_channel(ad7193_dev_t *dev, uint32_t channel_mask);

/**
 * @brief Configure the programmable gain.
 *
 * @param dev   Pointer to device handle.
 * @param gain  Gain setting (AD7193_CONF_GAIN_1..128).
 */
void ad7193_set_gain(ad7193_dev_t *dev, uint8_t gain);

/**
 * @brief Enable or disable pseudo-differential mode.
 *
 * @param dev     Pointer to device handle.
 * @param enable  true = pseudo-differential, false = fully-differential.
 */
void ad7193_set_pseudo_diff(ad7193_dev_t *dev, bool enable);

/**
 * @brief Enable or disable unipolar mode.
 *
 * @param dev     Pointer to device handle.
 * @param enable  true = unipolar, false = bipolar.
 */
void ad7193_set_unipolar(ad7193_dev_t *dev, bool enable);

/**
 * @brief Enable or disable the internal buffer.
 *
 * @param dev     Pointer to device handle.
 * @param enable  true = buffer enabled.
 */
void ad7193_set_buffer(ad7193_dev_t *dev, bool enable);

/**
 * @brief Set the output data rate via the filter register bits.
 *
 * @param dev   Pointer to device handle.
 * @param rate  Filter rate value (10-bit, 0-1023). Actual ODR depends on clock and filter.
 */
void ad7193_set_rate(ad7193_dev_t *dev, uint16_t rate);

/**
 * @brief Enable appending status register to data reads.
 *
 * @param dev     Pointer to device handle.
 * @param enable  true = enable status append.
 */
void ad7193_enable_data_status(ad7193_dev_t *dev, bool enable);

/**
 * @brief Wait for a conversion to complete (poll DOUT/RDY pin going low).
 *
 * @param dev         Pointer to device handle.
 * @param timeout_ms  Maximum time to wait in ms.
 * @return true if data ready, false if timeout.
 */
bool ad7193_wait_rdy(ad7193_dev_t *dev, uint32_t timeout_ms);

/**
 * @brief Read a single conversion result.
 *
 * Waits for RDY, then reads the data register.
 *
 * @param dev  Pointer to device handle.
 * @return 24-bit conversion result.
 */
uint32_t ad7193_read_data(ad7193_dev_t *dev);

/**
 * @brief Perform a single conversion on a specific channel.
 *
 * Configures single conversion mode, selects the channel, waits for
 * conversion complete, and returns the result.
 *
 * @param dev          Pointer to device handle.
 * @param channel_mask Channel bitmask (single channel).
 * @return 24-bit conversion result.
 */
uint32_t ad7193_single_conversion(ad7193_dev_t *dev, uint32_t channel_mask);

/**
 * @brief Read the on-chip temperature sensor.
 *
 * @param dev  Pointer to device handle.
 * @return Temperature in degrees Celsius (approx).
 */
float ad7193_read_temperature(ad7193_dev_t *dev);

/**
 * @brief Convert a raw 24-bit code to voltage.
 *
 * @param raw_code  Raw 24-bit ADC reading.
 * @param vref      Reference voltage (e.g. 2.5V).
 * @param gain      PGA gain (1, 8, 16, 32, 64, 128).
 * @param unipolar  true if unipolar mode.
 * @return Voltage in volts.
 */
float ad7193_code_to_voltage(uint32_t raw_code, float vref, uint8_t gain, bool unipolar);

/**
 * @brief Perform internal zero-scale calibration.
 */
void ad7193_calibrate_internal_zero(ad7193_dev_t *dev);

/**
 * @brief Perform internal full-scale calibration.
 */
void ad7193_calibrate_internal_full(ad7193_dev_t *dev);

/**
 * @brief Print all register values for debugging.
 */
void ad7193_dump_registers(ad7193_dev_t *dev);

#ifdef __cplusplus
}
#endif

#endif // AD7193_H
