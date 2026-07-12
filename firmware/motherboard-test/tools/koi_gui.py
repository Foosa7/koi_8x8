#!/usr/bin/env python3
"""
koi_gui.py — live 8x8 viewer for the Koi current-driver firmware.

Polls the Pico's `MEASA?` command and shows, for all 64 channels at once:
  - RAW    : the raw ADC-pin voltage reported by the firmware
  - HEATER : (raw + offset) x divider  (the true voltage across the TiN heater)
  - I      : true heater current = commanded current − sense-divider loss
             (the 100k/20k divider steals (raw+offset)/20k; compare against
             your source meter in series with the heater)
  - R      : heater_V / I    (the unknown, variable load resistance, derived)
  - P      : heater_V x I    (power dissipated in the heater)

The XTR200 is a *current source*: the current through the load is set by the DAC
(IOUT = 10 x Vdac / 4.7k), not by the load, so the current is the known quantity
and the load resistance is what we solve for from the measured voltage. The
firmware reports the raw ADC-pin voltage; this GUI adds a small ADC zero-offset
(mV, editable) and applies the divider (editable) to recover the heater voltage.

"Sweep + cal" sweeps one channel's current 0..max, logs raw_V vs I to a CSV, and
least-squares fits raw_V = slope*I + offset: it auto-fills the Offset+ field with
the fitted zero, and (if you enter the known calibration resistance Rcal) the
Divider field with Rcal/slope. Channels on unpopulated boards come back as `nan`
and are shown greyed out.

Robustness (fw1.1): every command reply echoes its command name, so command()
verifies each reply belongs to the command it sent and resyncs+retries on a
mismatch (two-way ack). The poller RESCANs automatically at connect (recovers
boards the boot-time detect missed — flaky HASL contact) and re-RESCANs, rate-
limited, whenever a previously seen board stops answering.

Per-channel offset table ("Cal offsets"): single-point cal against a known load
resistance. All populated channels are driven to I_test at once (so the shared
ground-return drop matches operating conditions), the voltage is measured, and
each channel's XTR200 offset CURRENT is solved from
    IOS = heater_V/R_known + heater_V/120k − I_test.
It is stored as a current (mA), not a voltage, because IOS is a real current
offset (datasheet ±10 µA max): its voltage signature scales with the load, so a
fixed mV correction would go stale the moment the heater R changes with power.
The table is applied to the I/R/P columns and to Characterize R, and persists
in offset_table.json (auto-loaded at startup). Channels that read railed
(no load fitted → compliance rail) are skipped, not poisoned into the table.

Each cell also has a current setpoint box (mA): type a value and press Enter to
drive that channel (sends `ISET g mA`). "Apply all"/"Zero all" set every channel
at once (`ISETA`). The firmware converts mA → DAC code via the XTR200 relation
IOUT = 10 × Vdac / 4.7k, so the host just sends current in mA.

Usage:
    python3 koi_gui.py                 # auto-detect the Pico serial port
    python3 koi_gui.py --port /dev/ttyACM0
    python3 koi_gui.py --divider 6.0 --interval 1.0

Requires: pyserial   (pip install pyserial)
"""

import argparse
import csv
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk

import serial
import serial.tools.list_ports

# Optional: resistance-characterization graph + cubic fit need numpy/matplotlib.
# The rest of the GUI works without them; only "Characterize R" graph export does.
try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")            # headless: render straight to a PNG file
    import matplotlib.pyplot as plt
    HAVE_PLOT = True
except ImportError:
    HAVE_PLOT = False

NUM_BOARDS = 8
CH_PER_BOARD = 8
TOTAL_CH = NUM_BOARDS * CH_PER_BOARD
DEFAULT_DIVIDER = 6.0
DEFAULT_OFFSET_MV = 0.0     # ADC zero-offset to add back to the raw pin voltage
DEFAULT_BAUD = 115200

# XTR200 current source: IOUT = GAIN × Vdac / RSET  (GAIN=10, RSET=4.7k).
# The firmware does this conversion when it receives a current in mA; these
# constants are only used here to display the implied Vdac and the max current.
XTR_GAIN = 10.0
XTR_RSET = 4700.0
DAC_VREF = 3.0

# Sense-divider current loss. The 6:1 divider (100k + 20k) sits across the output
# node and steals I_div = V_heater / 120k from the load. Equivalently it is just
# Ohm's law on the 20k bottom leg, whose voltage IS the ADC-pin voltage (raw +
# offset): I_div = (raw + offset) / 20k — ratio-independent. The XTR200 sources
# the commanded current accurately (datasheet RO = 47 GΩ, ideal); that current
# splits between the heater and the divider, so the TRUE heater current is
# I_heater = I_commanded − I_div (~0.68 % at 0.81 V / 811 Ω, scales with V).
DIVIDER_BOTTOM_OHMS = 20000.0

# Per-channel XTR200 offset-current table (mA), built by "Cal offsets" and
# auto-loaded at startup. Lives in the working directory next to the sweep CSVs.
OFFSET_TABLE_FILE = "offset_table.json"
#   Vdac = I[A] × RSET / GAIN   →   I_max at Vdac=VREF:
MAX_CURRENT_MA = (XTR_GAIN * DAC_VREF / XTR_RSET) * 1e3   # ≈ 6.383 mA


def current_to_vdac(ma):
    """Volts the DAC must output to source `ma` milliamps (display only)."""
    return (ma * 1e-3) * XTR_RSET / XTR_GAIN


# ──────────────────────────────────────────────────────────────────────────
# Serial link to the firmware
# ──────────────────────────────────────────────────────────────────────────
def autodetect_port():
    """Return the most likely Pico CDC port, or None."""
    candidates = []
    for p in serial.tools.list_ports.comports():
        name = (p.device or "")
        desc = f"{p.description} {p.manufacturer}".lower()
        if "ttyacm" in name.lower() or "usbmodem" in name.lower() \
                or "pico" in desc or "board cdc" in desc or "mbed" in desc:
            candidates.append(p.device)
    return candidates[0] if candidates else None


