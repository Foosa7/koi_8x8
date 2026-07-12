# Koi 8×8 — HardwareX manuscript

Draft of the HardwareX paper for the 64-channel current-source driver / readout controller.

## Build
```bash
make            # -> main.pdf  (runs pdflatex twice)
make clean      # remove aux files
```
Requires `pdflatex` (TeX Live). No bibliography is wired up yet (`refs.bib` is empty).

## Status / how to work in it
- `main.tex` is the manuscript. It follows the HardwareX section order
  (Specifications table → Hardware in context → Description → Design files →
  BOM → Build → Operation → Validation → Capabilities → Declarations).
- Search `main.tex` for `\todo{...}` (rendered in red in the PDF) — each marks
  text or data still to add. The biggest block is **Section 8, Validation** —
  that's the Keithley calibration campaign output.
- Set `\draftfalse` (comment the `\drafttrue` line near the top) to hide the
  TODO/notes markers for a clean read-through.
- For final submission, port into the official HardwareX `els-cas-templates`
  (elsarticle) template; this article-class version is for drafting.

## Where the seeded content came from
Architecture prose, the host-command protocol table, the measurement model, and
preliminary bench numbers are transcribed from the project `CLAUDE.md`. The
calibration tables and all quantitative validation figures are pending the
Keithley 2400 campaign.
