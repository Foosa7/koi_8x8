#!/usr/bin/env python3
"""
koi_bench.py — the paper's validation measurements, as callable routines.

Every routine here takes a live KoiLink plus a live Keithley2100 and writes one
self-describing CSV. They are driven from the "Bench" row in koi_gui.py (which
runs them on the poller thread, so the serial port keeps a single owner), but
each is a plain function and can equally be called from a script.

WHY THE CSVs CARRY A HEADER BLOCK
    The load resistance used to live as a hand-typed constant in three separate
    scripts, and when it turned out to be wrong every derived number had to be
    re-traced by hand. So each file records, as `#` comment lines, the channel,
    the load resistance AND how it was obtained, the live ADC settings, the
    firmware IDN, the DMM IDN, and the timestamp. `load_bench_csv()` skips the
    comments; pandas takes `comment='#'`.

    Corollary: nothing here derives current from an assumed resistance at
    acquisition time if the raw volts can be stored instead. Raw DMM volts are
    always a column, so any R can be applied afterwards.

EXACT DAC CODES
    The firmware maps current to code as `code = round(mA * calSlope / VREF *
    65535)`, so code `c` is commanded with `mA = c * VREF / (calSlope * 65535)`.
    That inverse assumes the firmware still holds the ideal seed (calSlope 0.47,
    calOffset 0) — trim those and CODE_MA below must be updated to match, or the
    code-level runs silently land between codes.
"""

import csv
import math
import os
import random
import re
import statistics
import time

# --- firmware-side constants (mirror src/main.cpp) --------------------------
VREF = 3.0
CAL_SLOPE = 0.47              # V/mA XTR200 transconductance seed
DAC_FULL = 65535
CODE_MA = VREF / (CAL_SLOPE * DAC_FULL)     # 97.398 nA per code
MAX_MA = VREF / CAL_SLOPE                   # 6.3830 mA full scale
R_DIVIDER = 120e3             # 100k + 20k sense divider across the load node

DEFAULT_SETTLE = 0.8          # s; 0.8 and 1.5 agree to 0.12 nA (characterized)
DEFAULT_NPLC = 1              # NPLC 1 + autozero OFF: 0.1 LSB in one reading
DEFAULT_VRANGE = 10.0         # fixed 10 V: spans 0-6.4 V node without a range
                              # change landing in a fitted intercept


def code_to_ma(c):
    return c * CODE_MA


def ma_to_code(ma):
    return int(round(ma / CODE_MA))


