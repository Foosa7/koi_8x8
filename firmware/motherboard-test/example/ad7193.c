#include "ad7193.h"
#include "pico/stdlib.h"
#include <stdio.h>
#include <string.h>

// ============================================================================
// Internal helpers
// ============================================================================

static void cs_select(ad7193_dev_t *dev) {
    asm volatile("nop \n nop \n nop");
    gpio_put(dev->config.pin_cs, 0);  // Active low
    asm volatile("nop \n nop \n nop");
}

static void cs_deselect(ad7193_dev_t *dev) {
    asm volatile("nop \n nop \n nop");
    gpio_put(dev->config.pin_cs, 1);
    asm volatile("nop \n nop \n nop");
}

// ============================================================================
// Reset
// ============================================================================

void ad7193_reset(ad7193_dev_t *dev) {
    // Send at least 40 serial clock cycles with DIN high to reset the interface.
    // 5 bytes of 0xFF = 40 bits of 1s.
    uint8_t reset_buf[5] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

    cs_select(dev);
    spi_write_blocking(dev->config.spi, reset_buf, 5);
    cs_deselect(dev);

    sleep_ms(5);  // Allow time for reset to complete
}

// ============================================================================
// Register Read / Write
// ============================================================================

uint32_t ad7193_read_reg(ad7193_dev_t *dev, uint8_t reg_addr) {
    uint8_t size = ad7193_reg_size[reg_addr & 0x07];

    // Build communications register byte: WEN=0, R/W=1 (read), ADDR, CREAD=0
    uint8_t comm_byte = AD7193_COMM_READ | AD7193_COMM_ADDR(reg_addr);

    uint8_t rx_buf[4] = {0};

    cs_select(dev);
    spi_write_blocking(dev->config.spi, &comm_byte, 1);
    spi_read_blocking(dev->config.spi, 0x00, rx_buf, size);
    cs_deselect(dev);

    // Assemble the result (MSB first)
    uint32_t result = 0;
    for (uint8_t i = 0; i < size; i++) {
        result = (result << 8) | rx_buf[i];
    }

    return result;
}

void ad7193_write_reg(ad7193_dev_t *dev, uint8_t reg_addr, uint32_t value) {
    uint8_t size = ad7193_reg_size[reg_addr & 0x07];

    // Build communications register byte: WEN=0, R/W=0 (write), ADDR
    uint8_t comm_byte = AD7193_COMM_WRITE | AD7193_COMM_ADDR(reg_addr);

    uint8_t tx_buf[4];

    // Serialize value MSB first
    for (int i = size - 1; i >= 0; i--) {
        tx_buf[i] = value & 0xFF;
        value >>= 8;
    }

    cs_select(dev);
    spi_write_blocking(dev->config.spi, &comm_byte, 1);
    spi_write_blocking(dev->config.spi, tx_buf, size);
    cs_deselect(dev);
}

// ============================================================================
// Initialization
// ============================================================================

int ad7193_init(ad7193_dev_t *dev, const ad7193_config_t *config) {
    memcpy(&dev->config, config, sizeof(ad7193_config_t));

    // Initialize SPI peripheral
    spi_init(dev->config.spi, dev->config.spi_freq);

    // Configure SPI pins
    gpio_set_function(dev->config.pin_sck, GPIO_FUNC_SPI);
    gpio_set_function(dev->config.pin_mosi, GPIO_FUNC_SPI);
    gpio_set_function(dev->config.pin_miso, GPIO_FUNC_SPI);

    // Configure CS pin as GPIO output, deselected (high)
    gpio_init(dev->config.pin_cs);
    gpio_set_dir(dev->config.pin_cs, GPIO_OUT);
    gpio_put(dev->config.pin_cs, 1);

    // AD7193 uses SPI Mode 3: CPOL=1, CPHA=1
    spi_set_format(dev->config.spi, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);

    // Perform a software reset
    ad7193_reset(dev);

    // Read and verify chip ID
    uint8_t id = ad7193_get_id(dev);
    printf("[AD7193] ID Register: 0x%02X\n", id);

    if ((id & AD7193_ID_MASK) != AD7193_ID_VALUE) {
        printf("[AD7193] ERROR: Unexpected ID! Expected 0xX2, got 0x%02X\n", id);
        return -1;
    }
    printf("[AD7193] Device identified successfully (ID=0x%02X)\n", id);

    // Cache the register defaults after reset
    dev->mode_reg = ad7193_read_reg(dev, AD7193_REG_MODE);
    dev->conf_reg = ad7193_read_reg(dev, AD7193_REG_CONF);
    dev->gpocon_reg = (uint8_t)ad7193_read_reg(dev, AD7193_REG_GPOCON);
    dev->data_sta = false;

    printf("[AD7193] Reset defaults - MODE: 0x%06lX, CONF: 0x%06lX, GPOCON: 0x%02X\n",
           (unsigned long)dev->mode_reg, (unsigned long)dev->conf_reg, dev->gpocon_reg);

    return 0;
}

