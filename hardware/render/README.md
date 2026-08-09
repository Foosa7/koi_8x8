# Board renders

Headless KiCad -> Blender pipeline that produces the board PNGs checked in next
to each KiCad project (`hardware/motherboard/motherboard.png`,
`hardware/daughterboard/daughterboard*.png`).

Two steps:

```bash
# 1. .kicad_pcb -> .pcb3d   (KiCad python with pcbnew, plus kicad-cli on PATH)
python3 export_pcb3d.py ../motherboard/motherboard.kicad_pcb motherboard.pcb3d --finish ENIG

# 2. .pcb3d -> PNG          (Blender)
blender -b -P render_pcb.py -- --pcb3d motherboard.pcb3d --out motherboard.png --view iso
```

Options come after the `--` separator. `--view` is one of `top`, `iso`,
`iso-low`, `bottom`; `--transparent` keeps an alpha background with shadows
(used for `daughterboard_top_transparent.png`); `--samples`, `--width`,
`--height`, `--margin` and `--exposure` control quality and framing.

## pcb2blender_exporter/

Vendored from [pcb2blender](https://github.com/30350n/pcb2blender) (GPLv3) —
the exporter half only, lightly patched so it runs outside the pcbnew GUI:
`export_pcb3d.py` loads the board itself instead of calling
`pcbnew.GetBoard()`, and routes VRML export through `kicad-cli` because
`pcbnew.ExportVRML()` needs the GUI frame's 3D-model cache. Upstream owns the
`.pcb3d` format — re-vendor from it rather than diverging further here.