class KoiLink:
    """Thin synchronous wrapper around the firmware's line protocol."""

    def __init__(self, port, baud=DEFAULT_BAUD, timeout=10.0):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        # Give the board a moment, then flush the power-on banner.
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    @staticmethod
    def _reply_matches(cmd, line):
        """True if `line` has the reply shape this command produces (two-way
        ack). fw1.1 echoes the command name in every OK reply ("OK ISET 5",
        "OK RESCAN active=0x.."), MEASA? replies are a 64-value CSV, MEAS? a
        bare float, *IDN? starts with KOI (or MOBO from pre-rename firmware). An ERR is a valid (in-sync) reply
        to anything. Anything else is a stray line from a desynced stream."""
        if line.startswith("ERR"):
            return True
        key = cmd.split()[0].upper()
        if key == "*IDN?":
            # Accept the old MOBO prefix until every Pico is reflashed.
            return line.startswith(("KOI", "MOBO"))
        if key == "MEASA?":
            return line.count(",") == TOTAL_CH - 1
        if key == "MEAS?":
            try:
                float(line)
                return True
            except ValueError:
                return False
        # OK-echo commands (ISET, ISETA, XTR, RESCAN, PING?, ...); the echo
        # drops the '?' (PING? -> "OK PING ...").
        return line.upper().startswith("OK " + key.rstrip("?"))

    def command(self, cmd, want_reply=True, retries=1):
        """Send one command; return its validated reply line (or None).

        Two-way ack: the reply must match the shape the firmware produces for
        THIS command (see _reply_matches). A mismatch means the stream is
        desynced — e.g. a straggler reply from a timed-out earlier command —
        so drain until the line is quiet and resend, rather than handing the
        stray line to the caller as if it were data."""
        for _attempt in range(1 + retries):
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\n").encode())
            self.ser.flush()
            if not want_reply:
                return None
            # Skip '#' debug/stream lines and blanks. Generous deadline: a full
            # MEASA? sweep is silent until it dumps the whole line, and can take
            # a couple of seconds (longer if a channel times out).
            deadline = time.time() + 10.0
            line = None
            while time.time() < deadline:
                line = self.ser.readline().decode(errors="replace").strip()
                if line and not line.startswith("#"):
                    break
                line = None
            if line is not None and self._reply_matches(cmd, line):
                return line
            self.resync()          # timeout or stray line → clean slate, retry
        return None

    @staticmethod
    def _parse_meas(line):
        """Parse a MEASA? CSV reply into exactly 64 floats (NaN where absent)."""
        out = []
        for tok in line.split(","):
            tok = tok.strip()
            try:
                out.append(float(tok))  # 'nan' parses to float('nan')
            except ValueError:
                out.append(float("nan"))
        # Pad/truncate defensively to exactly 64.
        if len(out) < TOTAL_CH:
            out += [float("nan")] * (TOTAL_CH - len(out))
        return out[:TOTAL_CH]

    def measure_all(self):
        """Return a list of 64 floats (NaN for unpopulated channels)."""
        line = self.command("MEASA?")
        return self._parse_meas(line) if line else None

    def measure_mask(self, mask):
        """Measure only the boards selected in `mask`; others come back NaN."""
        line = self.command(f"MEASA? {mask}")
        return self._parse_meas(line) if line else None

    def resync(self, quiet_s=0.25):
        """Recover from a desynced stream: read and discard everything until the
        firmware has been quiet for `quiet_s`, then clear the buffers. Used before
        a RESCAN and after a dropout so a half-sent reply can't misalign the next."""
        old = self.ser.timeout
        try:
            self.ser.timeout = quiet_s
            while self.ser.readline():     # drain until a read times out (no data)
                pass
        finally:
            self.ser.timeout = old
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def rescan(self, timeout=30.0):
        """Trigger a firmware re-detect and return the active-board bitmask parsed
        from its 'OK active=0x.. new=0x..' reply (or None on failure). Resyncs
        first; RESCAN can take a few seconds (per-board probe + retries), so the
        deadline is generous — far longer than a MEASA?."""
        self.resync()
        self.ser.write(b"RESCAN\n")
        self.ser.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if not line or line.startswith("#"):
                continue
            m = re.search(r"active=0x([0-9a-fA-F]+)", line)
            if m:
                return int(m.group(1), 16)
            if line.startswith("ERR"):
                return None
        return None

    def ping(self, board):
        """Liveness check of one board (fw1.1 `PING? b`): the firmware re-runs
        the ADC ID + one real conversion — the only readback a board has, and
        the proxy for the whole board (incl. the write-only DAC) being seated.
        Returns True/False, or None if the firmware didn't answer."""
        line = self.command(f"PING? {board}")
        if line is None or line.startswith("ERR"):
            return None
        parts = line.split()
        return parts[-1] == "1" if parts else None

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────
# Background poller thread
# ──────────────────────────────────────────────────────────────────────────
class Poller(threading.Thread):
    def __init__(self, link, out_queue, cmd_queue, interval):
        super().__init__(daemon=True)
        self.link = link
        self.q = out_queue
        self.cmd_q = cmd_queue      # outbound commands from the GUI (ISET/ISETA/…)
        self.interval = interval
        self._stop = threading.Event()
        self._sweep = None          # pending (channel, imax, step, settle), or None
        self._sweep_lock = threading.Lock()
        self._char = None           # pending characterize params dict, or None
        self._char_lock = threading.Lock()
        self._offcal = None         # pending offset-table cal params, or None
        self._offcal_lock = threading.Lock()
        self._rescan_req = True     # start with a firmware re-detect: recovers
                                    # boards the boot-time scan missed (flaky
                                    # HASL contact) without a manual click
        self.active_boards = None   # discovered once from a full scan
        self._grid = [float("nan")] * TOTAL_CH   # persistent last-known grid
        self._expected_mask = 0     # boards adopted from the last good RESCAN
        self._auto_rescan_last = 0.0   # rate-limit for automatic re-detects

    def request_sweep(self, channel, imax, step, settle):
        """Queue a sweep to run inline on this thread (keeps serial single-owner)."""
        with self._sweep_lock:
            self._sweep = (channel, imax, step, settle)

    def _take_sweep(self):
        with self._sweep_lock:
            s = self._sweep
            self._sweep = None
            return s

    def request_characterize(self, params):
        """Queue a resistance characterization (runs inline on this thread).
        `params` is a dict captured on the GUI thread (incl. divider/offset)."""
        with self._char_lock:
            self._char = params

    def _take_characterize(self):
        with self._char_lock:
            c = self._char
            self._char = None
            return c

    def request_offset_cal(self, params):
        """Queue a per-channel offset-current cal (runs inline on this thread)."""
        with self._offcal_lock:
            self._offcal = params

    def _take_offset_cal(self):
        with self._offcal_lock:
            o = self._offcal
            self._offcal = None
            return o

    def request_rescan(self):
        """Ask the poller to run a firmware RESCAN inline (on a quiet bus)."""
        self._rescan_req = True

    def _maybe_auto_rescan(self, why):
        """Self-heal: schedule a firmware RESCAN when a board that should be
        there stops answering (flaky HASL contact, mid-session drop). Rate-
        limited so a genuinely absent board doesn't turn every poll cycle
        into a multi-second re-detect."""
        now = time.time()
        if now - self._auto_rescan_last < 10.0:
            return
        self._auto_rescan_last = now
        self._rescan_req = True
        self.q.put(("info", f"auto-rescan: {why}"))

    def _run_rescan(self):
        """Firmware re-detect on a quiet bus: get the authoritative active mask
        from the RESCAN reply and rebuild active_boards from it (don't infer from
        MEASA?, which can't see a board the firmware hasn't detected)."""
        self._rescan_req = False
        mask = self.link.rescan()
        if mask is None:
            self.link.resync()             # desynced/timed out → clear the stream
            self.active_boards = None       # fall back to MEASA? discovery
            self.q.put(("error", "RESCAN: no reply (resynced)"))
            return
        self._expected_mask = mask          # baseline for dropout detection
        self.active_boards = [b for b in range(NUM_BOARDS) if mask & (1 << b)]
        # Show any board the firmware no longer reports as absent (clear stale data).
        for b in range(NUM_BOARDS):
            if not (mask & (1 << b)):
                lo = b * CH_PER_BOARD
                self._grid[lo:lo + CH_PER_BOARD] = [float("nan")] * CH_PER_BOARD
        self.q.put(("data", self._grid[:]))
        self.q.put(("rescan_done", mask))

    def _drain_commands(self):
        """Send any queued GUI commands (keeps all serial I/O on this thread)."""
        while True:
            try:
                cmd = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            reply = self.link.command(cmd)
            if reply and reply.startswith("ERR"):
                self.q.put(("error", f"{cmd} → {reply}"))

    def run(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._drain_commands()         # apply setpoints first
                if self._rescan_req:
                    self._run_rescan()         # inline, on a quiet bus
                sweep = self._take_sweep()
                if sweep is not None:
                    self._run_sweep(*sweep)    # inline, so no serial-bus contention
                char = self._take_characterize()
                if char is not None:
                    self._run_characterize(char)   # inline, single serial owner
                offcal = self._take_offset_cal()
                if offcal is not None:
                    self._run_offset_cal(offcal)   # inline, single serial owner
                self._poll_once()
            except Exception as e:  # serial dropout, etc.
                self.q.put(("error", str(e)))
                try:
                    self.link.resync()     # clear any half-sent reply
                except Exception:
                    pass
                self.active_boards = None  # re-discover after a dropout
                self._stop.wait(0.5)  # backoff so a dropout doesn't busy-spin
                continue
            # interval <= 0 means "as fast as MEASA? returns" (no idle gap);
            # otherwise sleep only the remainder of the requested period.
            if self.interval > 0:
                dt = self.interval - (time.time() - t0)
                if dt > 0:
                    self._stop.wait(dt)

    def _poll_once(self):
        """One refresh of the grid. Populated boards are scanned one at a time,
        with queued setpoints flushed *between* boards, so an ISET lands within
        ~one board scan (~90 ms) instead of waiting on the whole-grid MEASA?."""
        if not self.active_boards:
            # Discover populated boards from a single full scan (also the path
            # taken right after a dropout). A board is "active" if any of its
            # channels reports a real number rather than NaN.
            vals = self.link.measure_all()
            if vals is not None:
                self._grid = vals
                self.active_boards = sorted({
                    g // CH_PER_BOARD for g, v in enumerate(vals) if v == v
                })
                # A board the last RESCAN adopted but MEASA? can no longer see
                # (or that came back after a dropout) needs a firmware
                # re-detect, not just re-discovery — schedule one.
                expected = {b for b in range(NUM_BOARDS)
                            if self._expected_mask & (1 << b)}
                missing = expected - set(self.active_boards)
                if missing:
                    self._maybe_auto_rescan(
                        f"board(s) {sorted(missing)} missing from scan")
            self.q.put(("data", self._grid[:]))
            return

        for b in self.active_boards:
            if self._stop.is_set():
                break
            self._drain_commands()             # flush setpoints before each board
            vb = self.link.measure_mask(1 << b)
            lo = b * CH_PER_BOARD
            if vb:
                board_vals = vb[lo:lo + CH_PER_BOARD]
                self._grid[lo:lo + CH_PER_BOARD] = board_vals
                if all(v != v for v in board_vals):
                    # All-NaN from a board we asked for = the firmware lost it
                    # (contact glitch). RESCAN resyncs + reconfigures it.
                    self._maybe_auto_rescan(f"board {b} stopped reading")
            else:
                self._maybe_auto_rescan(f"board {b}: no valid MEASA? reply")
        self.q.put(("data", self._grid[:]))

    def _run_sweep(self, channel, imax, step, settle):
        """Step current 0..imax on `channel`, read raw_V via MEASA?, log CSV, fit."""
        currents = []
        i = 0.0
        while i <= imax + 1e-9:
            currents.append(round(i, 4))
            i += step
        rows = []
        for ma in currents:
            if self._stop.is_set():
                break
            self.link.command(f"ISET {channel} {ma:.6f}")
            self._stop.wait(settle)                     # settle (interruptible)
            vals = self.link.measure_all()              # same MEASA? path the GUI polls
            raw = vals[channel] if vals else float("nan")
            rows.append((ma, raw))
            self.q.put(("sweep_progress", (ma, raw)))
        self.link.command(f"ISET {channel} 0")          # leave the channel at 0 mA

        path = os.path.join(os.getcwd(),
                            f"sweep_ch{channel}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["I_cmd_mA", "raw_V", "raw_mV"])
                for ma, raw in rows:
                    w.writerow([f"{ma:.6f}", f"{raw:.9f}", f"{raw * 1e3:.4f}"])
        except Exception as e:
            self.q.put(("error", f"csv: {e}"))

        xs = [ma * 1e-3 for ma, raw in rows if raw == raw]   # amps
        ys = [raw for _, raw in rows if raw == raw]           # volts
        m, b, r2 = linfit(xs, ys) if len(xs) >= 2 else (float("nan"),) * 3
        self.q.put(("sweep_done", (path, m, b, r2)))

    def _run_characterize(self, p):
        """Step current i_start..i_stop on a channel, dwell, measure V, and derive
        the heater resistance R = V_heater / I_heater (I_heater = commanded minus
        the sense-divider draw). Logs a CSV and exports a PNG with the R points
        plus linear and cubic fits. `p` carries divider/offset captured on the GUI
        thread so this worker never touches Tk vars."""
        ch      = p["ch"]
        i0      = p["i_start"]
        i1      = p["i_stop"]
        steps   = p["steps"]
        dwell   = p["dwell"]
        divider = p["divider"]
        off_v   = p["off_v"]

        # Build the current points (inclusive, evenly spaced).
        if steps <= 1:
            currents = [i0]
        else:
            currents = [i0 + (i1 - i0) * k / (steps - 1) for k in range(steps)]

        rows = []   # (I_cmd_mA, raw_V, heater_V, I_heater_mA, R_ohm)
        aborted = None
        for idx, ma in enumerate(currents):
            if self._stop.is_set():
                break
            self.link.command(f"ISET {ch} {ma:.6f}")
            self._stop.wait(dwell)                       # dwell (interruptible)
            vals = self.link.measure_all()
            raw = vals[ch] if vals else float("nan")
            # Bail out early rather than logging a column of NaN: a NaN here means
            # the channel's board isn't being measured (absent / just dropped).
            if raw != raw and idx == 0:
                aborted = (f"ch{ch}: no reading (board {ch // CH_PER_BOARD} "
                           f"absent or dropped) — Rescan and retry")
                break
            heater_v = (raw + off_v) * divider
            i_div = (raw + off_v) / DIVIDER_BOTTOM_OHMS   # divider current (A)
            # true heater current: commanded + this channel's XTR offset − divider
            i_heater = (ma + p.get("ios_ma", 0.0)) * 1e-3 - i_div
            r = heater_v / i_heater if i_heater > 0 else float("nan")
            rows.append((ma, raw, heater_v, i_heater * 1e3, r))
            self.q.put(("char_progress", (ma, r)))
        self.link.command(f"ISET {ch} 0")                 # leave channel at 0 mA

        if aborted is not None:
            self.q.put(("char_done", (None, None, "", "", aborted)))
            return

        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = os.path.join(os.getcwd(), f"Rchar_ch{ch}_{stamp}")
        csv_path = base + ".csv"
        try:
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["I_cmd_mA", "raw_V", "heater_V", "I_heater_mA", "R_ohm"])
                for r in rows:
                    w.writerow([f"{r[0]:.6f}", f"{r[1]:.9f}", f"{r[2]:.6f}",
                                f"{r[3]:.6f}", f"{r[4]:.4f}"])
        except Exception as e:
            self.q.put(("error", f"csv: {e}"))

        # Fit R(I_heater): linear + cubic. x = true heater current (mA).
        xs = [r[3] for r in rows if r[4] == r[4]]
        ys = [r[4] for r in rows if r[4] == r[4]]
        lin = cub = None
        png_path = None
        if HAVE_PLOT and len(xs) >= 2:
            x = np.array(xs); y = np.array(ys)
            lin = np.polyfit(x, y, 1)
            cub = np.polyfit(x, y, min(3, len(xs) - 1))   # guard: need >degree pts
            png_path = base + ".png"
            try:
                self._export_plot(png_path, ch, x, y, lin, cub)
            except Exception as e:
                self.q.put(("error", f"plot: {e}"))
                png_path = None

        self.q.put(("char_done", (csv_path, png_path,
                                  _poly_str(lin), _poly_str(cub), None)))

    def _run_offset_cal(self, p):
        """Single-point per-channel offset-current cal (see module docstring).
        Drives every channel to `i_test` in one ISETA (all-on, so the shared
        ground-return drop matches operating conditions), averages a few full
        scans, zeroes everything, then solves each channel's offset current
        against the known load:
            I_heater = heater_V / R_known  and  I_heater = I_test + IOS − I_div
            ⇒  IOS = heater_V/R_known + heater_V/120k − I_test
        A channel whose implied |IOS| is absurd (> 100 µA, 10× the XTR200
        datasheet max) has no load fitted (compliance rail) or a wrong R_known
        — it is skipped and left at 0 rather than poisoning the table."""
        i_test  = p["i_test"]
        r_known = p["r_known"]
        settle  = p["settle"]
        divider = p["divider"]
        off_v   = p["off_v"]
        navg    = 3

        self.link.command("ISETA " + " ".join(f"{i_test:.6f}"
                                              for _ in range(TOTAL_CH)))
        self._stop.wait(settle)
        acc = [0.0] * TOTAL_CH
        cnt = [0] * TOTAL_CH
        for _ in range(navg):
            if self._stop.is_set():
                break
            vals = self.link.measure_all()
            if vals:
                for g, v in enumerate(vals):
                    if v == v:
                        acc[g] += v
                        cnt[g] += 1
        self.link.command("ISETA " + " ".join("0" for _ in range(TOTAL_CH)))

        ios_ma = [0.0] * TOTAL_CH
        used, skipped = [], []
        for g in range(TOTAL_CH):
            if not cnt[g]:
                continue                      # absent board → no entry
            raw = acc[g] / cnt[g]
            heater_v = (raw + off_v) * divider
            ios_a = (heater_v / r_known
                     + (raw + off_v) / DIVIDER_BOTTOM_OHMS
                     - i_test * 1e-3)
            if abs(ios_a) > 100e-6:
                skipped.append(g)
                continue
            ios_ma[g] = ios_a * 1e3
            used.append(g)

        path = os.path.join(os.getcwd(), OFFSET_TABLE_FILE)
        err = None
        try:
            with open(path, "w") as f:
                json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "i_test_mA": i_test, "r_known_ohm": r_known,
                           "divider": divider, "offset_mv": off_v * 1e3,
                           "ios_ma": ios_ma}, f, indent=1)
        except Exception as e:
            err = f"save: {e}"
        self.q.put(("offcal_done", (ios_ma, used, skipped, path, err)))

    @staticmethod
    def _export_plot(path, ch, x, y, lin, cub):
        xf = np.linspace(x.min(), x.max(), 200)
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot(x, y, "o", color="#1f77b4", label="measured R", zorder=3)
        ax.plot(xf, np.polyval(lin, xf), "-", color="#ff7f0e",
                label=f"linear (R²={_r2(x, y, lin):.4f})")
        ax.plot(xf, np.polyval(cub, xf), "--", color="#2ca02c",
                label=f"cubic (R²={_r2(x, y, cub):.4f})")
        ax.set_xlabel("heater current (mA)")
        ax.set_ylabel("resistance (Ω)")
        ax.set_title(f"Heater resistance — channel {ch}")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)

    def stop(self):
        self._stop.set()


