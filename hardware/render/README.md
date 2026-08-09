# Photorealistic board renders

Cycles renders of the two KiCad boards, via
[pcb2blender](https://github.com/30350n/pcb2blender) (GPL-3.0, by 30350n). The
`.pcb3d` intermediate carries the real soldermask/silkscreen/copper artwork as
texture maps plus the component meshes, so the output is a lit 3D scene rather
than KiCad's viewport screenshot.

These are **separate files** from the `kicad-cli`-generated `*_top.png` /
`*_bottom.png` / `*.png` next to each project — those stay as the quick
reference views. Blender output is named `*_render*.png`.

## One-time setup

The upstream release is pinned to KiCad 9.0 / Blender 5.1; both need nudging.

1. **Blender add-on.** Download `pcb2blender_importer_v2-17-5_b5-1.zip` from the
   [v2.17.5 release](https://github.com/30350n/pcb2blender/releases/tag/v2.17.5-k9.0-b5.1).
   Blender 5.2 is excluded by the manifest's `blender_version_max`, so raise it
   before installing:

   ```bash
   unzip -q pcb2blender_importer_v2-17-5_b5-1.zip -d imp && \
   sed -i 's/^blender_version_max = "5.2.0"/blender_version_max = "5.3.0"/' imp/blender_manifest.toml && \
   (cd imp && zip -qr ../importer_patched.zip .) && \
   blender --command extension install-file -r user_default --enable importer_patched.zip
   ```

2. **Exporter.** Already vendored here as `pcb2blender_exporter/` (`export.py` +
   `pcb3d.py`, unmodified, from `pcb2blender_exporter_v2-17-5_k9-0.zip`). It is
   normally a pcbnew action plugin; `export_pcb3d.py` drives it headless instead.

## Running

```bash
./hardware/render/render.sh              # both boards, all four views
./hardware/render/render.sh mother       # one board
SAMPLES=320 ./hardware/render/render.sh  # slower, cleaner
```

Roughly 8 min per view at 1568×1176 / 160 samples on 12 CPU cores.

## Files

| File | What it does |
| --- | --- |
| `export_pcb3d.py` | `.kicad_pcb` → `.pcb3d`, headless. Patches `pcbnew.GetBoard()` and routes VRML export through `kicad-cli`. |
| `render_pcb.py` | Blender script: import `.pcb3d`, build a studio rig, render. `--view iso\|top\|bottom`, `--transparent`, `--samples`, `--exposure`. |
| `render.sh` | Both boards × four views at the sizes the READMEs expect. |
| `pcb2blender_exporter/` | Vendored upstream exporter (GPL-3.0). |

## KiCad 10 notes

- `pcbnew.ExportVRML()` returns `False` outside the pcbnew GUI, so
  `export_pcb3d.py` shells out to `kicad-cli pcb export vrml` instead. That call
  **must** pass `--user-origin 0x0mm`: the plugin emits pad coordinates in
  absolute page mm, and kicad-cli otherwise centres the mesh on its own origin,
  scattering every solder joint ~100 mm off the board.
- `unknown drill shape '2'` during export is harmless — KiCad 10 added
  `PAD_DRILL_SHAPE_UNDEFINED`, which the pinned exporter's enum doesn't list. It
  degrades to `UNKNOWN` and only affects solder-joint geometry on pads with no
  drill.
- Cycles' device enumeration walks *every* GPU backend, and the oneAPI
  level-zero loader segfaults Blender on this machine, so renders default to
  CPU. `--gpu` opts back in if that gets fixed.

## Surface finish

Both boards carry `copper_finish "None"` in their KiCad stackup, which would
render the PCIe edge fingers as bare copper. `render.sh` passes `--finish ENIG`
to override that. Fix the stackup in KiCad and the override can go.
