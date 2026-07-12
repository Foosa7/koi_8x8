/**
 * AD7193 Dual-Chip Driver Test Program
 *
 * Tests two AD7193 24-bit sigma-delta ADCs on Raspberry Pi Pico.
 * Both chips share the same SPI bus, each with its own CS pin.
 *
 * Wiring (Pico -> AD7193 #1 & #2):
 *   GP17 -> CS1   (AD7193 #1 - Pin 9 /CS)
 *   GP20 -> CS2   (AD7193 #2 - Pin 9 /CS)
 *   GP18 -> SCLK  (Both chips - Pin 2 SCLK, wired in parallel)
 *   GP19 -> MOSI  (Both chips - Pin 10 DIN, wired in parallel)
 *   GP16 -> MISO  (Both chips - Pin 13 DOUT/RDY, wired in parallel)
 *
 *   Both AD7193 chips:
 *   AVDD, DVDD -> 3.3V
 *   AGND, DGND -> GND
 *   AINCOM     -> GND
 *   REFIN1(+)  -> 2.5V reference
 *   REFIN1(-)  -> GND
 */

extern "C" {
#include "ad7193.h"
}

#include "pico/stdlib.h"
#include <stdio.h>

// ============================================================================
// Pin Definitions — adjust to match your wiring
// ============================================================================
#define PIN_CS1   17   // Chip Select for ADC #1
#define PIN_CS2   20   // Chip Select for ADC #2
#define PIN_SCK   18
#define PIN_MOSI  19
#define PIN_MISO  16

// Reference voltage (adjust to your setup)
#define VREF      2.5f

// ============================================================================
// Helper: gain enum to numeric value
// ============================================================================
static uint8_t gain_to_value(uint8_t gain_bits) {
    switch (gain_bits) {
        case AD7193_CONF_GAIN_1:   return 1;
        case AD7193_CONF_GAIN_8:   return 8;
        case AD7193_CONF_GAIN_16:  return 16;
        case AD7193_CONF_GAIN_32:  return 32;
        case AD7193_CONF_GAIN_64:  return 64;
        case AD7193_CONF_GAIN_128: return 128;
        default:                   return 1;
    }
}

// ============================================================================
// Test 1: Device Identification
// ============================================================================
static bool test_device_id(ad7193_dev_t *dev) {
    printf("\n--- Test 1: Device ID ---\n");

    uint8_t id = ad7193_get_id(dev);
    printf("  ID Register: 0x%02X\n", id);
    printf("  Device Family (lower nibble): 0x%X\n", id & 0x0F);
    printf("  Silicon Revision (upper nibble): 0x%X\n", (id >> 4) & 0x0F);

    bool pass = ((id & AD7193_ID_MASK) == AD7193_ID_VALUE);
    printf("  Result: %s\n", pass ? "PASS" : "FAIL");
    return pass;
}

// ============================================================================
// Test 2: Register Read/Write
// ============================================================================
static bool test_register_rw(ad7193_dev_t *dev) {
    printf("\n--- Test 2: Register Read/Write ---\n");

    // Read the current mode register
    uint32_t mode_orig = ad7193_read_reg(dev, AD7193_REG_MODE);
    printf("  Mode Register (original): 0x%06lX\n", mode_orig);

    // Write a known value (change the rate to 200, different from default 96)
    uint32_t mode_test = (mode_orig & ~0x3FF) | 200;
    ad7193_write_reg(dev, AD7193_REG_MODE, mode_test);

    // Read it back
    uint32_t mode_read = ad7193_read_reg(dev, AD7193_REG_MODE);
    printf("  Mode Register (written):  0x%06lX\n", mode_test);
    printf("  Mode Register (readback): 0x%06lX\n", mode_read);

    bool pass = (mode_read == mode_test);
    printf("  Result: %s\n", pass ? "PASS" : "FAIL");

    // Restore original
    ad7193_write_reg(dev, AD7193_REG_MODE, mode_orig);
    dev->mode_reg = mode_orig;

    return pass;
}