def _poly_str(coeffs):
    """Pretty-print numpy polyfit coeffs (highest power first) as a string."""
    if coeffs is None:
        return ""
    deg = len(coeffs) - 1
    terms = []
    for k, c in enumerate(coeffs):
        power = deg - k
        if power == 0:
            terms.append(f"{c:+.5g}")
        elif power == 1:
            terms.append(f"{c:+.5g}·I")
        else:
            terms.append(f"{c:+.5g}·I^{power}")
    return " ".join(terms)


def _r2(x, y, coeffs):
    """Coefficient of determination for a numpy polyfit on (x, y)."""
    resid = y - np.polyval(coeffs, x)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def linfit(xs, ys):
    """Ordinary least squares y = m*x + b; returns (m, b, r2)."""
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return float("nan"), float("nan"), float("nan")
    m = (n * sxy - sx * sy) / denom
    b = (sy - m * sx) / n
    ybar = sy / n
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((y - (m * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return m, b, r2


# ──────────────────────────────────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self, link, divider, interval, offset_mv=DEFAULT_OFFSET_MV):
        super().__init__()
        self.link = link
        self.q = queue.Queue()
        self.cmd_q = queue.Queue()        # GUI → device commands (thread-safe)
        self.poller = None
        self.sweep_active = False
        self.char_active = False
        self.offcal_active = False
        self.ios_ma = [0.0] * TOTAL_CH    # per-channel XTR offset current (mA)
        self.divider = tk.DoubleVar(value=divider)
        self.offset_mv = tk.DoubleVar(value=offset_mv)   # ADC zero-offset (added back)
        self.interval = interval
        self.sweep_step = 0.25            # mA per sweep point
        self.sweep_settle = 0.4           # s settle per sweep point
        self._last_update_t = None
        self.setpoint_ma = [0.0] * TOTAL_CH

        self.title("Koi 8x8 — channel monitor")
        self.configure(padx=10, pady=10)
        self.geometry("1200x760")          # bounded so the grid scrolls

        self._build_controls()
        self._build_grid()
        self._load_offset_table()

        self.after(30, self._drain_queue)
        self.start_polling()

    # ---- layout -----------------------------------------------------------
    def _build_controls(self):
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(bar, text="Divider:").pack(side="left")
        e = ttk.Entry(bar, width=7, textvariable=self.divider)
        e.pack(side="left", padx=(2, 8))
        e.bind("<Return>", lambda _ev: self._recompute())

        # ADC zero-offset (mV) added back to raw_V before the divider.
        ttk.Label(bar, text="Offset+ (mV):").pack(side="left")
        eo = ttk.Entry(bar, width=7, textvariable=self.offset_mv)
        eo.pack(side="left", padx=(2, 12))
        eo.bind("<Return>", lambda _ev: self._recompute())

        # Set-all-channels control.
        ttk.Label(bar, text="Set all (mA):").pack(side="left")
        self.setall_var = tk.StringVar(value="0")
        sa = ttk.Entry(bar, width=7, textvariable=self.setall_var)
        sa.pack(side="left", padx=(2, 4))
        sa.bind("<Return>", lambda _ev: self.set_all())
        ttk.Button(bar, text="Apply all", command=self.set_all).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Zero all", command=self.zero_all).pack(side="left", padx=(0, 12))

        self.status = ttk.Label(bar, text="connecting…")
        self.status.pack(side="left")

        ttk.Button(bar, text="Pause", command=self.toggle).pack(side="right")
        ttk.Button(bar, text="Rescan", command=self.rescan).pack(side="right", padx=(0, 6))

        # Second row: calibration sweep (logs raw_V vs I to CSV, fits gain/offset).
        bar2 = ttk.Frame(self)
        bar2.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar2, text="Sweep ch:").pack(side="left")
        self.sweep_ch = tk.StringVar(value="0")
        ttk.Entry(bar2, width=4, textvariable=self.sweep_ch).pack(side="left", padx=(2, 4))
        ttk.Label(bar2, text="→ max (mA):").pack(side="left")
        self.sweep_imax = tk.StringVar(value="3")
        ttk.Entry(bar2, width=5, textvariable=self.sweep_imax).pack(side="left", padx=(2, 4))
        ttk.Label(bar2, text="Rcal (Ω):").pack(side="left")
        self.sweep_rload = tk.StringVar(value="")
        ttk.Entry(bar2, width=8, textvariable=self.sweep_rload).pack(side="left", padx=(2, 4))
        ttk.Button(bar2, text="Sweep + cal", command=self.start_sweep).pack(side="left", padx=(0, 8))
        ttk.Label(bar2, text="(fits offset; with Rcal also fits divider)",
                  foreground="#777").pack(side="left")

        # Third row: resistance characterization (R vs current, linear+cubic fit,
        # exports CSV + PNG graph).
        bar3 = ttk.Frame(self)
        bar3.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar3, text="Characterize R — ch:").pack(side="left")
        self.char_ch = tk.StringVar(value="0")
        ttk.Entry(bar3, width=4, textvariable=self.char_ch).pack(side="left", padx=(2, 6))
        ttk.Label(bar3, text="start (mA):").pack(side="left")
        self.char_start = tk.StringVar(value="0.5")
        ttk.Entry(bar3, width=5, textvariable=self.char_start).pack(side="left", padx=(2, 6))
        ttk.Label(bar3, text="stop (mA):").pack(side="left")
        self.char_stop = tk.StringVar(value="5")
        ttk.Entry(bar3, width=5, textvariable=self.char_stop).pack(side="left", padx=(2, 6))
        ttk.Label(bar3, text="steps:").pack(side="left")
        self.char_steps = tk.StringVar(value="20")
        ttk.Entry(bar3, width=5, textvariable=self.char_steps).pack(side="left", padx=(2, 6))
        ttk.Label(bar3, text="dwell (s):").pack(side="left")
        self.char_dwell = tk.StringVar(value="0.5")
        ttk.Entry(bar3, width=5, textvariable=self.char_dwell).pack(side="left", padx=(2, 6))
        ttk.Button(bar3, text="Characterize R",
                   command=self.start_characterize).pack(side="left", padx=(0, 8))
        note = "exports CSV + PNG (linear+cubic fit)" if HAVE_PLOT \
            else "graph needs: pip install matplotlib"
        ttk.Label(bar3, text=f"({note})", foreground="#777").pack(side="left")

        # Fourth row: per-channel offset-current table. Single-point cal: drive
        # every populated channel to I_test against a KNOWN load resistance and
        # solve each channel's XTR200 offset current (see module docstring).
        bar4 = ttk.Frame(self)
        bar4.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar4, text="Offset table — I test (mA):").pack(side="left")
        self.offcal_i = tk.StringVar(value="1.0")
        ttk.Entry(bar4, width=5, textvariable=self.offcal_i).pack(side="left", padx=(2, 6))
        ttk.Label(bar4, text="R known (Ω):").pack(side="left")
        self.offcal_r = tk.StringVar(value="")
        ttk.Entry(bar4, width=8, textvariable=self.offcal_r).pack(side="left", padx=(2, 6))
        ttk.Label(bar4, text="settle (s):").pack(side="left")
        self.offcal_settle = tk.StringVar(value="1.0")
        ttk.Entry(bar4, width=5, textvariable=self.offcal_settle).pack(side="left", padx=(2, 6))
        ttk.Button(bar4, text="Cal offsets",
                   command=self.start_offset_cal).pack(side="left", padx=(0, 4))
        ttk.Button(bar4, text="Clear",
                   command=self.clear_offsets).pack(side="left", padx=(0, 8))
        self.offtab_lbl = ttk.Label(bar4, text="offsets: none", foreground="#777")
        self.offtab_lbl.pack(side="left")

    def _build_grid(self):
        # The 8×8 grid is taller/wider than most screens, so host it in a
        # scrollable canvas (vertical + horizontal) instead of packing it raw.
        container = ttk.Frame(self)
        container.grid(row=4, column=0, sticky="nsew")
        self.rowconfigure(4, weight=1)
        self.columnconfigure(0, weight=1)

        canvas = tk.Canvas(container, highlightthickness=0)
        vbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        grid = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>",
                  lambda _ev: canvas.configure(scrollregion=canvas.bbox("all")))

        # Mouse-wheel scrolling (Linux sends Button-4/5; others <MouseWheel>).
        def _wheel(ev):
            if getattr(ev, "num", 0) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(ev, "num", 0) == 5:
                canvas.yview_scroll(1, "units")
            else:
                canvas.yview_scroll(-1 if ev.delta > 0 else 1, "units")
        canvas.bind_all("<MouseWheel>", _wheel)
        canvas.bind_all("<Button-4>", _wheel)
        canvas.bind_all("<Button-5>", _wheel)

        # Column headers (channel 0..7)
        ttk.Label(grid, text="brd\\ch", width=8, anchor="center").grid(row=0, column=0)
        for c in range(CH_PER_BOARD):
            ttk.Label(grid, text=f"ch{c}", width=14, anchor="center").grid(row=0, column=c + 1)

        self.cells = {}  # g -> dict(raw, heat, frame, set, entry)
        for b in range(NUM_BOARDS):
            ttk.Label(grid, text=f"brd {b}", width=8, anchor="center").grid(row=b + 1, column=0)
            for c in range(CH_PER_BOARD):
                g = b * CH_PER_BOARD + c
                f = tk.Frame(grid, bd=1, relief="solid", padx=4, pady=2)
                f.grid(row=b + 1, column=c + 1, padx=1, pady=1, sticky="nsew")

                # Per-channel current setpoint (mA). Enter sends ISET g <mA>.
                set_var = tk.StringVar(value="0")
                entry = tk.Entry(f, textvariable=set_var, width=7,
                                 justify="center", font=("TkFixedFont", 9))
                entry.pack(fill="x")
                entry.bind("<Return>", lambda _ev, gg=g: self.set_channel(gg))

                raw_var = tk.StringVar(value="—")
                heat_var = tk.StringVar(value="—")
                cur_var = tk.StringVar(value="—")
                res_var = tk.StringVar(value="—")
                pwr_var = tk.StringVar(value="—")
                raw_lbl = tk.Label(f, textvariable=raw_var, font=("TkFixedFont", 9),
                                   fg="#555")
                raw_lbl.pack()
                heat_lbl = tk.Label(f, textvariable=heat_var,
                                    font=("TkFixedFont", 11, "bold"))
                heat_lbl.pack()
                cur_lbl = tk.Label(f, textvariable=cur_var, font=("TkFixedFont", 9),
                                   fg="#06c")
                cur_lbl.pack()
                res_lbl = tk.Label(f, textvariable=res_var, font=("TkFixedFont", 9),
                                   fg="#093")
                res_lbl.pack()
                pwr_lbl = tk.Label(f, textvariable=pwr_var, font=("TkFixedFont", 9),
                                   fg="#a50")
                pwr_lbl.pack()
                self.cells[g] = dict(raw=raw_var, heat=heat_var, cur=cur_var,
                                     res=res_var, pwr=pwr_var,
                                     frame=f, set=set_var, entry=entry,
                                     raw_lbl=raw_lbl, heat_lbl=heat_lbl,
                                     cur_lbl=cur_lbl, res_lbl=res_lbl,
                                     pwr_lbl=pwr_lbl)
        self._last = [float("nan")] * TOTAL_CH

    # ---- polling control --------------------------------------------------
    def start_polling(self):
        if self.poller is None:
            self.poller = Poller(self.link, self.q, self.cmd_q, self.interval)
            self.poller.start()

    # ---- sending currents -------------------------------------------------
    def _parse_ma(self, text):
        """Parse a current entry; clamp to [0, MAX] and warn if clamped."""
        try:
            ma = float(text)
        except ValueError:
            self.status.config(text=f"bad current: {text!r}")
            return None
        if ma < 0:
            ma = 0.0
        if ma > MAX_CURRENT_MA:
            self.status.config(text=f"clamped to {MAX_CURRENT_MA:.3f} mA max")
            ma = MAX_CURRENT_MA
        return ma

    def set_channel(self, g):
        ma = self._parse_ma(self.cells[g]["set"].get())
        if ma is None:
            return
        self.setpoint_ma[g] = ma
        self.cells[g]["set"].set(f"{ma:g}")
        self.cmd_q.put(f"ISET {g} {ma:.6f}")
        self.status.config(text=f"ch{g}: {ma:.4f} mA  (Vdac={current_to_vdac(ma):.4f} V)")

    def set_all(self):
        ma = self._parse_ma(self.setall_var.get())
        if ma is None:
            return
        self.setpoint_ma = [ma] * TOTAL_CH
        for g in range(TOTAL_CH):
            self.cells[g]["set"].set(f"{ma:g}")
        # One ISETA frame sets all 64 at once.
        self.cmd_q.put("ISETA " + " ".join(f"{ma:.6f}" for _ in range(TOTAL_CH)))
        self.status.config(text=f"all channels: {ma:.4f} mA")

    def zero_all(self):
        self.setall_var.set("0")
        self.set_all()

    def toggle(self):
        if self.poller:
            self.poller.stop()
            self.poller = None
            self.status.config(text="paused")
        else:
            self.start_polling()
            self.status.config(text="running")

    def rescan(self):
        """Ask the firmware to re-detect boards (recovers a board whose pads were
        cleaned, or that dropped mid-session). Runs inline on the poller thread on
        a quiet bus — no need to Pause first — and adopts the firmware's reported
        active-board mask directly."""
        if self.poller is None:                  # nothing draining the queue while paused
            self.start_polling()
        self.poller.request_rescan()
        self.status.config(text="rescanning boards…")

    # ---- updates ----------------------------------------------------------
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "data" and payload is not None:
                    self._last = payload
                    self._render(payload)
                    now = time.time()
                    if self._last_update_t is not None:
                        hz = 1.0 / max(now - self._last_update_t, 1e-6)
                        self.status.config(text=f"{hz:4.1f} updates/s")
                    self._last_update_t = now
                elif kind == "sweep_progress":
                    ma, raw = payload
                    self.status.config(text=f"sweep: {ma:.3f} mA → {raw * 1e3:8.3f} mV")
                elif kind == "sweep_done":
                    self._on_sweep_done(payload)
                elif kind == "char_progress":
                    ma, r = payload
                    rtxt = f"{r:8.2f} Ω" if r == r else "  —"
                    self.status.config(text=f"Rchar: {ma:.3f} mA → {rtxt}")
                elif kind == "char_done":
                    self._on_char_done(payload)
                elif kind == "offcal_done":
                    self._on_offcal_done(payload)
                elif kind == "rescan_done":
                    boards = [b for b in range(NUM_BOARDS) if payload & (1 << b)]
                    self.status.config(
                        text=f"rescan: active boards {boards} (0x{payload:02X})")
                elif kind == "info":
                    self.status.config(text=str(payload))
                elif kind == "error":
                    self.status.config(text=f"serial error: {payload}")
        except queue.Empty:
            pass
        self.after(30, self._drain_queue)

    # ---- calibration sweep ------------------------------------------------
    def start_sweep(self):
        if self.sweep_active:
            self.status.config(text="sweep already running")
            return
        try:
            ch = int(self.sweep_ch.get())
            imax = float(self.sweep_imax.get())
        except ValueError:
            self.status.config(text="bad sweep ch/max")
            return
        if not (0 <= ch < TOTAL_CH):
            self.status.config(text="sweep ch must be 0..63")
            return
        imax = min(imax, MAX_CURRENT_MA)
        if self.poller is None:                      # need the poller thread to run it
            self.start_polling()
        # The poller runs the sweep inline (single serial owner — no bus race).
        self.sweep_active = True
        self.poller.request_sweep(ch, imax, self.sweep_step, self.sweep_settle)
        self.status.config(text=f"sweeping ch{ch} 0..{imax:g} mA …")

    # ---- resistance characterization --------------------------------------
    def start_characterize(self):
        if self.char_active:
            self.status.config(text="characterization already running")
            return
        try:
            ch    = int(self.char_ch.get())
            i0    = float(self.char_start.get())
            i1    = float(self.char_stop.get())
            steps = int(self.char_steps.get())
            dwell = float(self.char_dwell.get())
        except ValueError:
            self.status.config(text="bad characterize inputs")
            return
        if not (0 <= ch < TOTAL_CH):
            self.status.config(text="characterize ch must be 0..63")
            return
        if steps < 2:
            self.status.config(text="steps must be ≥ 2")
            return
        if dwell < 0:
            self.status.config(text="dwell must be ≥ 0")
            return
        i0 = max(0.0, min(i0, MAX_CURRENT_MA))
        i1 = max(0.0, min(i1, MAX_CURRENT_MA))
        # Guard against characterizing an unpopulated channel (the #1 cause of an
        # all-NaN log). If we know the active boards and this channel's board is
        # not among them, refuse and tell the user to Rescan / pick another.
        board = ch // CH_PER_BOARD
        active = self.poller.active_boards if self.poller else None
        if active is not None and board not in active:
            self.status.config(
                text=f"ch{ch}: board {board} not detected — Rescan or pick a populated channel")
            return
        # Capture divider/offset HERE (GUI thread) so the worker never reads Tk vars.
        try:
            divider = float(self.divider.get())
        except (tk.TclError, ValueError):
            divider = DEFAULT_DIVIDER
        try:
            off_v = float(self.offset_mv.get()) * 1e-3
        except (tk.TclError, ValueError):
            off_v = 0.0
        if self.poller is None:
            self.start_polling()
        self.char_active = True
        self.poller.request_characterize(dict(
            ch=ch, i_start=i0, i_stop=i1, steps=steps, dwell=dwell,
            divider=divider, off_v=off_v, ios_ma=self.ios_ma[ch]))
        self.status.config(
            text=f"characterizing ch{ch} {i0:g}→{i1:g} mA, {steps} steps …")

    def _on_char_done(self, payload):
        csv_path, png_path, lin_s, cub_s, err = payload
        self.char_active = False
        if err:
            self.status.config(text="Rchar: " + err)
            return
        msg = [f"saved {os.path.basename(csv_path)}"]
        if png_path:
            msg.append(os.path.basename(png_path))
        if lin_s:
            msg.append(f"linear: R={lin_s}")
        if cub_s:
            msg.append(f"cubic: R={cub_s}")
        elif not HAVE_PLOT:
            msg.append("(install matplotlib for graph+cubic)")
        self.status.config(text="Rchar: " + "  ".join(msg))

    # ---- per-channel offset-current table --------------------------------
    def start_offset_cal(self):
        if self.offcal_active:
            self.status.config(text="offset cal already running")
            return
        try:
            i_test  = float(self.offcal_i.get())
            r_known = float(self.offcal_r.get())
            settle  = float(self.offcal_settle.get())
        except ValueError:
            self.status.config(text="offset cal needs I test + R known (Ω)")
            return
        if not (0 < i_test <= MAX_CURRENT_MA):
            self.status.config(text=f"I test must be 0..{MAX_CURRENT_MA:.2f} mA")
            return
        if r_known <= 0:
            self.status.config(text="R known must be > 0 Ω")
            return
        settle = max(0.0, settle)
        try:
            divider = float(self.divider.get())
        except (tk.TclError, ValueError):
            divider = DEFAULT_DIVIDER
        try:
            off_v = float(self.offset_mv.get()) * 1e-3
        except (tk.TclError, ValueError):
            off_v = 0.0
        if self.poller is None:
            self.start_polling()
        self.offcal_active = True
        self.poller.request_offset_cal(dict(i_test=i_test, r_known=r_known,
                                            settle=settle, divider=divider,
                                            off_v=off_v))
        self.status.config(
            text=f"offset cal: all channels @ {i_test:g} mA vs {r_known:g} Ω …")

    def _on_offcal_done(self, payload):
        ios_ma, used, skipped, path, err = payload
        self.offcal_active = False
        self.ios_ma = ios_ma
        if used:
            us = [ios_ma[g] * 1e3 for g in used]          # mA → µA
            self.offtab_lbl.config(
                text=f"offsets: {len(used)} ch, {min(us):+.1f}..{max(us):+.1f} µA")
        else:
            self.offtab_lbl.config(text="offsets: none calibrated")
        msg = f"offset cal: {len(used)} ch"
        if skipped:
            msg += f", skipped {skipped} (railed/no load — check R known)"
        msg += f"  ({err})" if err else f"  → {os.path.basename(path)}"
        self.status.config(text=msg)
        self._recompute()

    def clear_offsets(self):
        """Zero the in-memory table and remove the persisted file (else the
        cleared table would silently come back on the next start)."""
        self.ios_ma = [0.0] * TOTAL_CH
        note = ""
        try:
            os.remove(os.path.join(os.getcwd(), OFFSET_TABLE_FILE))
            note = f" ({OFFSET_TABLE_FILE} deleted)"
        except OSError:
            pass
        self.offtab_lbl.config(text="offsets: none")
        self.status.config(text="offset table cleared" + note)
        self._recompute()

    def _load_offset_table(self):
        """Adopt a previously saved table, if any (silent when absent)."""
        try:
            with open(os.path.join(os.getcwd(), OFFSET_TABLE_FILE)) as f:
                d = json.load(f)
            tab = [float(x) for x in d["ios_ma"]]
            if len(tab) != TOTAL_CH:
                raise ValueError(f"expected {TOTAL_CH} entries, got {len(tab)}")
        except FileNotFoundError:
            return
        except Exception as e:
            self.offtab_lbl.config(text=f"offsets: load failed ({e})")
            return
        self.ios_ma = tab
        nz = [v * 1e3 for v in tab if v != 0.0]
        if nz:
            self.offtab_lbl.config(
                text=f"offsets: {len(nz)} ch loaded ({d.get('created', '?')}), "
                     f"{min(nz):+.1f}..{max(nz):+.1f} µA")
        else:
            self.offtab_lbl.config(text="offsets: saved table is empty")

    def _on_sweep_done(self, payload):
        path, m, b, r2 = payload
        self.sweep_active = False
        msg = [f"saved {os.path.basename(path)}"]
        if m == m:                                   # valid fit
            self.offset_mv.set(f"{-b * 1e3:.4f}")    # add back the fitted zero
            msg.append(f"offset={-b*1e3:+.3f} mV")
            msg.append(f"slope={m:.3f} V/A")
            try:
                rload = float(self.sweep_rload.get())
            except (tk.TclError, ValueError):
                rload = 0.0
            if rload > 0 and m != 0:
                self.divider.set(f"{rload / m:.4f}")  # divider = Rcal / slope
                msg.append(f"divider={rload/m:.4f}")
            msg.append(f"r²={r2:.5f}")
        self.status.config(text="cal: " + "  ".join(msg))
        self._recompute()

    def _recompute(self):
        """Re-render heater column when the divider is edited live."""
        self._render(self._last)

    def _render(self, vals):
        try:
            div = float(self.divider.get())
        except (tk.TclError, ValueError):
            div = DEFAULT_DIVIDER
        try:
            off_v = float(self.offset_mv.get()) * 1e-3   # mV → V, added back to raw
        except (tk.TclError, ValueError):
            off_v = 0.0
        all_lbls = ("raw_lbl", "heat_lbl", "cur_lbl", "res_lbl", "pwr_lbl")
        for g, raw in enumerate(vals):
            c = self.cells[g]
            if raw != raw:  # NaN → unpopulated channel
                c["raw"].set("—")
                c["heat"].set("absent")
                for k in ("cur", "res", "pwr"):
                    c[k].set("—")
                for lbl in all_lbls:
                    c[lbl].config(bg="#eee", fg="#999")
                c["frame"].config(bg="#eee")
                c["entry"].config(state="disabled")
            else:
                heater_v = (raw + off_v) * div     # offset-corrected, then divider
                # The divider steals (raw+offset)/20k from the source; the heater
                # gets the rest. Source current = commanded + this channel's XTR
                # offset current (per-channel table from "Cal offsets").
                i_div = (raw + off_v) / DIVIDER_BOTTOM_OHMS
                i_a = (self.setpoint_ma[g] + self.ios_ma[g]) * 1e-3 - i_div
                c["raw"].set(f"{raw * 1e3:8.3f} mV")
                c["heat"].set(f"{heater_v:8.4f} V")
                c["cur"].set(f"I {i_a * 1e3:7.4f} mA")
                # The XTR200 sources I, the ADC measures V → solve for the unknown load.
                # With zero current there's no defined resistance, only leakage voltage.
                if i_a > 0:
                    c["res"].set(f"R {heater_v / i_a:8.2f} Ω")
                    c["pwr"].set(f"P {heater_v * i_a * 1e3:7.3f} mW")
                else:
                    c["res"].set("R —")
                    c["pwr"].set("P 0 mW")
                c["frame"].config(bg="white")
                for lbl in all_lbls:
                    c[lbl].config(bg="white")
                c["raw_lbl"].config(fg="#555")
                c["heat_lbl"].config(fg="black")
                c["cur_lbl"].config(fg="#06c")
                c["res_lbl"].config(fg="#093")
                c["pwr_lbl"].config(fg="#a50")
                if str(c["entry"].cget("state")) == "disabled":
                    c["entry"].config(state="normal")

    def destroy(self):
        if self.poller:
            self.poller.stop()
        self.link.close()
        super().destroy()


def main():
    ap = argparse.ArgumentParser(description="Koi 8x8 channel monitor")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--divider", type=float, default=DEFAULT_DIVIDER,
                    help="input divider ratio (heater_V = (raw_V + offset) x divider)")
    ap.add_argument("--offset", type=float, default=DEFAULT_OFFSET_MV,
                    help="ADC zero-offset in mV, added back to raw_V before the divider")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="seconds between MEASA? polls (0 = as fast as it returns)")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    if not port:
        raise SystemExit("No serial port found. Pass --port /dev/ttyACMx")

    print(f"Connecting to {port} @ {args.baud} …")
    link = KoiLink(port, args.baud)
    app = App(link, args.divider, args.interval, args.offset)
    app.mainloop()


if __name__ == "__main__":
    main()
