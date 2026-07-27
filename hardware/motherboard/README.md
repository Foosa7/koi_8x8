# Motherboard — RP2040 controller board

The controller board for Koi 8×8. Carries a Raspberry Pi Pico (RP2040) and
hosts up to 8 [daughterboards](../daughterboard/) in PCIe-x4 card-edge slots,
addressing each board's DAC/ADC over a shared SPI bus. Formerly codenamed
`MOBO`. The PC talks to the Pico's firmware over USB serial — see
[`firmware/motherboard-test/`](../../firmware/motherboard-test/).

![Motherboard 3D render](motherboard.png)

## What's on it

| Ref | Part | Function |
| --- | --- | --- |
| A1 | Raspberry Pi Pico (RP2040) | Runs the firmware; USB-serial host link |
| U1, U5 | SN74LV138 (74HC138) | 3-to-8 decoders — ADC/DAC chip-select across the 8 slots |
| U3 | SN74LV595 | Shift register — XTR200 front-end enables |
| J1–J8 | Samtec PCIE-064-02-F-D-TH | 8 daughterboard slots (PCIe-x4 sockets) |
| J9–J11 | Molex 54132-5033 | 50-pin FFC connectors — channel outputs |
| J12 | Barrel jack + switch pin | 12 V DC power in |
| PS1 | XP Power DSM2-12-S3-S | Isolated 12 V DC-DC converter |

Full pricing and part numbers: [`../../master_bom.csv`](../../master_bom.csv).

## Files

| File | What it is |
| --- | --- |
| `motherboard.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | The KiCad project |
| `Schematics/motherboard.pdf` | Exported schematic |
| `production/` | Fab outputs — gerbers, BOM, positions, netlist |
| `PCIE-064-02-F-D-TH.stp`, `Pico.wrl` | 3D models used for the render above |