// ============================================================================
// Test 3: Register Dump
// ============================================================================
static void test_register_dump(ad7193_dev_t *dev) {
    printf("\n--- Test 3: Full Register Dump ---\n");
    ad7193_dump_registers(dev);
}

// ============================================================================
// Test 4: Internal Calibration
// ============================================================================
static void test_calibration(ad7193_dev_t *dev) {
    printf("\n--- Test 4: Internal Calibration ---\n");
    ad7193_calibrate_internal_zero(dev);
    ad7193_calibrate_internal_full(dev);
}

// ============================================================================
// Test 4b: Internal Short Self-Test
// ============================================================================
static void test_internal_short(ad7193_dev_t *dev) {
    printf("\n--- Test 4b: Internal Short Self-Test (AIN2-AIN2) ---\n");
    printf("  This shorts AIN2 to itself internally.\n");
    printf("  Expected result: ~0V (should be very close to zero)\n\n");

    // Use bipolar mode for this test so we can see if it's near zero
    ad7193_set_pseudo_diff(dev, false);
    ad7193_set_unipolar(dev, false);  // Bipolar: midscale = 0V
    ad7193_set_buffer(dev, true);
    ad7193_set_gain(dev, AD7193_CONF_GAIN_1);
    ad7193_set_rate(dev, 96);

    // Read the short channel (CH9 = bit 17 = AIN2-AIN2)
    uint32_t raw = ad7193_single_conversion(dev, AD7193_CH_SHORT);
    float voltage = ad7193_code_to_voltage(raw, VREF, 1, false);
    printf("  SHORT channel: Raw=0x%06lX  Voltage=%.6f V\n",
           (unsigned long)raw, voltage);

    if (raw == 0xFFFFFF || raw == 0x000000) {
        printf("  WARNING: Saturated reading! SPI may not be working correctly.\n");
    } else if (raw >= 0x7F0000 && raw <= 0x810000) {
        printf("  PASS: Reading is near midscale (0V in bipolar). ADC is working!\n");
    } else {
        printf("  NOTE: Reading is offset from zero. May need calibration.\n");
    }
}

// ============================================================================
// Test 5: Single Conversion on All 8 Channels
// ============================================================================
static void test_all_channels(ad7193_dev_t *dev) {
    printf("\n--- Test 5: Single Conversion - All 8 Channels ---\n");

    // Enable pseudo-differential mode for 8 individual channels
    ad7193_set_pseudo_diff(dev, true);
    ad7193_set_unipolar(dev, true);
    ad7193_set_buffer(dev, true);
    ad7193_set_gain(dev, AD7193_CONF_GAIN_1);
    ad7193_set_rate(dev, 96);  // ~50 Hz output data rate

    uint8_t gain_val = gain_to_value(AD7193_CONF_GAIN_1);

    printf("  Config: Pseudo-Diff, Unipolar, Buffered, Gain=%d, Rate=96\n", gain_val);
    printf("  Vref = %.2f V\n\n", VREF);

    const uint32_t channels[] = {
        AD7193_CH_AIN1, AD7193_CH_AIN2, AD7193_CH_AIN3, AD7193_CH_AIN4,
        AD7193_CH_AIN5, AD7193_CH_AIN6, AD7193_CH_AIN7, AD7193_CH_AIN8,
    };
    const char *ch_names[] = {
        "AIN1", "AIN2", "AIN3", "AIN4",
        "AIN5", "AIN6", "AIN7", "AIN8",
    };

    for (int i = 0; i < 8; i++) {
        uint32_t raw = ad7193_single_conversion(dev, channels[i]);
        float voltage = ad7193_code_to_voltage(raw, VREF, gain_val, true);
        printf("  CH%-4s: Raw=0x%06lX (%7lu)  Voltage=%.6f V\n",
               ch_names[i], raw, raw, voltage);
    }
}

