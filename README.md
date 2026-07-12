# Koi 8×8 — 64-channel current-source driver / readout

An open-hardware, low-cost controller for driving **TiN thermo-optic heaters on
photonic chips**: 64 independent precision current sources with per-channel
voltage readback, built from one controller board and eight hot-pluggable
8-channel analog boards.

![System architecture](report/figures/fig0_architecture.png)

Each daughterboard pairs a 16-bit DAC (DAC80508) with eight XTR200 precision
current sources, and reads the voltage across each load through a 6:1 divider
with a 24-bit ΔΣ ADC (AD7193), so the host gets current, voltage, resistance,
and power per channel. A Raspberry Pi Pico (RP2040) on the motherboard runs a
line-based ASCII command server over USB — one `ISETA` sets all 64 currents,
one `MEASA?` reads all 64 voltages.

## Key numbers

| Metric | Value |
| --- | --- |
| Channels | 64 (8 boards × 8), hot-pluggable per board |
| Current range | 0–6 mA per channel (working range 0.25–6 mA), 16-bit setting |
| Readback | 24-bit ΔΣ, divider-corrected load voltage → V / R / P per channel |
| Linearity (measured, 23 ch) | worst-case ±0.32 µA deviation from linear over 0.25–6 mA (≈0.006 % of span) |
| Offset (pre-calibration) | ≈24 µA systematic, uniform across channels; single-point cal brings it to the ±4 µA XTR200 device floor (Keithley campaign planned) |
| Full 64-channel scan | ≈11 ms/channel at the default filter rate (~0.7 s for all 64; filter rate adjustable live) |
| Parts cost | **$723 per system ≈ $11.30/channel** ([master_bom.csv](master_bom.csv)) — commercial equivalents (e.g. Qontrol) ≈ $125/channel |

## Measured results (3 boards / 24 channels, 2026-07-03)

| | |
| --- | --- |
| ![V-I linearity](report/figures/fig1_linearity_23ch.png) | ![Residuals](report/figures/fig2_linearity_residuals.png) |

![Per-channel uniformity](report/figures/fig3_channel_uniformity.png)

Three populated daughterboards were swept 0.1–6 mA into nominal 820 Ω ±1 %
loads (one channel had no load fitted during the run and is excluded). All 23
loaded channels are linear to within ±0.32 µA and show a uniform ~24 µA
pre-calibration offset — a systematic that the planned per-channel calibration
removes. Figures are regenerated from the raw bench CSVs by
`report/figures/make_figures.py`.

## What's in this folder

| Path | Contents |
| --- | --- |
| `report/figures/` | Architecture diagram + measurement figures (and the scripts that generate them) |
| `hardware/motherboard/` | KiCad project, schematic PDF (`Schematics/`), 3D render (`motherboard.png`), fab outputs (`production/`: gerbers, BOM, positions) |
| `hardware/daughterboard/` | Same set for the analog board, incl. top/bottom renders and an interactive HTML BOM (`bom/ibom.html`) |
| `master_bom.csv` | Consolidated system BOM with unit prices and cost total |
| `firmware/motherboard-test/` | RP2040 firmware (PlatformIO, `pio run -t upload`) and Python host tools — `tools/koi_gui.py` is a live 8×8 monitor GUI; `tools/*.csv` are the raw bench datasets |
| `paper/main.pdf` | Draft HardwareX manuscript (validation section pending the calibration campaign) |

## Status (July 2026)

**Done:** rev-1 hardware designed, fabricated, and brought up; firmware 1.1
with robust host protocol (auto-detection, hot-rescan, fast scanning); host GUI
and calibration tooling; three daughterboards bench-validated (results above).

**Next:** Keithley 2400/2100 per-channel calibration campaign (jig designed,
parts list ready), population of all 8 boards, HardwareX submission.
