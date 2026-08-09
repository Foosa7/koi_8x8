#!/usr/bin/env python3
"""Headless pcb2blender exporter: .kicad_pcb -> .pcb3d, no pcbnew GUI needed.

pcb2blender ships a pcbnew *action plugin*, which means clicking a button in the
GUI. Two small patches make it scriptable:

  * it calls pcbnew.GetBoard() for the currently-open board -> we load the board
    ourselves and patch that in;
  * it calls pcbnew.ExportVRML(), which returns False outside the pcbnew GUI (it
    needs the frame's 3D-model cache) -> we route that step through kicad-cli.

Usage:

    python3 export_pcb3d.py board.kicad_pcb out.pcb3d [--finish ENIG]

Requires the pcb2blender exporter sources under ./pcb2blender_exporter/ (see
README.md in this directory).
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import pcbnew  # noqa: E402

VRML_UNITS = {1.0: "mm", 0.001: "m", 1.0 / 25.4: "in"}


def export_vrml_via_cli(board_path: str):
    def _export(wrl_path, mm_to_wrml_unit, include_unspecified, include_dnp,
                _export_3d_files, _use_relative_paths, subdir, x_ref, y_ref):
        wrl_path = Path(wrl_path)
        wrl_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "kicad-cli", "pcb", "export", "vrml",
            "-o", str(wrl_path), "--force",
            "--units", VRML_UNITS[mm_to_wrml_unit],
            "--models-dir", Path(subdir).name, "--models-relative",
            # The plugin exports pad coordinates in absolute page mm and calls
            # ExportVRML with xRef=yRef=0. kicad-cli would otherwise centre the
            # board on its own origin, which offsets every solder joint by ~100 mm.
            "--user-origin", f"{x_ref}x{y_ref}mm",
        ]
        if not include_unspecified:
            cmd.append("--no-unspecified")
        if not include_dnp:
            cmd.append("--no-dnp")
        cmd.append(board_path)
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        return True

    return _export


def override_finish(module, finish: str) -> None:
    """Both Koi boards carry `copper_finish "None"` in their stackup, which renders
    the PCIe edge fingers as bare copper. The real boards are gold-plated."""
    from pcb2blender_exporter.pcb3d import SurfaceFinish

    original = module.get_stackup

    def patched(board):
        stackup = original(board)
        stackup.surface_finish = SurfaceFinish[finish]
        return stackup

    module.get_stackup = patched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("board")
    parser.add_argument("out")
    parser.add_argument("--finish", choices=("ENIG", "HASL", "NONE"), default=None,
                        help="override the stackup's copper finish")
    args = parser.parse_args()

    board = pcbnew.LoadBoard(args.board)
    pcbnew.GetBoard = lambda: board
    pcbnew.ExportVRML = export_vrml_via_cli(args.board)

    from pcb2blender_exporter import export as exporter

    if args.finish:
        override_finish(exporter, args.finish)

    boarddefs, ignored = exporter.get_boarddefs(board)
    if boarddefs:
        print(f"board definitions: {sorted(boarddefs)}")
    if ignored:
        print(f"ignored PCB3D_ texts: {ignored}")

    exporter.export_pcb3d(args.out, boarddefs)
    print(f"wrote {args.out} ({Path(args.out).stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