// ============================================================================
// Test 6: Temperature Sensor
// ============================================================================
static void test_temperature(ad7193_dev_t *dev) {
    printf("\n--- Test 6: On-Chip Temperature Sensor ---\n");

    float temp = ad7193_read_temperature(dev);
    if (temp > -999.0f) {
        printf("  Temperature: %.2f °C\n", temp);
    } else {
        printf("  Temperature read FAILED\n");
    }
}

// ============================================================================
// Test 7: Continuous Conversion on a Single Channel
// ============================================================================
static void test_continuous(ad7193_dev_t *dev) {
    printf("\n--- Test 7: Continuous Conversion (AIN1, 10 samples) ---\n");

    ad7193_set_pseudo_diff(dev, true);
    ad7193_set_unipolar(dev, true);
    ad7193_set_buffer(dev, true);
    ad7193_set_gain(dev, AD7193_CONF_GAIN_1);
    ad7193_set_rate(dev, 96);

    // Select channel 1
    ad7193_set_channel(dev, AD7193_CH_AIN1);

    // Start continuous conversion
    ad7193_set_mode(dev, AD7193_MODE_CONT);

    uint8_t gain_val = gain_to_value(AD7193_CONF_GAIN_1);

    for (int i = 0; i < 10; i++) {
        if (!ad7193_wait_rdy(dev, 2000)) {
            printf("  Sample %d: TIMEOUT\n", i);
            continue;
        }
        uint32_t raw = ad7193_read_data(dev);
        float voltage = ad7193_code_to_voltage(raw, VREF, gain_val, true);
        printf("  Sample %2d: Raw=0x%06lX  Voltage=%.6f V\n", i, raw, voltage);
    }

    // Return to idle mode
    ad7193_set_mode(dev, AD7193_MODE_IDLE);
}

// ============================================================================
// Helper: initialize and test a single device
// ============================================================================
static bool init_and_test(ad7193_dev_t *dev, const ad7193_config_t *config, const char *label) {
    printf("\n============================================\n");
    printf("  Initializing %s (CS=GP%d)\n", label, config->pin_cs);
    printf("============================================\n");

    int ret = ad7193_init(dev, config);
    if (ret != 0) {
        printf("  [FAIL] %s initialization failed! Check wiring.\n", label);
        return false;
    }

    // Quick test suite
    printf("\n  [%s] ID: 0x%02X — ", label, ad7193_get_id(dev));
    printf("PASS\n");

    // Register R/W test
    uint32_t mode_orig = ad7193_read_reg(dev, AD7193_REG_MODE);
    uint32_t mode_test = (mode_orig & ~0x3FF) | 200;
    ad7193_write_reg(dev, AD7193_REG_MODE, mode_test);
    uint32_t mode_read = ad7193_read_reg(dev, AD7193_REG_MODE);
    printf("  [%s] Register R/W: %s\n", label,
           (mode_read == mode_test) ? "PASS" : "FAIL");
    ad7193_write_reg(dev, AD7193_REG_MODE, mode_orig);
    dev->mode_reg = mode_orig;

    // Calibration
    ad7193_calibrate_internal_zero(dev);
    ad7193_calibrate_internal_full(dev);

    // Internal short test
    ad7193_set_pseudo_diff(dev, false);
    ad7193_set_unipolar(dev, false);
    ad7193_set_buffer(dev, true);
    ad7193_set_gain(dev, AD7193_CONF_GAIN_1);
    ad7193_set_rate(dev, 96);
    uint32_t short_raw = ad7193_single_conversion(dev, AD7193_CH_SHORT);
    float short_v = ad7193_code_to_voltage(short_raw, VREF, 1, false);
    printf("  [%s] Internal Short: Raw=0x%06lX  V=%.6f — %s\n", label,
           (unsigned long)short_raw, short_v,
           (short_raw >= 0x7F0000 && short_raw <= 0x810000) ? "PASS" : "CHECK");

    // Temperature
    float temp = ad7193_read_temperature(dev);
    printf("  [%s] Temperature: %.2f °C\n", label, temp);

    // Configure for channel reading: pseudo-diff, unipolar, buffered, gain=1
    ad7193_set_pseudo_diff(dev, true);
    ad7193_set_unipolar(dev, true);
    ad7193_set_buffer(dev, true);
    ad7193_set_gain(dev, AD7193_CONF_GAIN_1);
    ad7193_set_rate(dev, 96);

    printf("  [%s] Ready!\n", label);
    return true;
}