# ===========================================================================
# Context + CSV plumbing
# ===========================================================================
class BenchCtx:
    """Everything a measurement needs, captured once on the GUI thread.

    `emit(str)` reports progress; `stopped()` is polled between points so a run
    aborts promptly without leaving the channel driven.
    """

    def __init__(self, link, dmm, channel, r_load, r_source="typed",
                 outdir=".", settle=DEFAULT_SETTLE, nplc=DEFAULT_NPLC,
                 xtr_mask=0x01, emit=None, stopped=None, v_sign=1):
        self.link = link
        self.dmm = dmm
        self.ch = channel
        self.r_load = r_load
        self.r_source = r_source        # "4-wire <ts>" once measured, else "typed"
        self.outdir = outdir
        self.settle = settle
        self.nplc = nplc
        self.xtr_mask = xtr_mask        # front-end enables to restore afterwards
        self.emit = emit or (lambda s: None)
        self.stopped = stopped or (lambda: False)
        self.v_sign = v_sign        # +1, or -1 if the DMM leads are reversed
        self._err_benign = 0
        self._err_bad = []

    # -- Koi helpers --------------------------------------------------------
    def iset(self, ma):
        self.link.command(f"ISET {self.ch} {ma:.9f}")

    def koi_read(self):
        """One ADC-pin reading on this channel, in volts (NaN if it fails)."""
        r = self.link.command(f"MEAS? {self.ch}")
        try:
            return float(r)
        except (TypeError, ValueError):
            return float("nan")

    def xtr_on(self):
        self.link.command(f"XTR 0x{self.xtr_mask:02X}")

    def xtr_off(self):
        self.link.command("XTR 0x00")

    def idle(self):
        """Leave the channel safe: 0 mA, front-ends back to the entry state."""
        self.iset(0.0)
        self.link.command(f"XTR 0x{self.xtr_mask:02X}")

    # -- DMM helpers --------------------------------------------------------
    def dmm_stats(self, n):
        """(mean, sd) over n readings, with the error queue checked after.

        CLAUDE.md: check the queue DURING acquisition, not just after config.
        A -113 'Undefined header' every ~130 readings is a cosmetic artifact of
        the USBTMC stall-recovery re-issuing a command mid-READ?; an overload
        (>1e30) or a short burst invalidates the run and must surface.
        """
        mean, sd, vals = self.dmm.read_stats(n)
        self.check_errors(vals)
        return mean * self.v_sign, sd

    def check_errors(self, vals=None, where=""):
        """Fold an overload check and one error-queue drain into the run log.

        Two buckets, because they mean different things:

        * **parse errors** (SCPI -1xx: -113 undefined header, -109 missing
          parameter, …) are the documented artifact of the 2100's USBTMC
          interface stalling roughly once per few hundred readings and the
          driver re-issuing a command mid-`READ?`. A command got mangled; no
          reading was corrupted. Counted and reported, not fatal — but a run
          with an unusual number of them deserves a second look, which is why
          the count is always printed rather than hidden.
        * **everything else**, plus any overload (>1e30), invalidates the run.
        """
        if vals and any(abs(v) > 1e30 for v in vals):
            self._err_bad.append(
                f"OVERLOAD {where}({max(vals, key=abs):.3g})".strip())
        for e in self.dmm.errors():
            code = e.lstrip("+").split(",")[0]
            try:
                parse_class = -200 < int(code) < 0
            except ValueError:
                parse_class = False
            if parse_class:
                self._err_benign += 1
            else:
                self._err_bad.append(f"{where}{e}")

    def err_summary(self):
        s = f"{self._err_benign} parse-class SCPI errors (stall recovery)"
        if self._err_bad:
            s += f"; {len(self._err_bad)} FATAL: {self._err_bad[0]}"
        return s

    @property
    def err_fatal(self):
        return bool(self._err_bad)

    # -- setup checks -------------------------------------------------------
    def check_polarity(self, i_probe=1.0):
        """Drive a known current and look at the sign the meter reports.

        Reversed DMM leads add no error to a DC measurement across a resistor,
        so this corrects rather than refuses — but it is recorded in the CSV
        header and announced, because silently flipping a sign would hide a
        genuinely miswired fixture.
        """
        self.xtr_on()
        self.iset(i_probe)
        time.sleep(max(self.settle, 1.0))
        v = self.dmm.read()
        self.iset(0.0)
        if abs(v) < 1e-3:
            self.emit(f"WARNING: only {v*1e3:.3f} mV at {i_probe} mA — "
                      "is the load connected?")
            return 1
        self.v_sign = -1 if v < 0 else 1
        if self.v_sign < 0:
            self.emit("NOTE: DMM leads are reversed — sign corrected in software "
                      "(harmless for DC, but recorded in the CSV header)")
        return self.v_sign

    def require_adc_rate(self, minimum=8):
        """Refuse to record on-board ADC readings below a usable filter word.

        RATE 8 is fine — verified 2026-07-30, agreeing with RATE 96 to 0.01 mV
        and returning all 64 channels on a full `MEASA?`. (An earlier note
        claimed 4/8/12 returned raw code 0; that was reads taken before the
        filter settled after the RATE change, not the filter word itself.
        Always discard ~5 reads after changing RATE — every routine here does.)

        The guard is kept because a genuinely too-low word still reads zero
        while replying OK, which is invisible in the data itself.
        """
        adc = self.link.command("ADC?") or ""
        m = re.search(r"rate=(\d+)", adc)
        if not m:
            return
        rate = int(m.group(1))
        if rate < minimum:
            raise RuntimeError(
                f"ADC RATE is {rate}, below the usable minimum of {minimum} — "
                f"the on-board readings would all be zero. Set RATE {minimum} "
                "or higher (96 recommended) and re-run.")

    # -- current model ------------------------------------------------------
    def i_source(self, v_node):
        """Current out of the XTR200, in mA: the load's share plus the sense
        divider's known share. Exact and a-priori — not a calibrated parameter.

        Returns mA, not amps, because every setpoint in this file and in the
        firmware protocol is in mA and the two get differenced constantly.
        """
        return (v_node / self.r_load + v_node / R_DIVIDER) * 1e3