// ============================================================================
// ID
// ============================================================================

uint8_t ad7193_get_id(ad7193_dev_t *dev) {
    return (uint8_t)ad7193_read_reg(dev, AD7193_REG_ID);
}

// ============================================================================
// Mode Configuration
// ============================================================================

void ad7193_set_mode(ad7193_dev_t *dev, uint32_t mode) {
    dev->mode_reg = (dev->mode_reg & ~AD7193_MODE_SEL_MASK) | (mode & AD7193_MODE_SEL_MASK);
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);
}

void ad7193_set_rate(ad7193_dev_t *dev, uint16_t rate) {
    dev->mode_reg = (dev->mode_reg & ~0x3FF) | AD7193_MODE_RATE(rate);
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);
}

void ad7193_enable_data_status(ad7193_dev_t *dev, bool enable) {
    if (enable) {
        dev->mode_reg |= AD7193_MODE_DAT_STA;
    } else {
        dev->mode_reg &= ~AD7193_MODE_DAT_STA;
    }
    dev->data_sta = enable;
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);
}

// ============================================================================
// Configuration
// ============================================================================

void ad7193_set_channel(ad7193_dev_t *dev, uint32_t channel_mask) {
    dev->conf_reg = (dev->conf_reg & ~AD7193_CONF_CHAN_MASK) |
                    (channel_mask & AD7193_CONF_CHAN_MASK);
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
}

void ad7193_set_gain(ad7193_dev_t *dev, uint8_t gain) {
    dev->conf_reg = (dev->conf_reg & ~AD7193_CONF_GAIN_MASK) |
                    (gain & AD7193_CONF_GAIN_MASK);
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
}

void ad7193_set_pseudo_diff(ad7193_dev_t *dev, bool enable) {
    if (enable) {
        dev->conf_reg |= AD7193_CONF_PSEUDO;
    } else {
        dev->conf_reg &= ~AD7193_CONF_PSEUDO;
    }
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
}

void ad7193_set_unipolar(ad7193_dev_t *dev, bool enable) {
    if (enable) {
        dev->conf_reg |= AD7193_CONF_UNIPOLAR;
    } else {
        dev->conf_reg &= ~AD7193_CONF_UNIPOLAR;
    }
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
}

void ad7193_set_buffer(ad7193_dev_t *dev, bool enable) {
    if (enable) {
        dev->conf_reg |= AD7193_CONF_BUF;
    } else {
        dev->conf_reg &= ~AD7193_CONF_BUF;
    }
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
}

// ============================================================================
// Data Acquisition
// ============================================================================

bool ad7193_wait_rdy(ad7193_dev_t *dev, uint32_t timeout_ms) {
    // Poll the STATUS register's RDY bit (bit 7).
    // RDY=0 means conversion complete, RDY=1 means still converting.
    // NOTE: We can't poll the DOUT/RDY pin directly because it is
    // tri-stated (high-impedance) when CS is HIGH, causing false reads.
    absolute_time_t deadline = make_timeout_time_ms(timeout_ms);

    while (!time_reached(deadline)) {
        uint8_t status = (uint8_t)ad7193_read_reg(dev, AD7193_REG_STATUS);
        if (!(status & AD7193_STAT_RDY)) {
            return true;  // RDY bit = 0 means conversion is complete
        }
        sleep_ms(1);
    }

    printf("[AD7193] WARNING: Timeout waiting for RDY\n");
    return false;
}