// ============================================================================
// Main
// ============================================================================
int main() {
    stdio_init_all();
    sleep_ms(3000);  // Wait for USB serial to connect

    printf("\n");
    printf("============================================\n");
    printf("  AD7193 Dual-Chip Test Suite\n");
    printf("  Raspberry Pi Pico — SPI0\n");
    printf("============================================\n");
    printf("  Shared: SCK=GP%d, MOSI=GP%d, MISO=GP%d\n", PIN_SCK, PIN_MOSI, PIN_MISO);
    printf("  ADC #1: CS=GP%d\n", PIN_CS1);
    printf("  ADC #2: CS=GP%d\n", PIN_CS2);
    printf("============================================\n");

    // ----- Device 1 -----
    ad7193_config_t config1 = {
        .spi      = spi0,
        .pin_cs   = PIN_CS1,
        .pin_sck  = PIN_SCK,
        .pin_mosi = PIN_MOSI,
        .pin_miso = PIN_MISO,
        .spi_freq = 1000000,
    };
    ad7193_dev_t dev1;
    bool dev1_ok = init_and_test(&dev1, &config1, "ADC1");

    // ----- Device 2 -----
    ad7193_config_t config2 = {
        .spi      = spi0,
        .pin_cs   = PIN_CS2,
        .pin_sck  = PIN_SCK,
        .pin_mosi = PIN_MOSI,
        .pin_miso = PIN_MISO,
        .spi_freq = 1000000,
    };
    ad7193_dev_t dev2;
    bool dev2_ok = init_and_test(&dev2, &config2, "ADC2");

    if (!dev1_ok && !dev2_ok) {
        printf("\n[FATAL] Neither ADC initialized! Check wiring.\n");
        while (true) { sleep_ms(1000); }
    }

    printf("\n============================================\n");
    printf("  Init complete: ADC1=%s  ADC2=%s\n",
           dev1_ok ? "OK" : "FAIL", dev2_ok ? "OK" : "FAIL");
    printf("============================================\n");

    // ----- Continuous monitoring loop: both chips -----
    printf("\n--- Continuous Monitoring (16 channels) ---\n");
    printf("Press Ctrl+C to stop.\n\n");

    uint8_t gain_val = gain_to_value(AD7193_CONF_GAIN_1);

    const uint32_t channels[] = {
        AD7193_CH_AIN1, AD7193_CH_AIN2, AD7193_CH_AIN3, AD7193_CH_AIN4,
        AD7193_CH_AIN5, AD7193_CH_AIN6, AD7193_CH_AIN7, AD7193_CH_AIN8,
    };
    const char *ch_names[] = {
        "AIN1", "AIN2", "AIN3", "AIN4",
        "AIN5", "AIN6", "AIN7", "AIN8",
    };

    while (true) {
        printf("Device  CH     Raw Code   Voltage\n");
        printf("------  ----   --------   --------\n");

        // Read ADC1
        if (dev1_ok) {
            for (int i = 0; i < 8; i++) {
                uint32_t raw = ad7193_single_conversion(&dev1, channels[i]);
                float voltage = ad7193_code_to_voltage(raw, VREF, gain_val, true);
                printf("ADC1    %-4s   0x%06lX   %.6f V\n",
                       ch_names[i], (unsigned long)raw, voltage);
            }
        }

        // Read ADC2
        if (dev2_ok) {
            for (int i = 0; i < 8; i++) {
                uint32_t raw = ad7193_single_conversion(&dev2, channels[i]);
                float voltage = ad7193_code_to_voltage(raw, VREF, gain_val, true);
                printf("ADC2    %-4s   0x%06lX   %.6f V\n",
                       ch_names[i], (unsigned long)raw, voltage);
            }
        }

        printf("\n");
        sleep_ms(2000);
    }

    return 0;
}