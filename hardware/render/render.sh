#!/usr/bin/env bash
# Regenerate the photorealistic board renders (KiCad -> pcb2blender -> Cycles).
#
#   ./hardware/render/render.sh            # both boards, all views
#   ./hardware/render/render.sh mother     # one board
#
# See README.md in this directory for the one-time Blender add-on install.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HW="$(dirname "$HERE")"
WORK="${WORK:-$(mktemp -d)}"
SAMPLES="${SAMPLES:-160}"

render_board() {
    local name="$1" dir="$2" width="$3" height="$4"
    local pcb3d="$WORK/$name.pcb3d"

    echo "==> exporting $name.pcb3d"
    python3 "$HERE/export_pcb3d.py" "$dir/$name.kicad_pcb" "$pcb3d" --finish ENIG

    local common=(--pcb3d "$pcb3d" --samples "$SAMPLES" --width "$width" --height "$height")
    echo "==> rendering $name (iso, top, bottom, transparent)"
    blender -b -P "$HERE/render_pcb.py" -- "${common[@]}" \
        --view iso    --out "$dir/${name}_render.png"
    blender -b -P "$HERE/render_pcb.py" -- "${common[@]}" \
        --view top    --out "$dir/${name}_render_top.png"
    blender -b -P "$HERE/render_pcb.py" -- "${common[@]}" \
        --view bottom --out "$dir/${name}_render_bottom.png"
    blender -b -P "$HERE/render_pcb.py" -- "${common[@]}" \
        --view top --transparent --out "$dir/${name}_render_top_transparent.png"
}

target="${1:-all}"
if [[ "$target" == all || "$target" == daughter* ]]; then
    render_board daughterboard "$HW/daughterboard" 1568 1176
fi
if [[ "$target" == all || "$target" == mother* ]]; then
    render_board motherboard "$HW/motherboard" 1723 921
fi

echo "done; intermediates in $WORK"