uint32_t ad7193_read_data(ad7193_dev_t *dev) {
    uint8_t comm_byte = AD7193_COMM_READ | AD7193_COMM_ADDR(AD7193_REG_DATA);
    uint8_t size = dev->data_sta ? 4 : 3;  // 3 bytes + optional status byte
    uint8_t rx_buf[4] = {0};

    cs_select(dev);
    spi_write_blocking(dev->config.spi, &comm_byte, 1);
    spi_read_blocking(dev->config.spi, 0x00, rx_buf, size);
    cs_deselect(dev);

    // Assemble 24-bit result (ignore status byte if present)
    uint32_t result = ((uint32_t)rx_buf[0] << 16) |
                      ((uint32_t)rx_buf[1] << 8) |
                      (uint32_t)rx_buf[2];

    return result;
}

uint32_t ad7193_single_conversion(ad7193_dev_t *dev, uint32_t channel_mask) {
    // Select the channel
    ad7193_set_channel(dev, channel_mask);

    // Set single conversion mode
    uint32_t mode_backup = dev->mode_reg;
    dev->mode_reg = (dev->mode_reg & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_SINGLE;
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);

    // Wait for conversion to complete
    if (!ad7193_wait_rdy(dev, 5000)) {
        printf("[AD7193] Single conversion timeout on channel mask 0x%06lX\n", (unsigned long)channel_mask);
        dev->mode_reg = mode_backup;
        return 0;
    }

    // Read the data
    uint32_t data = ad7193_read_data(dev);

    // Restore mode register
    dev->mode_reg = mode_backup;

    return data;
}

// ============================================================================
// Temperature Sensor
// ============================================================================

float ad7193_read_temperature(ad7193_dev_t *dev) {
    // Save current config
    uint32_t conf_backup = dev->conf_reg;

    // Select temperature channel, gain=1, no pseudo-diff, bipolar for temp sensor
    dev->conf_reg &= ~(AD7193_CONF_CHAN_MASK | AD7193_CONF_GAIN_MASK | AD7193_CONF_PSEUDO | AD7193_CONF_UNIPOLAR);
    dev->conf_reg |= AD7193_CH_TEMP | AD7193_CONF_GAIN_1;
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);

    // Single conversion
    uint32_t mode_backup = dev->mode_reg;
    dev->mode_reg = (dev->mode_reg & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_SINGLE;
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);

    if (!ad7193_wait_rdy(dev, 5000)) {
        printf("[AD7193] Temperature read timeout\n");
        // Restore
        dev->conf_reg = conf_backup;
        ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
        dev->mode_reg = mode_backup;
        return -999.0f;
    }

    uint32_t raw = ad7193_read_data(dev);

    // Restore configuration
    dev->conf_reg = conf_backup;
    ad7193_write_reg(dev, AD7193_REG_CONF, dev->conf_reg);
    dev->mode_reg = mode_backup;

    // Temperature conversion formula from datasheet:
    // T(°C) = (raw_code - 0x800000) / 2815 - 273
    // This is an approximation; see datasheet for precise coefficients.
    float temp = ((float)raw - 0x800000) / 2815.0f - 273.0f;
    return temp;
}

// ============================================================================
// Voltage Conversion
// ============================================================================

float ad7193_code_to_voltage(uint32_t raw_code, float vref, uint8_t gain, bool unipolar) {
    if (gain == 0) gain = 1;  // Protect against divide by zero

    if (unipolar) {
        // Unipolar: V = (raw_code / 2^24) * (Vref / Gain)
        return ((float)raw_code / 16777216.0f) * (vref / (float)gain);
    } else {
        // Bipolar: V = ((raw_code - 2^23) / 2^23) * (Vref / Gain)
        // Midscale (0x800000) = 0V, full scale (0xFFFFFF) = +Vref/Gain,
        // zero scale (0x000000) = -Vref/Gain
        return (((float)raw_code / 8388608.0f) - 1.0f) * (vref / (float)gain);
    }
}

