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

Error flags: each poll cycle also snapshots the 64 XTR200 ERRORFLAG pins
(`ERR?`, via the daisy-chained SN74LV165s). A faulting channel — compliance
rail, open load at current — turns red with an "EF!" tag, and the control bar
lists all faulted channels. Flags of unpopulated boards are ignored (a missing
board breaks the 165 chain, so those bits are garbage). Old firmware without
ERR? just shows "EF: n/a".

DAC-reference watchdog: a populated channel commanded to 0 mA but reading
> 1 V raw means that board's DAC browned out back to its internal 2.5 V
reference (write-only bus — the firmware can't detect it). The GUI auto-sends
`DACINIT <b>` (soft reset, external ref, gain, setpoint reload), rate-limited
and capped at 3 tries per episode; the "DAC init" button does all boards
manually.

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
from tkinter import messagebox, ttk

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

# Optional: the Bench row (paper validation runs against a Keithley 2100) needs
# both the bench routines and the USBTMC driver. Without them the row is built
# but disabled, rather than the whole GUI failing to start on a machine with no
# meter attached.
try:
    import koi_bench
    from keithley2100 import Keithley2100, autodetect_usbtmc
    koi_bench.Keithley2100 = Keithley2100
    koi_bench.autodetect_dmm = autodetect_usbtmc
    HAVE_BENCH = True
except ImportError:
    koi_bench = None
    HAVE_BENCH = False

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

# Per-channel MEASURE-side voltage offset (raw ADC volts), built by "Capture
# zeros" and auto-loaded at startup. The AD7193's internal zero/full-scale
# calibration runs with channel 0 (AIN1) selected, so its shared offset register
# nulls channel 0; channels 1..7 carry their own small ADC mux-path offset
# (bench: ~0.2..1.4 mV = a few mV heater-referred after the divider). This table
# captures each channel's 0 mA baseline and subtracts it in the display so every
# channel reads ~0 at zero drive. Complementary to the current-side offset table.
VZERO_TABLE_FILE = "vzero_table.json"
# A channel reading above this at zero drive wasn't actually at zero (left
# driven / faulted) → skip it, don't poison the table with a real signal.
VZERO_MAX_V = 0.010

# XTR200 ERRORFLAG polarity as seen by the SN74LV165 chain (`ERR?` reports the
# raw pin level): EF is open-drain with an on-board pullup, so 1 = OK and
# 0 = fault. Flip this if a board revision buffers/inverts the flag.
ERRFLAG_ACTIVE_LOW = True

# DAC-lost-its-reference signature (bench-observed): a populated channel
# commanded to 0 mA reads way above zero — the board's DAC browned out back to
# its internal 2.5 V reference (write-only bus, so the firmware can't see it).
# A legit 0 mA reading is only the µA-scale IOS × load ≈ single mV, so >1 V raw
# is unambiguous. The GUI auto-sends `DACINIT <b>` to rewrite the config.
DAC_FAULT_RAW_V = 1.0
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

    def error_flags(self):
        """Snapshot all 64 XTR200 ERRORFLAG pins (`ERR?`, via the SN74LV165
        chain). Returns the raw 64-bit level word (bit g = channel g's EF pin
        level; the CALLER applies polarity and masks unpopulated boards —
        a missing board breaks the chain, so its upstream bits are garbage).
        None if the firmware didn't answer; False if it doesn't know the
        command (pre-errorflag firmware)."""
        line = self.command("ERR?")
        if line is None:
            return None
        if line.startswith("ERR"):
            return False
        try:
            return int(line.split()[-1], 16)
        except ValueError:
            return None

    def adc_settings(self):
        """Query the live AD7193 sampling settings (`ADC?`). Returns a dict
        {rate, avg, gain, chop, filter, rej60} parsed from the firmware's
        'OK ADC rate=.. avg=.. gain=.. chop=.. filter=.. rej60=..' reply, or
        None if the firmware didn't answer / doesn't know the command (older
        build) so the GUI can leave its controls at their defaults."""
        line = self.command("ADC?")
        if not line or line.startswith("ERR"):
            return None
        out = {}
        for tok in line.split():
            if "=" in tok:
                k, _, v = tok.partition("=")
                out[k] = v
        if "rate" not in out:
            return None
        return out

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
        self._vzero = None          # pending measure-side zero-offset capture
        self._vzero_lock = threading.Lock()
        self._bench = None          # pending bench measurement (key, params)
        self._bench_lock = threading.Lock()
        self._bench_abort = threading.Event()
        self.dmm = None             # Keithley2100, opened on this thread only
                                    # (USBTMC node has a single owner)
        self._rescan_req = True     # start with a firmware re-detect: recovers
                                    # boards the boot-time scan missed (flaky
                                    # HASL contact) without a manual click
        self.active_boards = None   # discovered once from a full scan
        self._grid = [float("nan")] * TOTAL_CH   # persistent last-known grid
        self._expected_mask = 0     # boards adopted from the last good RESCAN
        self._auto_rescan_last = 0.0   # rate-limit for automatic re-detects
        self._err_supported = True  # cleared if the firmware lacks ERR?
        self._adc_synced = False    # ADC? settings pulled into the GUI once

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

    def request_vzero_cal(self, params):
        """Queue a per-channel measure-side zero-offset capture (runs inline)."""
        with self._vzero_lock:
            self._vzero = params

    def _take_vzero_cal(self):
        with self._vzero_lock:
            v = self._vzero
            self._vzero = None
            return v

    def request_bench(self, key, params):
        """Queue a bench measurement (runs inline on this thread, so the Koi
        serial port and the DMM both keep a single owner)."""
        self._bench_abort.clear()
        with self._bench_lock:
            self._bench = (key, params)

    def _take_bench(self):
        with self._bench_lock:
            b = self._bench
            self._bench = None
            return b

    def abort_bench(self):
        self._bench_abort.set()

    def _run_bench(self, key, params):
        """Run one koi_bench routine, with the grid poll suspended for the
        duration: the poll's MEASA? traffic and the DAC-brownout watchdog would
        both fight a measurement that is deliberately holding a setpoint."""
        if koi_bench is None:
            self.q.put(("bench_done", (key, None, "koi_bench.py not importable")))
            return
        try:
            if self.dmm is None:
                node = koi_bench.autodetect_dmm()
                if node is None:
                    raise RuntimeError("no /dev/usbtmc* — is the 2100 connected? "
                                       "(see tools/setup_usbtmc.md)")
                self.dmm = koi_bench.Keithley2100(node)
                self.q.put(("info", f"DMM: {self.dmm.idn()}"))

            fn = dict((k, f) for k, _, f, _ in koi_bench.MEASUREMENTS)[key]
            ctx = koi_bench.BenchCtx(
                link=self.link, dmm=self.dmm,
                channel=params["ch"], r_load=params["r_load"],
                r_source=params.get("r_source", "typed"),
                outdir=params["outdir"], settle=params["settle"],
                xtr_mask=params.get("xtr_mask", 0x01),
                emit=lambda s: self.q.put(("bench_progress", s)),
                stopped=self._bench_abort.is_set)
            out = fn(ctx, **params.get("kwargs", {}))
            path, summary = out[0], out[1]
            extra = out[2] if len(out) > 2 else None
            self.q.put(("bench_done", (key, path, summary, extra)))
        except Exception as e:
            # Never leave the channel driven because a measurement raised.
            try:
                self.link.command(f"ISET {params['ch']} 0")
            except Exception:
                pass
            self.q.put(("bench_done", (key, None, f"{type(e).__name__}: {e}")))

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
        # First good connect: mirror the device's live sampling settings into the
        # GUI controls (so they show the real state, not just the defaults).
        if not self._adc_synced:
            settings = self.link.adc_settings()
            self._adc_synced = True        # don't clobber later user edits on reconnect
            if settings:
                self.q.put(("adc_settings", settings))

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
                vzero = self._take_vzero_cal()
                if vzero is not None:
                    self._run_vzero_cal(vzero)     # inline, single serial owner
                bench = self._take_bench()
                if bench is not None:
                    self._run_bench(*bench)        # inline; poll stays suspended
                    continue                       # skip this cycle's poll
                self._poll_once()
                self._poll_errflags()
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

    def _poll_errflags(self):
        """Piggyback one ERR? per poll cycle (a single fast round-trip — the
        165 chain is bit-banged, no ADC involvement). Old firmware answers
        'ERR unknown command'; disable further queries instead of resending a
        dead command every cycle."""
        if not self._err_supported:
            return
        flags = self.link.error_flags()
        if flags is False:
            self._err_supported = False
            self.q.put(("info", "firmware has no ERR? — reflash for error flags"))
        elif flags is not None:
            self.q.put(("errflags", flags))

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

    def _run_vzero_cal(self, p):
        """Capture each channel's zero-current ADC baseline as a per-channel
        measure-side voltage offset. Disables all drive first (`*RST` → currents
        0 AND front-ends off) so the reading is the pure ADC/divider offset — the
        XTR200 IOS is a current-side term handled by the offset-current table, so
        it must NOT be folded in here. Averages a few full scans and stores raw
        volts per channel, THEN re-enables the front-ends (else the next drive
        command produces no output). A channel still above VZERO_MAX_V wasn't
        actually at zero (left driven / faulted) → skipped, left at 0 rather than
        poisoning the table with a real signal."""
        settle = p["settle"]
        navg   = 5

        self.link.command("*RST")               # currents 0 + front-ends off
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

        # *RST disabled every front-end; re-enable the populated boards so the
        # board returns to its normal operating state (drive-ready, currents 0).
        mask = 0
        for b in (self.active_boards if self.active_boards else range(NUM_BOARDS)):
            mask |= (1 << b)
        self.link.command(f"XTR 0x{mask:02X}")

        vzero_v = [0.0] * TOTAL_CH
        used, skipped = [], []
        for g in range(TOTAL_CH):
            if not cnt[g]:
                continue                          # absent board → no entry
            raw = acc[g] / cnt[g]
            if abs(raw) > VZERO_MAX_V:
                skipped.append(g)
                continue
            vzero_v[g] = raw
            used.append(g)

        path = os.path.join(os.getcwd(), VZERO_TABLE_FILE)
        err = None
        try:
            with open(path, "w") as f:
                json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "vzero_v": vzero_v}, f, indent=1)
        except Exception as e:
            err = f"save: {e}"
        self.q.put(("vzero_done", (vzero_v, used, skipped, path, err)))

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