def _stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def bench_outdir(base="bench"):
    """`bench/<YYYYMMDD>/`, created — where every run's CSVs are filed.

    One run per dated folder keeps `tools/` free of loose data files, and makes
    "which campaign is this number from?" answerable from the path alone. Used
    by run_campaign.py and the standalone scripts so they all land together.
    """
    d = os.path.join(base, time.strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    return d


def open_csv(ctx, name, columns, extra=()):
    """Open `<outdir>/<name>_ch<g>_<ts>.csv`, write the provenance block and the
    column header, and return (file, writer, path)."""
    os.makedirs(ctx.outdir, exist_ok=True)
    path = os.path.join(ctx.outdir, f"{name}_ch{ctx.ch}_{_stamp()}.csv")
    f = open(path, "w", newline="")
    idn = ctx.link.command("*IDN?") or "?"
    adc = ctx.link.command("ADC?") or "?"
    try:
        dmm_idn = ctx.dmm.idn()
    except Exception:
        dmm_idn = "?"
    for line in (
        f"measurement: {name}",
        f"utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}  "
        f"local: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"channel: g={ctx.ch} (board {ctx.ch // 8}, ch {ctx.ch % 8})",
        f"r_load_ohm: {ctx.r_load:.4f}   source: {ctx.r_source}",
        f"r_divider_ohm: {R_DIVIDER:.0f}",
        f"settle_s: {ctx.settle}   dmm_nplc: {ctx.nplc}",
        f"code_nA: {CODE_MA * 1e6:.4f}   full_scale_mA: {MAX_MA:.4f}",
        f"koi: {idn}",
        f"koi_adc: {adc}",
        f"dmm: {dmm_idn}",
        f"dmm_polarity: {'inverted (corrected in software)' if ctx.v_sign < 0 else 'normal'}",
    ) + tuple(extra):
        f.write(f"# {line}\n")
    w = csv.writer(f)
    w.writerow(columns)

    # Flush after every row. These runs are minutes long against an instrument
    # whose USBTMC interface is known to stall; without this, a fatal hang 40
    # minutes in loses every point still sitting in the buffer.
    class _FlushingWriter:
        def __init__(self, writer, fh):
            self._w, self._f = writer, fh

        def writerow(self, row):
            self._w.writerow(row)
            self._f.flush()

    return f, _FlushingWriter(w, f), path


def load_bench_csv(path):
    """Read one back: returns (meta_dict, list_of_row_dicts)."""
    meta, body = {}, []
    with open(path) as f:
        for line in f:
            if not line.startswith("#"):
                body.append(line)
                continue
            k, _, v = line[1:].strip().partition(":")
            meta[k.strip()] = v.strip()
    return meta, list(csv.DictReader(body))


# ===========================================================================
# 1. Load resistance, four-wire
# ===========================================================================
def meas_rload(ctx, n=20, nplc=10):
    """Offset-compensated four-wire ohms on the load, front-ends OFF.

    Front-ends off matters: any current the XTR200 pushes through the load adds
    I*R to the meter's reading. Disabled leakage is 0.35 nA against a ~1 mA test
    current (0.35 ppm); an *enabled* channel at 0 mA still carries its offset
    current, which is 0.4-2.4 %. Offset compensation (V measured with the test
    current on and off) then cancels any constant residual and thermal EMF.
    """
    ctx.emit("R: front-ends off, settling…")
    ctx.iset(0.0)
    ctx.xtr_off()
    time.sleep(1.0)

    ctx.dmm.config_fres(nplc=nplc, rng=1e3)
    ctx.dmm.write("SENS:FRES:OCOM ON")          # offset-compensated ohms
    ctx.dmm.query("*OPC?")

    vals = ctx.dmm.read_n(n)
    ctx.link.command(f"XTR 0x{ctx.xtr_mask:02X}")

    # An open SENSE pair reads full-scale overload (~9.9e37). Four-wire ohms
    # needs BOTH pairs at the load pads; falling back to two-wire here would be
    # worse than useless, because lead and relay resistance is 100-1000 ppm on
    # 1 kOhm — the same size as the errors this campaign exists to resolve.
    if any(abs(v) > 1e30 for v in vals):
        raise RuntimeError(
            "4-wire ohms reads overload — the 2100's SENSE terminals look open. "
            "Connect a second (sense) pair at the load pads, or measure R "
            "separately; two-wire through the leads is not accurate enough "
            "(100-1000 ppm on 1 kΩ).")
    ctx.check_errors(vals, where="ohms scan: ")

    f, w, path = open_csv(ctx, "rload", ["reading", "R_ohm"],
                          extra=(f"ocomp: ON   nplc: {nplc}   n: {n}",))
    with f:
        for i, v in enumerate(vals):
            w.writerow([i, f"{v:.6f}"])

    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    summary = (f"R = {mean:.4f} Ω  ±{sd*1e3:.2f} mΩ (1σ, n={len(vals)})  "
               f"[{ctx.err_summary()}]")
    ctx.emit(summary)
    return path, summary, mean


# ===========================================================================
# 2. Set current vs 2100 — source accuracy, DMM only
# ===========================================================================
def meas_set_vs_dmm(ctx, npoints=64, i_min=0.1, i_max=MAX_MA, nreads=8,
                    randomize=True):
    """Commanded current vs the current the 2100 actually sees. The on-board ADC
    takes no part, so nothing about the sense path can contaminate the source
    accuracy number.

    Randomized point order by default: it costs nothing and decorrelates any
    residual drift from the setpoint, so a slow thermal or reference drift shows
    up as scatter rather than as a fake gain error.
    """
    ctx.dmm.config_vdc(nplc=ctx.nplc, rng=DEFAULT_VRANGE, autozero=False)
    ctx.check_polarity()

    pts = [i_min + i * (i_max - i_min) / (npoints - 1) for i in range(npoints)]
    order = pts[:]
    if randomize:
        random.shuffle(order)

    f, w, path = open_csv(
        ctx, "set_vs_dmm",
        ["seq", "cmd_mA", "dmm_V", "dmm_sd_V", "n", "I_source_mA", "err_uA", "t_s"],
        extra=(f"order: {'randomized' if randomize else 'monotonic'}   "
               f"points: {npoints}   reads/point: {nreads}",))
    t0 = time.time()
    rows = []
    with f:
        for seq, ma in enumerate(order):
            if ctx.stopped():
                ctx.emit("aborted")
                break
            ctx.iset(ma)
            time.sleep(ctx.settle)
            v, sd = ctx.dmm_stats(nreads)
            isrc = ctx.i_source(v)
            err_ua = (isrc - ma) * 1e3
            w.writerow([seq, f"{ma:.9f}", f"{v:.9f}", f"{sd:.3e}", nreads,
                        f"{isrc:.9f}", f"{err_ua:.4f}", f"{time.time()-t0:.3f}"])
            rows.append((ma, isrc))
            ctx.emit(f"set-vs-2100 {seq+1}/{len(order)}: "
                     f"{ma:.4f} mA → {isrc:.4f} mA ({err_ua:+.2f} µA)")
    ctx.idle()

    summary = _fit_summary(rows) + f"  [{ctx.err_summary()}]"
    ctx.emit(summary)
    return path, summary


def _fit_summary(rows):
    if len(rows) < 2:
        return "too few points to fit"
    xs = [a for a, _ in rows]
    ys = [b for _, b in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    m = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx
    b = my - m * mx
    resid = [y - (m * x + b) for x, y in zip(xs, ys)]
    rms = math.sqrt(sum(r * r for r in resid) / n)
    worst = max(abs(y - x) for x, y in rows) * 1e3
    return (f"gain {(m-1)*100:+.3f} %, I_OS {b*1e3:+.2f} µA, "
            f"worst dev {worst:.2f} µA, residual {rms*1e3:.3f} µA rms "
            f"({rms/MAX_MA*100:.4f} % FS)")


# ===========================================================================
# 3. Drive sweep — both meters (source + readback + resistance)
# ===========================================================================
def meas_drive_sweep(ctx, npoints=48, i_min=0.05, i_max=MAX_MA, nreads=8,
                     koi_reads=8):
    """The workhorse: at every setpoint record the DMM across the load AND the
    Koi's own ADC-pin reading, each with its repeat scatter. One file feeds the
    transfer figure, both deviation panels, the end-to-end resistance check and
    the precision-vs-setpoint plot.
    """
    ctx.require_adc_rate()
    ctx.dmm.config_vdc(nplc=ctx.nplc, rng=DEFAULT_VRANGE, autozero=False)
    ctx.check_polarity()
    pts = [i_min + i * (i_max - i_min) / (npoints - 1) for i in range(npoints)]

    f, w, path = open_csv(
        ctx, "drive_sweep",
        ["cmd_mA", "dmm_V", "dmm_sd_V", "dmm_n", "koi_raw_V", "koi_sd_V",
         "koi_n", "I_source_mA", "R_reported_ohm", "t_s"],
        extra=(f"points: {npoints}   dmm reads/point: {nreads}   "
               f"koi reads/point: {koi_reads}",))
    t0 = time.time()
    with f:
        for k, ma in enumerate(pts):
            if ctx.stopped():
                ctx.emit("aborted")
                break
            ctx.iset(ma)
            time.sleep(ctx.settle)
            v, vsd = ctx.dmm_stats(nreads)
            kv = [ctx.koi_read() for _ in range(koi_reads)]
            kv = [x for x in kv if x == x]
            kmean = statistics.fmean(kv) if kv else float("nan")
            ksd = statistics.stdev(kv) if len(kv) > 1 else 0.0
            isrc = ctx.i_source(v)                    # mA, from the DMM
            # R AS THE INSTRUMENT REPORTS IT — the end-to-end figure of merit:
            # the Koi's own sense reading over the COMMANDED current, minus the
            # divider's share. Deriving R from the DMM instead would be circular
            # (i_source already came from v and r_load, so it returns r_load
            # exactly, for every row).
            v_heater = kmean * 6.0                    # nominal ratio; the
            # calibrated ratio/offset are applied at analysis time, not here
            i_heater_ma = ma - (v_heater / R_DIVIDER) * 1e3
            rmeas = (v_heater / (i_heater_ma * 1e-3)
                     if i_heater_ma > 0 and v_heater == v_heater
                     else float("nan"))
            w.writerow([f"{ma:.9f}", f"{v:.9f}", f"{vsd:.3e}", nreads,
                        f"{kmean:.9f}", f"{ksd:.3e}", len(kv),
                        f"{isrc:.9f}", f"{rmeas:.4f}", f"{time.time()-t0:.3f}"])
            ctx.emit(f"drive sweep {k+1}/{len(pts)}: {ma:.4f} mA → "
                     f"{v*1e3:.3f} mV DMM, {kmean*1e3:.4f} mV ADC")
    ctx.idle()
    summary = f"drive sweep done, {len(pts)} points  [{ctx.err_summary()}]"
    ctx.emit(summary)
    return path, summary


# ===========================================================================
# 4. Low-current sweep — the sense-path knee
# ===========================================================================
def meas_low_current(ctx, nreads=8):
    """Dense points through the 44 mV knee, both meters, on a FIXED DMM range.

    Autoranging here would split the sweep across two range calibrations and
    fold the difference between them straight into the fitted intercept — which
    is the quantity this measurement exists to determine.
    """
    ctx.require_adc_rate()
    ctx.dmm.config_vdc(nplc=10, rng=DEFAULT_VRANGE, autozero=True)
    ctx.check_polarity()
    pts = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030, 0.035, 0.040, 0.045,
           0.050, 0.060, 0.075, 0.100, 0.150, 0.200, 0.400, 1.0, 2.0, 3.0]

    f, w, path = open_csv(
        ctx, "low_current",
        ["cmd_mA", "dmm_V", "dmm_sd_V", "koi_raw_V", "koi_sd_V",
         "incr_ratio", "t_s"],
        extra=("dmm range: fixed 10 V (no autorange — see docstring)",
               f"reads/point: {nreads}"))
    t0 = time.time()
    prev = None
    with f:
        for k, ma in enumerate(pts):
            if ctx.stopped():
                ctx.emit("aborted")
                break
            ctx.iset(ma)
            time.sleep(max(ctx.settle, 1.0))
            v, vsd = ctx.dmm_stats(nreads)
            kv = [ctx.koi_read() for _ in range(nreads)]
            kv = [x for x in kv if x == x]
            kmean = statistics.fmean(kv) if kv else float("nan")
            ksd = statistics.stdev(kv) if len(kv) > 1 else 0.0
            ratio = ""
            if prev and kmean == kmean and (kmean - prev[1]) > 1e-9:
                ratio = f"{(v - prev[0]) / (kmean - prev[1]):.4f}"
            prev = (v, kmean)
            w.writerow([f"{ma:.6f}", f"{v:.9f}", f"{vsd:.3e}",
                        f"{kmean:.9f}", f"{ksd:.3e}", ratio,
                        f"{time.time()-t0:.3f}"])
            ctx.emit(f"low-I {k+1}/{len(pts)}: {ma:.3f} mA → "
                     f"{v*1e3:.3f} mV node, ratio {ratio or '—'}")
    ctx.idle()
    summary = f"low-current sweep done, {len(pts)} points  [{ctx.err_summary()}]"
    ctx.emit(summary)
    return path, summary


# ===========================================================================
# 5. Single-code steps — DNL and monotonicity
# ===========================================================================
def meas_codes(ctx, centres=(1.0, 3.0, 5.0), ncodes=33, nreads=8):
    """Adjacent-code steps around each centre. DMM only.

    Adjacent differences are taken seconds apart at essentially constant
    dissipation, so DNL is the one code-level number a monotonic order cannot
    distort — unlike INL, which needs the randomized run below.
    """
    ctx.dmm.config_vdc(nplc=ctx.nplc, rng=DEFAULT_VRANGE, autozero=False)
    ctx.check_polarity()
    half = ncodes // 2

    f, w, path = open_csv(
        ctx, "codes",
        ["centre_mA", "code", "cmd_mA", "dmm_V", "dmm_sd_V", "I_source_mA"],
        extra=(f"centres: {centres}   codes/centre: {ncodes}   "
               f"reads/point: {nreads}",))
    all_steps = []
    with f:
        for centre in centres:
            c0 = ma_to_code(centre)
            codes = list(range(c0 - half, c0 + half + 1))
            series = []
            for k, c in enumerate(codes):
                if ctx.stopped():
                    ctx.emit("aborted")
                    break
                ma = code_to_ma(c)
                ctx.iset(ma)
                time.sleep(ctx.settle)
                v, sd = ctx.dmm_stats(nreads)
                isrc = ctx.i_source(v)
                series.append(isrc)
                w.writerow([f"{centre:g}", c, f"{ma:.9f}", f"{v:.9f}",
                            f"{sd:.3e}", f"{isrc:.9f}"])
                ctx.emit(f"codes @{centre:g} mA: {k+1}/{len(codes)} (code {c})")
            steps = [series[i + 1] - series[i] for i in range(len(series) - 1)]
            all_steps += steps
    ctx.idle()

    if all_steps:
        dnl = [s / CODE_MA - 1 for s in all_steps]
        nonmono = sum(1 for d in dnl if d <= -1)
        summary = (f"{len(all_steps)} steps: mean {statistics.fmean(all_steps)*1e6:.2f} nA "
                   f"(ideal {CODE_MA*1e6:.2f}), max |DNL| {max(abs(d) for d in dnl):.2f} LSB, "
                   f"{nonmono} non-monotonic  [{ctx.err_summary()}]")
    else:
        summary = "no steps recorded"
    ctx.emit(summary)
    return path, summary


# ===========================================================================
# 6. INL — randomized order, interleaved drift reference
# ===========================================================================
def meas_inl(ctx, npoints=48, passes=2, ref_every=4, i_min=0.2, i_max=MAX_MA,
             nreads=8, order="random"):
    """INL with the thermal confound broken two ways.

    `order="random"` visits codes shuffled, so any slow drift is uncorrelated
    with the code; an interleaved reference point re-measured every `ref_every`
    points is interpolated to each datum's timestamp and divided out. With a
    blower holding the load at ambient, `order="monotonic"` should now agree —
    running both is the check that the fan actually removed the self-heating,
    rather than an assumption that it did.
    """
    ctx.dmm.config_vdc(nplc=ctx.nplc, rng=DEFAULT_VRANGE, autozero=False)
    ctx.check_polarity()

    lo, hi = ma_to_code(i_min), ma_to_code(i_max)
    codes = [lo + round(i * (hi - lo) / (npoints - 1)) for i in range(npoints)]
    ref_code = (lo + hi) // 2
    ref_ma = code_to_ma(ref_code)

    def point(ma):
        ctx.iset(ma)
        time.sleep(ctx.settle)
        v, _ = ctx.dmm_stats(nreads)
        return ctx.i_source(v)

    f, w, path = open_csv(
        ctx, "inl",
        ["pass", "seq", "code", "cmd_mA", "I_source_mA", "I_corr_mA",
         "ref_interp_mA", "t_s"],
        extra=(f"order: {order}   points: {npoints}   passes: {passes}   "
               f"ref every: {ref_every} (code {ref_code}, {ref_ma:.4f} mA)",))
    t0 = time.time()
    rows = []
    with f:
        for p in range(passes):
            seq = codes[:]
            if order == "random":
                random.shuffle(seq)
            refs = [(time.time() - t0, point(ref_ma))]
            for k, c in enumerate(seq):
                if ctx.stopped():
                    ctx.emit("aborted")
                    break
                ma = code_to_ma(c)
                t = time.time() - t0
                isrc = point(ma)
                if k % ref_every == ref_every - 1:
                    refs.append((time.time() - t0, point(ref_ma)))
                # drift-correct against the reference interpolated to this
                # point's timestamp (bracketing refs, else the nearest)
                ri = _interp(refs, t)
                icorr = isrc * (refs[0][1] / ri) if ri else isrc
                w.writerow([p, k, c, f"{ma:.9f}", f"{isrc:.9f}",
                            f"{icorr:.9f}", f"{ri:.9f}", f"{t:.3f}"])
                rows.append((ma, icorr))
                ctx.emit(f"INL pass {p+1}/{passes}: {k+1}/{len(seq)} (code {c})")
            drift = (refs[-1][1] / refs[0][1] - 1) * 1e6 if refs[0][1] else 0
            ctx.emit(f"pass {p+1} reference drift {drift:+.1f} ppm")
    ctx.idle()

    summary = _inl_summary(rows) + f"  [{ctx.err_summary()}]"
    ctx.emit(summary)
    return path, summary


def _interp(refs, t):
    """Reference value interpolated to time t (refs = [(t, value), …])."""
    if not refs:
        return None
    if len(refs) == 1 or t <= refs[0][0]:
        return refs[0][1]
    if t >= refs[-1][0]:
        return refs[-1][1]
    for i in range(len(refs) - 1):
        t0, v0 = refs[i]
        t1, v1 = refs[i + 1]
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v0
    return refs[-1][1]


def _inl_summary(rows):
    if len(rows) < 3:
        return "too few points"
    by = {}
    for ma, i in rows:
        by.setdefault(round(ma, 9), []).append(i)
    xs = sorted(by)
    ys = [statistics.fmean(by[x]) for x in xs]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    m = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx
    b = my - m * mx
    inl = [(y - (m * x + b)) / CODE_MA for x, y in zip(xs, ys)]
    rms = math.sqrt(sum(v * v for v in inl) / len(inl))
    worst = max(inl, key=abs)
    rep = [statistics.stdev(v) / CODE_MA for v in by.values() if len(v) > 1]
    reptxt = f", repeatability {statistics.fmean(rep):.2f} LSB" if rep else ""
    return (f"INL rms {rms:.2f} LSB, max {worst:+.2f} LSB "
            f"({abs(worst)*CODE_MA/MAX_MA*100:.4f} % FS), "
            f"gain {(m-1)*100:+.3f} %{reptxt}")


# ===========================================================================
# 7. Noise vs RATE / AVG
# ===========================================================================
NOISE_GRID = ((16, 4), (96, 1), (96, 4), (96, 16), (240, 4))


def meas_noise(ctx, i_test=1.0, nreads=30, discard=5, grid=NOISE_GRID):
    """ADC noise floor across the sampling settings, with the 2100 watching the
    same node as a bound on how much of the spread is source/load rather than
    ADC.

    `discard` reads are thrown away after every settings change: the first
    conversions following a RATE change are invalid. RATE below 16 is not
    offered — 4/8/12 return raw code 0 on both read paths while still
    acknowledging the command.
    """
    ctx.require_adc_rate()
    ctx.dmm.config_vdc(nplc=ctx.nplc, rng=DEFAULT_VRANGE, autozero=False)
    ctx.check_polarity()

    # This is the one routine that mutates the sampling settings, so remember
    # the entry state and put it back — otherwise it silently leaves the device
    # at whatever the last grid row happened to be, and every later run in the
    # session inherits it.
    entry = ctx.link.command("ADC?") or ""
    e_rate = re.search(r"rate=(\d+)", entry)
    e_avg = re.search(r"avg=(\d+)", entry)

    ctx.iset(i_test)
    time.sleep(max(ctx.settle, 2.0))

    f, w, path = open_csv(
        ctx, "noise",
        ["rate", "avg", "t_per_read_s", "koi_mean_V", "koi_sd_V",
         "koi_sd_load_uV", "equiv_current_nA", "dmm_mean_V", "dmm_sd_V", "n"],
        extra=(f"i_test_mA: {i_test}   reads/setting: {nreads}   "
               f"discarded after each change: {discard}",))
    with f:
        for rate, avg in grid:
            if ctx.stopped():
                ctx.emit("aborted")
                break
            if rate < 16:
                ctx.emit(f"skipping RATE {rate}: below the usable minimum of 16")
                continue
            ctx.link.command(f"RATE {rate}")
            ctx.link.command(f"AVG {avg}")
            for _ in range(discard):
                ctx.koi_read()
            t0 = time.time()
            kv = [ctx.koi_read() for _ in range(nreads)]
            tper = (time.time() - t0) / nreads
            kv = [x for x in kv if x == x]
            dv, dsd = ctx.dmm_stats(min(nreads, 20))
            kmean = statistics.fmean(kv) if kv else float("nan")
            ksd = statistics.stdev(kv) if len(kv) > 1 else float("nan")
            sd_load = ksd * 6.0 * 1e6
            w.writerow([rate, avg, f"{tper:.4f}", f"{kmean:.9f}", f"{ksd:.3e}",
                        f"{sd_load:.3f}", f"{sd_load/ctx.r_load*1e3:.3f}",
                        f"{dv:.9f}", f"{dsd:.3e}", len(kv)])
            ctx.emit(f"noise RATE {rate}/AVG {avg}: {tper*1e3:.0f} ms/read, "
                     f"1σ {ksd*1e6:.2f} µV at pin ({sd_load:.1f} µV at load)")

    if e_rate:
        ctx.link.command(f"RATE {e_rate.group(1)}")
    if e_avg:
        ctx.link.command(f"AVG {e_avg.group(1)}")
    ctx.emit(f"restored RATE {e_rate.group(1) if e_rate else '?'} / "
             f"AVG {e_avg.group(1) if e_avg else '?'}")
    ctx.idle()
    summary = f"noise grid done  [{ctx.err_summary()}]"
    ctx.emit(summary)
    return path, summary


# ===========================================================================
# 8. Settling — step response
# ===========================================================================
def meas_settling(ctx, i_from=3.0, i_to=5.0, duration=6.0, nplc=1):
    """Current after a step, sampled as fast as the meter allows. Backs the
    settle time the other routines assume rather than leaving it asserted."""
    ctx.dmm.config_vdc(nplc=nplc, rng=DEFAULT_VRANGE, autozero=False)
    ctx.check_polarity()
    ctx.iset(i_from)
    time.sleep(3.0)

    f, w, path = open_csv(ctx, "settling",
                          ["t_s", "dmm_V", "I_source_mA"],
                          extra=(f"step: {i_from} → {i_to} mA   "
                                 f"duration: {duration} s   nplc: {nplc}",))
    with f:
        t0 = time.time()
        ctx.iset(i_to)
        while time.time() - t0 < duration:
            if ctx.stopped():
                break
            v = ctx.dmm.read()
            t = time.time() - t0
            w.writerow([f"{t:.4f}", f"{v:.9f}", f"{ctx.i_source(v):.9f}"])
            ctx.emit(f"settling {t:.1f}/{duration:.0f} s")
    ctx.idle()
    summary = f"settling {i_from}→{i_to} mA captured  [{ctx.err_summary()}]"
    ctx.emit(summary)
    return path, summary


# ===========================================================================
# Registry — the GUI builds its buttons from this
# ===========================================================================
MEASUREMENTS = [
    ("rload",      "Measure R (4-wire)", meas_rload,
     "Front-ends off, offset-compensated 4-wire ohms. Sets R for everything else."),
    ("set_vs_dmm", "Set vs 2100",        meas_set_vs_dmm,
     "Commanded current vs the 2100, DMM only — pure source accuracy."),
    ("drive",      "Drive sweep",        meas_drive_sweep,
     "Both meters at every setpoint: transfer, readback accuracy, R, precision."),
    ("low_i",      "Low-current sweep",  meas_low_current,
     "Dense points through the 44 mV sense knee, fixed DMM range."),
    ("codes",      "Single-code steps",  meas_codes,
     "±16 codes at 1/3/5 mA: step size, DNL, monotonicity."),
    ("inl",        "INL",                meas_inl,
     "Randomized code order with an interleaved drift reference."),
    ("noise",      "Noise vs RATE/AVG",  meas_noise,
     "ADC noise floor across the sampling grid, 2100 as a bound."),
    ("settling",   "Settling",           meas_settling,
     "Step response, to justify the settle time the sweeps use."),
]