// ============================================================================
// Calibration
// ============================================================================

void ad7193_calibrate_internal_zero(ad7193_dev_t *dev) {
    printf("[AD7193] Starting internal zero-scale calibration...\n");
    dev->mode_reg = (dev->mode_reg & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_CAL_INT_Z;
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);

    if (!ad7193_wait_rdy(dev, 10000)) {
        printf("[AD7193] Zero-scale calibration timeout!\n");
        return;
    }
    printf("[AD7193] Zero-scale calibration complete. Offset reg: 0x%06lX\n",
           (unsigned long)ad7193_read_reg(dev, AD7193_REG_OFFSET));
}

void ad7193_calibrate_internal_full(ad7193_dev_t *dev) {
    printf("[AD7193] Starting internal full-scale calibration...\n");
    dev->mode_reg = (dev->mode_reg & ~AD7193_MODE_SEL_MASK) | AD7193_MODE_CAL_INT_F;
    ad7193_write_reg(dev, AD7193_REG_MODE, dev->mode_reg);

    if (!ad7193_wait_rdy(dev, 10000)) {
        printf("[AD7193] Full-scale calibration timeout!\n");
        return;
    }
    printf("[AD7193] Full-scale calibration complete. Full-scale reg: 0x%06lX\n",
           (unsigned long)ad7193_read_reg(dev, AD7193_REG_FULLSCALE));
}

// ============================================================================
// Debug
// ============================================================================

void ad7193_dump_registers(ad7193_dev_t *dev) {
    printf("\n========== AD7193 Register Dump ==========\n");

    uint8_t status = (uint8_t)ad7193_read_reg(dev, AD7193_REG_STATUS);
    printf("  STATUS    (0x00): 0x%02X", status);
    printf("  [RDY=%d ERR=%d NOREF=%d PAR=%d CH=%d]\n",
           (status >> 7) & 1, (status >> 6) & 1, (status >> 5) & 1,
           (status >> 4) & 1, status & 0x0F);

    uint32_t mode = ad7193_read_reg(dev, AD7193_REG_MODE);
    printf("  MODE      (0x01): 0x%06lX", (unsigned long)mode);
    printf("  [Mode=%lu CLK=%lu Rate=%lu]\n",
           (unsigned long)((mode >> 21) & 7), (unsigned long)((mode >> 18) & 3), (unsigned long)(mode & 0x3FF));

    uint32_t conf = ad7193_read_reg(dev, AD7193_REG_CONF);
    printf("  CONFIG    (0x02): 0x%06lX", (unsigned long)conf);
    printf("  [CHOP=%lu PSEUDO=%lu CH=0x%03lX BUF=%lu UNI=%lu GAIN=%lu]\n",
           (unsigned long)((conf >> 23) & 1), (unsigned long)((conf >> 18) & 1), (unsigned long)((conf >> 8) & 0x3FF),
           (unsigned long)((conf >> 4) & 1), (unsigned long)((conf >> 3) & 1), (unsigned long)(conf & 7));

    uint8_t id = (uint8_t)ad7193_read_reg(dev, AD7193_REG_ID);
    printf("  ID        (0x04): 0x%02X\n", id);

    uint8_t gpocon = (uint8_t)ad7193_read_reg(dev, AD7193_REG_GPOCON);
    printf("  GPOCON    (0x05): 0x%02X\n", gpocon);

    uint32_t offset = ad7193_read_reg(dev, AD7193_REG_OFFSET);
    printf("  OFFSET    (0x06): 0x%06lX\n", (unsigned long)offset);

    uint32_t fullscale = ad7193_read_reg(dev, AD7193_REG_FULLSCALE);
    printf("  FULLSCALE (0x07): 0x%06lX\n", (unsigned long)fullscale);

    printf("===========================================\n\n");
}