class _Tooltip:
    """Hover text for the bench buttons — the row is dense and each button
    starts a multi-minute run, so what one does needs to be legible first."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._win = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _ev=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self._win:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._win = tk.Toplevel(self.widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(self._win, text=self.text, justify="left",
                 background="#ffffe0", relief="solid", borderwidth=1,
                 wraplength=420, padx=6, pady=3).pack()

    def _hide(self, _ev=None):
        self._cancel()
        if self._win:
            self._win.destroy()
            self._win = None


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
        self.vzero_active = False
        self.ios_ma = [0.0] * TOTAL_CH    # per-channel XTR offset current (mA)
        self.vzero_v = [0.0] * TOTAL_CH   # per-channel measure-side V offset (raw V)
        self.divider = tk.DoubleVar(value=divider)
        self.offset_mv = tk.DoubleVar(value=offset_mv)   # ADC zero-offset (added back)
        self.interval = interval
        self.sweep_step = 0.25            # mA per sweep point
        self.sweep_settle = 0.4           # s settle per sweep point
        self._last_update_t = None
        self.setpoint_ma = [0.0] * TOTAL_CH
        self.errflags = None              # raw 64-bit ERR? word (None = unknown)
        self._dacinit_state = {}          # board → (last_send_t, strikes)
        self.xtr_enabled = True           # front-ends on/off (XTR-off diagnostic)
        self.bench_active = None          # key of the running bench measurement

        # Bench row (paper validation against the Keithley 2100). R load starts
        # blank on purpose: it must come from the 4-wire button, not a typed
        # constant — a stale hand-entered value is what invalidated the first
        # characterization campaign.
        self.bench_ch = tk.StringVar(value="0")
        self.bench_r = tk.StringVar(value="")
        self.bench_settle = tk.StringVar(value="0.8")
        self.bench_outdir = tk.StringVar(
            value=os.path.join("bench", time.strftime("%Y%m%d")))
        self.bench_r_source = "typed"

        # AD7193 sampling settings (mirror the firmware defaults; refreshed from
        # the device's ADC? reply once connected). Changing a control sends the
        # matching command — RATE/AVG/FILTER/REJ60 are cheap; GAIN/CHOP trigger a
        # per-board recalibration in firmware (a few seconds).
        self.adc_rate = tk.StringVar(value="16")
        self.adc_avg = tk.StringVar(value="4")
        self.adc_gain = tk.StringVar(value="1")
        self.adc_filter = tk.StringVar(value="SINC4")
        self.adc_chop = tk.BooleanVar(value=False)
        self.adc_rej60 = tk.BooleanVar(value=False)
        self.adc_bipolar = tk.BooleanVar(value=False)
        # AD7193 input buffer (fw1.3). Default ON — the only setting valid with
        # the 6:1 divider in front of the pin; see apply_buf().
        self.adc_buf = tk.BooleanVar(value=True)

        self.title("Koi 8x8 — channel monitor")
        self.configure(padx=10, pady=10)
        self.geometry("1200x760")          # bounded so the grid scrolls

        self._build_controls()
        self._build_grid()
        self._load_offset_table()
        self._load_vzero_table()

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

        # XTR200 error-flag summary (fed by the per-cycle ERR? snapshot).
        self.ef_lbl = ttk.Label(bar, text="EF: n/a", foreground="#777")
        self.ef_lbl.pack(side="left", padx=(12, 0))

        ttk.Button(bar, text="Pause", command=self.toggle).pack(side="right")
        ttk.Button(bar, text="Rescan", command=self.rescan).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="DAC init", command=self.dac_init).pack(side="right", padx=(0, 6))
        self.xtr_btn = ttk.Button(bar, text="XTR off", command=self.toggle_xtr)
        self.xtr_btn.pack(side="right", padx=(0, 6))

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

        # Fifth row: per-channel MEASURE-side zero-offset (voltage). Captures each
        # channel's 0 mA ADC baseline and subtracts it in the display, so every
        # channel reads ~0 at zero drive — removes the per-channel ADC mux-path
        # offset the single-channel internal calibration leaves on channels 1..7.
        bar5 = ttk.Frame(self)
        bar5.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(bar5, text="Measure zero-offset:").pack(side="left")
        ttk.Button(bar5, text="Capture zeros",
                   command=self.start_vzero_cal).pack(side="left", padx=(4, 4))
        ttk.Button(bar5, text="Clear",
                   command=self.clear_vzero).pack(side="left", padx=(0, 8))
        self.vzero_lbl = ttk.Label(bar5, text="Vzero: none", foreground="#777")
        self.vzero_lbl.pack(side="left")
        ttk.Label(bar5, text="  (momentarily zeros drive, records each ch's 0 mA baseline)",
                  foreground="#777").pack(side="left")

        # AD7193 internal calibration, on demand — for A/B-ing the cal's effect on
        # the zero-scale offset. "Run ADC cal" forces the XTR200 front-ends off,
        # runs zero/full-scale cal, restores enables; "Clear ADC cal" reverts to
        # the factory offset/full-scale (undoes any internal cal).
        ttk.Label(bar5, text="   ADC cal:").pack(side="left")
        ttk.Button(bar5, text="Run ADC cal",
                   command=self.run_adc_cal).pack(side="left", padx=(4, 4))
        ttk.Button(bar5, text="Clear ADC cal",
                   command=self.clear_adc_cal).pack(side="left")

        # Sixth row: AD7193 sampling settings (speed / noise / offset knobs).
        bar6 = ttk.Frame(self)
        bar6.grid(row=5, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(bar6, text="ADC  RATE (FS):").pack(side="left")
        er = ttk.Entry(bar6, width=5, textvariable=self.adc_rate)
        er.pack(side="left", padx=(2, 8))
        er.bind("<Return>", lambda _ev: self.apply_rate())

        ttk.Label(bar6, text="AVG:").pack(side="left")
        ea = ttk.Entry(bar6, width=4, textvariable=self.adc_avg)
        ea.pack(side="left", padx=(2, 8))
        ea.bind("<Return>", lambda _ev: self.apply_avg())

        ttk.Label(bar6, text="Gain:").pack(side="left")
        og = ttk.OptionMenu(bar6, self.adc_gain, self.adc_gain.get(),
                            "1", "8", "16", "32", "64", "128",
                            command=lambda _v: self.apply_gain())
        og.pack(side="left", padx=(2, 8))

        ttk.Label(bar6, text="Filter:").pack(side="left")
        of = ttk.OptionMenu(bar6, self.adc_filter, self.adc_filter.get(),
                            "SINC4", "SINC3",
                            command=lambda _v: self.apply_filter())
        of.pack(side="left", padx=(2, 8))

        ttk.Checkbutton(bar6, text="CHOP", variable=self.adc_chop,
                        command=self.apply_chop).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(bar6, text="REJ60", variable=self.adc_rej60,
                        command=self.apply_rej60).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(bar6, text="Bipolar", variable=self.adc_bipolar,
                        command=self.apply_bipolar).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(bar6, text="Buffer", variable=self.adc_buf,
                        command=self.apply_buf).pack(side="left", padx=(0, 8))

        ttk.Label(bar6, text="(gain/chop/buffer recalibrate; higher FS/CHOP/REJ60 = "
                             "slower, cleaner; bipolar shows signed/negative V; "
                             "leave Buffer ON for normal use)",
                  foreground="#777").pack(side="left")

        self._build_bench_rows()

    def _build_bench_rows(self):
        """Bench row: the paper's validation measurements against a Keithley
        2100. Each writes one self-describing CSV (see koi_bench.py); the grid
        poll is suspended while one runs, so nothing competes for the setpoint.
        """
        bar7 = ttk.Frame(self)
        bar7.grid(row=6, column=0, sticky="ew", pady=(0, 4))

        ttk.Label(bar7, text="Bench ch:").pack(side="left")
        ttk.Entry(bar7, width=4, textvariable=self.bench_ch).pack(
            side="left", padx=(2, 6))
        ttk.Label(bar7, text="R load (Ω):").pack(side="left")
        ttk.Entry(bar7, width=10, textvariable=self.bench_r).pack(
            side="left", padx=(2, 2))
        self.bench_rsrc = ttk.Label(bar7, text="(typed)", foreground="#777")
        self.bench_rsrc.pack(side="left", padx=(0, 6))
        ttk.Label(bar7, text="settle (s):").pack(side="left")
        ttk.Entry(bar7, width=5, textvariable=self.bench_settle).pack(
            side="left", padx=(2, 6))
        ttk.Label(bar7, text="out:").pack(side="left")
        ttk.Entry(bar7, width=18, textvariable=self.bench_outdir).pack(
            side="left", padx=(2, 6))
        ttk.Button(bar7, text="Stop", command=self.abort_bench).pack(
            side="right", padx=(0, 4))

        bar8 = ttk.Frame(self)
        bar8.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        self.bench_buttons = {}
        if HAVE_BENCH:
            for key, label, _fn, tip in koi_bench.MEASUREMENTS:
                b = ttk.Button(bar8, text=label,
                               command=lambda k=key: self.start_bench(k))
                b.pack(side="left", padx=(0, 4))
                self.bench_buttons[key] = b
                _Tooltip(b, tip)
        else:
            ttk.Label(bar8, text="bench measurements need koi_bench.py + "
                                 "keithley2100.py on the path",
                      foreground="#a00").pack(side="left")
        self.bench_lbl = ttk.Label(bar8, text="", foreground="#777")
        self.bench_lbl.pack(side="left", padx=(8, 0))

    def _build_grid(self):
        # The 8×8 grid is taller/wider than most screens, so host it in a
        # scrollable canvas (vertical + horizontal) instead of packing it raw.
        container = ttk.Frame(self)
        container.grid(row=8, column=0, sticky="nsew")
        self.rowconfigure(8, weight=1)
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

    def dac_init(self):
        """Manually rewrite every populated DAC's config (soft reset, external
        ref, gain) and reload setpoints — recovery for the internal-2.5V-
        reference brownout signature (>1 V at 0 mA / VREF pin at 2.5 V)."""
        if self.poller is None:
            self.start_polling()
        self.cmd_q.put("DACINIT")
        self.status.config(text="DACINIT sent (rewriting DAC config…)")

    def toggle_xtr(self):
        """Toggle all XTR200 front-ends off/on — an ADC-offset diagnostic.
        With the front-ends DISABLED (`XTR 0x00`) no channel sources any
        current, so whatever the grid still reads is a pure ADC/PCB voltage-
        side offset. Compare that against the enabled-at-0mA reading: if the
        residual collapses when the XTRs go off it was XTR200 IOS/leakage
        current (a current-side term); if it persists it's ADC/PCB. Leaves the
        currents untouched — re-enabling restores normal drive."""
        if self.poller is None:
            self.start_polling()
        if self.xtr_enabled:
            self.cmd_q.put("XTR 0x00")
            self.xtr_enabled = False
            self.xtr_btn.config(text="XTR on")
            self.status.config(text="XTR front-ends OFF — grid = pure ADC offset")
        else:
            active = self.poller.active_boards if self.poller else None
            mask = 0
            for b in (active if active else range(NUM_BOARDS)):
                mask |= (1 << b)
            self.cmd_q.put(f"XTR 0x{mask:02X}")
            self.xtr_enabled = True
            self.xtr_btn.config(text="XTR off")
            self.status.config(text=f"XTR front-ends ON (0x{mask:02X})")

    def run_adc_cal(self):
        """Run the AD7193 internal zero/full-scale calibration on all populated
        boards (`CAL`). The firmware forces the XTR200 front-ends off across the
        cal so no offset current biases the zero-scale point, then restores the
        enable state. Follow with 'Clear ADC cal' to compare the offset."""
        if self.poller is None:
            self.start_polling()
        self.cmd_q.put("CAL")
        self.status.config(text="CAL sent (running ADC internal calibration…)")

    def clear_adc_cal(self):
        """Clear the AD7193 user calibration on all populated boards (`CALCLR`):
        resets each ADC to its factory offset/full-scale, undoing any internal
        cal, so you can read the uncalibrated zero-scale offset."""
        if self.poller is None:
            self.start_polling()
        self.cmd_q.put("CALCLR")
        self.status.config(text="CALCLR sent (cleared ADC cal → factory)")

    # ---- ADC sampling settings -------------------------------------------
    def _ensure_polling(self):
        if self.poller is None:          # nothing drains the queue while paused
            self.start_polling()

    def apply_rate(self):
        try:
            fs = int(float(self.adc_rate.get()))
        except ValueError:
            self.status.config(text="RATE must be an integer 1..1023")
            return
        if not (1 <= fs <= 1023):
            self.status.config(text="RATE out of range (1..1023)")
            return
        self.adc_rate.set(str(fs))
        self._ensure_polling()
        self.cmd_q.put(f"RATE {fs}")
        self.status.config(text=f"ADC RATE (FS) → {fs}")

    def apply_avg(self):
        try:
            n = int(float(self.adc_avg.get()))
        except ValueError:
            self.status.config(text="AVG must be an integer 1..64")
            return
        if not (1 <= n <= 64):
            self.status.config(text="AVG out of range (1..64)")
            return
        self.adc_avg.set(str(n))
        self._ensure_polling()
        self.cmd_q.put(f"AVG {n}")
        self.status.config(text=f"ADC AVG → {n}")

    def apply_gain(self):
        g = self.adc_gain.get()
        self._ensure_polling()
        self.cmd_q.put(f"GAIN {g}")
        self.status.config(text=f"ADC gain → ×{g} (zero-scale recal…)")

    def apply_filter(self):
        f = self.adc_filter.get()
        self._ensure_polling()
        self.cmd_q.put(f"FILTER {f}")
        self.status.config(text=f"ADC filter → {f}")

    def apply_chop(self):
        on = self.adc_chop.get()
        self._ensure_polling()
        self.cmd_q.put("CHOP ON" if on else "CHOP OFF")
        self.status.config(text=f"ADC CHOP → {'ON' if on else 'OFF'} (recalibrating…)")

    def apply_rej60(self):
        on = self.adc_rej60.get()
        self._ensure_polling()
        self.cmd_q.put("REJ60 ON" if on else "REJ60 OFF")
        self.status.config(text=f"ADC REJ60 → {'ON' if on else 'OFF'}")

    def apply_buf(self):
        """AD7193 input buffer (fw1.3). Turning it OFF is confirmed first.

        Unbuffered mode is only valid with a low-impedance source driving the
        ADC pin directly. Through the on-board 6:1 divider (~16.7 kOhm) the
        channel reads 0.000000 — a plausible-looking number that is not a
        measurement. Recoverable by turning Buffer back on.

        The firmware does NOT recalibrate on a BUF change (see 'BUF ON|OFF [CAL]'
        in main.cpp): whenever unbuffered mode is legitimately in use there is a
        forced non-zero voltage on the pin, which a zero-scale cal would absorb
        into the offset register. Use the Cal button with the input at 0 V."""
        on = self.adc_buf.get()
        if not on:
            ok = messagebox.askokcancel(
                "Turn the ADC input buffer OFF?",
                "Unbuffered mode needs an SMU driving the ADC pin directly on a "
                "desoldered channel.\n\n"
                "Through the on-board 6:1 divider every channel will read exactly "
                "0.000000. That is not a measurement.\n\n"
                "The offset is NOT recalibrated on this change — it still holds "
                "the value from the previous buffer setting. Run Cal with the "
                "input at 0 V once you are done.\n\n"
                "Turn Buffer back ON to recover.",
                icon="warning", default="cancel", parent=self)
            if not ok:
                self.adc_buf.set(True)      # revert the checkbox, send nothing
                return
        self._ensure_polling()
        self.cmd_q.put("BUF ON" if on else "BUF OFF")
        self.status.config(
            text=f"ADC buffer → {'ON' if on else 'OFF'} — offset NOT recalibrated "
                 f"(run Cal with the input at 0 V to refresh it)")

    def apply_bipolar(self):
        on = self.adc_bipolar.get()
        self._ensure_polling()
        self.cmd_q.put("BIPOLAR ON" if on else "BIPOLAR OFF")
        self.status.config(
            text=f"ADC polarity → {'bipolar (±FS, signed)' if on else 'unipolar (0..FS)'}")

    def _apply_adc_settings(self, s):
        """Mirror the device's live ADC settings (from ADC?) into the controls.
        Sets the tk vars directly — the OptionMenu/Checkbutton callbacks only
        fire on user interaction, so this display refresh doesn't re-send."""
        if "rate" in s:
            self.adc_rate.set(str(s["rate"]))
        if "avg" in s:
            self.adc_avg.set(str(s["avg"]))
        if "gain" in s:
            self.adc_gain.set(str(s["gain"]))
        if "filter" in s:
            self.adc_filter.set(str(s["filter"]))
        if "chop" in s:
            self.adc_chop.set(s["chop"] not in ("0", "OFF", "off"))
        if "rej60" in s:
            self.adc_rej60.set(s["rej60"] not in ("0", "OFF", "off"))
        if "polarity" in s:
            self.adc_bipolar.set(str(s["polarity"]).lower().startswith("bi"))
        # buf= is fw1.3+; pre-1.3 firmware omits it, so leave the control at its
        # ON default rather than implying the device reported something.
        if "buf" in s:
            self.adc_buf.set(s["buf"] not in ("0", "OFF", "off"))
        self.status.config(
            text=f"ADC settings: FS={s.get('rate')} avg={s.get('avg')} "
                 f"gain=×{s.get('gain')} filter={s.get('filter')} "
                 f"chop={s.get('chop')} rej60={s.get('rej60')} "
                 f"polarity={s.get('polarity')} buf={s.get('buf', 'n/a')}")

    # ---- updates ----------------------------------------------------------
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "data" and payload is not None:
                    self._last = payload
                    self._render(payload)
                    self._check_dac_ref(payload)
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
                elif kind == "vzero_done":
                    self._on_vzero_done(payload)
                elif kind == "bench_progress":
                    self.status.config(text=str(payload))
                elif kind == "bench_done":
                    self._on_bench_done(payload)
                elif kind == "errflags":
                    if payload != self.errflags:
                        self.errflags = payload
                        self._render(self._last)   # repaint fault highlights
                    self._update_ef_label()
                elif kind == "rescan_done":
                    boards = [b for b in range(NUM_BOARDS) if payload & (1 << b)]
                    self.status.config(
                        text=f"rescan: active boards {boards} (0x{payload:02X})")
                elif kind == "adc_settings":
                    self._apply_adc_settings(payload)
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

    # ---- bench measurements (Keithley 2100) --------------------------------
    def start_bench(self, key):
        """Validate inputs on the GUI thread, then hand the run to the poller."""
        if self.bench_active:
            self.status.config(text=f"bench '{self.bench_active}' already running")
            return
        try:
            ch = int(self.bench_ch.get())
            settle = float(self.bench_settle.get())
        except ValueError:
            self.status.config(text="bad bench channel / settle")
            return
        if not (0 <= ch < TOTAL_CH):
            self.status.config(text="bench ch must be 0..63")
            return

        # Everything except the ohms scan derives a current from R, so refuse to
        # run without one rather than silently recording numbers that will have
        # to be re-derived later.
        r_load = 0.0
        if key != "rload":
            try:
                r_load = float(self.bench_r.get())
            except ValueError:
                r_load = 0.0
            if r_load <= 0:
                self.status.config(
                    text="set R load first — run 'Measure R (4-wire)'")
                return

        board = ch // CH_PER_BOARD
        active = self.poller.active_boards if self.poller else None
        if active is not None and board not in active:
            self.status.config(
                text=f"ch{ch}: board {board} not detected — Rescan first")
            return

        mask = 0
        for b in (active if active else [board]):
            mask |= (1 << b)

        if self.poller is None:
            self.start_polling()
        self.bench_active = key
        for b in self.bench_buttons.values():
            b.state(["disabled"])
        self.poller.request_bench(key, dict(
            ch=ch, r_load=r_load, r_source=self.bench_r_source,
            outdir=self.bench_outdir.get(), settle=settle, xtr_mask=mask))
        self.status.config(text=f"bench: {key} on ch{ch} …")

    def abort_bench(self):
        if self.poller and self.bench_active:
            self.poller.abort_bench()
            self.status.config(text=f"bench: aborting {self.bench_active} …")

    def _on_bench_done(self, payload):
        key, path, summary = payload[0], payload[1], payload[2]
        extra = payload[3] if len(payload) > 3 else None
        self.bench_active = None
        for b in self.bench_buttons.values():
            b.state(["!disabled"])
        # The ohms scan is the one measurement that feeds the others: adopt its
        # result as R for everything downstream, and record that it was measured
        # rather than typed so the CSV headers say so.
        if key == "rload" and extra:
            self.bench_r.set(f"{extra:.4f}")
            self.bench_r_source = f"4-wire {time.strftime('%Y-%m-%d %H:%M')}"
            self.bench_rsrc.config(text="(4-wire)", foreground="#070")
        if path:
            self.bench_lbl.config(text=f"{os.path.basename(path)} — {summary}",
                                  foreground="#070")
            self.status.config(text=f"bench {key}: {summary}")
        else:
            self.bench_lbl.config(text=f"{key}: {summary}", foreground="#a00")
            self.status.config(text=f"bench {key} failed: {summary}")

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

    # ---- per-channel measure-side zero-offset (voltage) -------------------
    def start_vzero_cal(self):
        if self.vzero_active:
            self.status.config(text="zero-offset capture already running")
            return
        if self.poller is None:
            self.start_polling()
        self.vzero_active = True
        self.poller.request_vzero_cal(dict(settle=0.6))
        self.status.config(text="capturing zero-offsets (drive off)…")

    def _on_vzero_done(self, payload):
        vzero_v, used, skipped, path, err = payload
        self.vzero_active = False
        self.vzero_v = vzero_v
        # Capture left the firmware at 0 mA with front-ends re-enabled — reflect
        # the zeroed setpoints so the fields and DAC-brownout watchdog stay in sync.
        self.setpoint_ma = [0.0] * TOTAL_CH
        for g in range(TOTAL_CH):
            self.cells[g]["set"].set("0")
        self._update_vzero_label(used)
        msg = f"zero-offset capture: {len(used)} ch"
        if skipped:
            msg += f", skipped {skipped} (still driven/faulted)"
        msg += f"  ({err})" if err else f"  → {os.path.basename(path)}"
        self.status.config(text=msg)
        self._recompute()

    def clear_vzero(self):
        """Zero the in-memory measure-side table and remove its file."""
        self.vzero_v = [0.0] * TOTAL_CH
        note = ""
        try:
            os.remove(os.path.join(os.getcwd(), VZERO_TABLE_FILE))
            note = f" ({VZERO_TABLE_FILE} deleted)"
        except OSError:
            pass
        self.vzero_lbl.config(text="Vzero: none")
        self.status.config(text="zero-offset table cleared" + note)
        self._recompute()

    def _update_vzero_label(self, used=None):
        nz = [self.vzero_v[g] * 1e3 for g in range(TOTAL_CH)
              if self.vzero_v[g] != 0.0]
        if nz:
            n = len(used) if used is not None else len(nz)
            self.vzero_lbl.config(
                text=f"Vzero: {n} ch, {min(nz):+.3f}..{max(nz):+.3f} mV")
        else:
            self.vzero_lbl.config(text="Vzero: none")

    def _load_vzero_table(self):
        """Adopt a previously saved measure-side zero-offset table, if any."""
        try:
            with open(os.path.join(os.getcwd(), VZERO_TABLE_FILE)) as f:
                d = json.load(f)
            tab = [float(x) for x in d["vzero_v"]]
            if len(tab) != TOTAL_CH:
                raise ValueError(f"expected {TOTAL_CH} entries, got {len(tab)}")
        except FileNotFoundError:
            return
        except Exception as e:
            self.vzero_lbl.config(text=f"Vzero: load failed ({e})")
            return
        self.vzero_v = tab
        self._update_vzero_label()

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

    def _check_dac_ref(self, vals):
        """Auto-recover a DAC that lost its external-ref config (brownout →
        internal 2.5 V reference): a populated channel commanded to 0 mA
        reading raw > DAC_FAULT_RAW_V triggers a `DACINIT <b>` rewrite for its
        board. Rate-limited to 1/10 s per board, and after 3 sends without the
        condition clearing it gives up (guards against a reset loop when the
        real cause is elsewhere — e.g. the GUI restarted while the firmware
        still drives setpoints this session never commanded). Suppressed while
        a sweep/cal runs, since those drive currents setpoint_ma doesn't track."""
        if self.sweep_active or self.char_active or self.offcal_active \
                or self.vzero_active or self.bench_active:
            return
        now = time.time()
        suspect = {g // CH_PER_BOARD for g, raw in enumerate(vals)
                   if raw == raw and self.setpoint_ma[g] == 0
                   and raw > DAC_FAULT_RAW_V}
        for b in range(NUM_BOARDS):
            if b not in suspect:
                self._dacinit_state.pop(b, None)     # clean again → re-arm
                continue
            last, strikes = self._dacinit_state.get(b, (0.0, 0))
            if strikes >= 3 or now - last < 10.0:
                continue
            self._dacinit_state[b] = (now, strikes + 1)
            self.cmd_q.put(f"DACINIT {b}")
            if strikes + 1 >= 3:
                self.status.config(text=f"board {b}: still >1 V at 0 mA after "
                                        f"3 DACINITs — giving up, check hardware")
            else:
                self.status.config(text=f"board {b}: >1 V at 0 mA — DAC ref "
                                        f"lost? DACINIT sent")

    def _ef_fault(self, g):
        """True if channel g's XTR200 flags an error. Only meaningful for
        populated channels — a missing board breaks the 165 chain, so callers
        must gate on the channel actually reading (non-NaN)."""
        if self.errflags is None:
            return False
        level = (self.errflags >> g) & 1
        return (level == 0) if ERRFLAG_ACTIVE_LOW else (level == 1)

    def _update_ef_label(self):
        if self.errflags is None:
            self.ef_lbl.config(text="EF: n/a", foreground="#777")
            return
        faulted = [g for g in range(TOTAL_CH)
                   if self._last[g] == self._last[g] and self._ef_fault(g)]
        if not faulted:
            self.ef_lbl.config(text="EF: ok", foreground="#777")
        elif len(faulted) > 8:
            self.ef_lbl.config(text=f"EF fault: {len(faulted)} channels",
                               foreground="#c00")
        else:
            self.ef_lbl.config(text="EF fault: ch " + ",".join(map(str, faulted)),
                               foreground="#c00")

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
                # Correct raw by the global Offset+ and this channel's measured
                # zero baseline (per-channel measure-side offset), then divide.
                eff = raw + off_v - self.vzero_v[g]
                heater_v = eff * div
                # The divider steals eff/20k from the source; the heater gets the
                # rest. Source current = commanded + this channel's XTR offset
                # current (per-channel table from "Cal offsets").
                i_div = eff / DIVIDER_BOTTOM_OHMS
                i_a = (self.setpoint_ma[g] + self.ios_ma[g]) * 1e-3 - i_div
                fault = self._ef_fault(g)
                bg = "#fdd" if fault else "white"
                c["raw"].set(f"{raw * 1e3:8.3f} mV" + ("  EF!" if fault else ""))
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
                c["frame"].config(bg=bg)
                for lbl in all_lbls:
                    c[lbl].config(bg=bg)
                c["raw_lbl"].config(fg="#c00" if fault else "#555")
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
