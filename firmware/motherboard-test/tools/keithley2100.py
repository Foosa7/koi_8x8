#!/usr/bin/env python3
"""
keithley2100.py — minimal SCPI driver for the Keithley 2100 6.5-digit DMM
over the Linux kernel's USBTMC character device (/dev/usbtmc*).

No pyvisa / python-usbtmc needed: the usbtmc kernel driver already speaks
the bulk-transfer protocol, so a SCPI session is just read()/write() on the
device node.

Access requires the device node to be readable by your user. If you get
PermissionError on /dev/usbtmc1, install a udev rule (see setup_usbtmc.md).

Typical use — DC volts, 10 NPLC, autorange off on the 10 V range:

    with Keithley2100() as dmm:
        print(dmm.idn())
        dmm.config_vdc(nplc=10, rng=10)
        print(dmm.read())          # volts, float

The 2100's own accuracy on DCV 10 V / 10 NPLC is ~35 ppm of reading +
6 ppm of range (1 yr), i.e. ~90 uV at 1 V — an order of magnitude below
the ~3.4 mV measure-path offset this rig is chasing, so it is a valid
reference for that work.

Speed and gotchas (all bench-measured; see docs/characterization.md §5B):

  * Use NPLC 1 with autozero OFF. One reading gives 0.1 LSB of a 98 uV step at
    ~40 readings/s — about 80x faster than 10 reads at NPLC 10 for the same
    precision. NPLC 10 is 10x slower for identical noise.
  * THE 2100 SILENTLY COERCES NPLC, with no SCPI error. Requested -> reported:
    0.02->1, 0.06->10, 0.2->10, 1->1, 10->10, 100->10. Measured throughput did
    not always match the readback either, so trust the achieved rate over both
    the request and the query.
  * Use read_burst() rather than n calls to read(): at low NPLC the USB
    round-trip, not integration, limits throughput.
  * Fix the range (rng=) for offset/accuracy work. Autoranging splits a sweep
    across two range calibrations and the difference lands in the fitted
    intercept.
  * The USBTMC interface stalls roughly once per few hundred readings. A device
    CLEAR is not always enough; reopen() recovers. Every read path needs this —
    see query() and read_burst().
"""

import fcntl
import glob
import os
import struct
import time

# ioctl codes from <linux/usb/tmc.h>, built the same way the _IO* macros do it
# rather than hardcoded, because the nr values are easy to get wrong (nr 9 is
# GET_TIMEOUT/_IOR, nr 10 is SET_TIMEOUT/_IOW).
_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction, nr, size, typ=91):        # USBTMC_IOC_NR = 91
    return (direction << 30) | (size << 16) | (typ << 8) | nr


_USBTMC_IOCTL_CLEAR = _ioc(0, 2, 0)                  # _IO (91, 2)
_USBTMC_IOCTL_GET_TIMEOUT = _ioc(_IOC_READ, 9, 4)    # _IOR(91, 9, __u32)
_USBTMC_IOCTL_SET_TIMEOUT = _ioc(_IOC_WRITE, 10, 4)  # _IOW(91, 10, __u32)

DEFAULT_TIMEOUT_MS = 10000

# NPLC values the 2100 accepts (50 Hz mains -> 100 NPLC = 2 s/reading).
VALID_NPLC = (0.02, 0.2, 1, 10, 100)


class Keithley2100:
    """SCPI session to a Keithley 2100 on a USBTMC device node."""

    def __init__(self, path=None, timeout_ms=DEFAULT_TIMEOUT_MS):
        self._timeout_ms = timeout_ms
        # Replayed after a reopen so a recovered session keeps its measurement
        # setup instead of silently reverting to the power-on default.
        self._config = None
        self._recovering = False
        self.path = path or autodetect_usbtmc()
        if not self.path:
            raise RuntimeError(
                "no /dev/usbtmc* device found — is the 2100 plugged in and "
                "the usbtmc kernel module loaded?"
            )
        try:
            self.fd = os.open(self.path, os.O_RDWR)
        except PermissionError as exc:
            raise PermissionError(
                f"{self.path}: {exc.strerror}. Install the udev rule from "
                "tools/setup_usbtmc.md so your user can open it."
            ) from exc
        self.set_timeout(timeout_ms)
        # A previous session that died mid-transfer can leave the instrument
        # holding an unread reply; clear it so our first query isn't off by one.
        try:
            self.clear()
        except OSError:
            pass

    # ---------------------------------------------------------------- io ---

    def set_timeout(self, ms):
        """Set the USBTMC bulk-read timeout. Long NPLC needs a long one."""
        fcntl.ioctl(self.fd, _USBTMC_IOCTL_SET_TIMEOUT, struct.pack("I", int(ms)))

    def get_timeout(self):
        buf = fcntl.ioctl(self.fd, _USBTMC_IOCTL_GET_TIMEOUT, struct.pack("I", 0))
        return struct.unpack("I", buf)[0]

    def clear(self):
        """USBTMC device clear — flushes the instrument's I/O buffers.

        Use this to recover a session left mid-transfer (e.g. after a timeout);
        it is the bus-level reset, independent of SCPI's *CLS.
        """
        fcntl.ioctl(self.fd, _USBTMC_IOCTL_CLEAR)

    def write(self, cmd):
        os.write(self.fd, (cmd.strip() + "\n").encode("ascii"))

    def read_raw(self, nbytes=4096):
        return os.read(self.fd, nbytes)

    def read_values(self, nvalues, nbytes=1 << 16, max_chunks=4096):
        """Read a comma-separated reply until `nvalues` values have arrived.

        The 2100 returns a multi-reading burst as SEVERAL USBTMC messages, so a
        single os.read() yields only the first slice — the earlier symptom was
        asking for 100 readings and getting 16, truncated mid-stream. Nor can
        completion be detected from the data: replies carry no trailing newline
        (USBTMC marks end-of-message out of band) and a short read does not imply
        the last message. The only reliable termination is counting values.

        Leaving the remainder unread is not harmless: it stays in the
        instrument's output buffer and the next session parses it as commands,
        which is what produced the spurious -108 "Parameter not allowed".
        """
        buf = b""
        for _ in range(max_chunks):
            try:
                buf += os.read(self.fd, nbytes)
            except TimeoutError:
                # Fewer values than asked for (aborted burst, overload). Hand
                # back what arrived so the caller can see the short count,
                # rather than raising and losing it.
                if buf:
                    break
                raise
            if buf.count(b",") + 1 >= nvalues:
                break
        return buf

    def reopen(self):
        """Close and reopen the device node, then replay the measurement config.

        The 2100's USBTMC interface occasionally stalls under sustained polling
        in a way a device CLEAR does not clear; only a fresh file descriptor
        recovers it. Observed roughly once per few hundred readings.
        """
        try:
            self.close()
        except OSError:
            pass
        time.sleep(0.5)
        self.path = self.path or autodetect_usbtmc()
        self.fd = os.open(self.path, os.O_RDWR)
        self.set_timeout(self._timeout_ms)
        try:
            self.clear()
        except OSError:
            pass
        # Recovering re-issues commands while a READ? may still be pending, so
        # the instrument logs -113 "Undefined header" for the garbled fragment.
        # Those are artifacts of the recovery itself and predate anything the
        # caller measures next — drop them here so a later errors() check
        # reports only genuine acquisition problems.
        try:
            self.write("*CLS")
        except OSError:
            pass
        # Guard: the config helpers themselves issue queries, and a failure
        # inside one must not recurse back into reopen().
        if self._config and not self._recovering:
            self._recovering = True
            try:
                fn, kw = self._config
                fn(**kw)
            finally:
                self._recovering = False

    def query(self, cmd, nbytes=4096, retries=3):
        """Send a query and return its reply, recovering from a stalled read.

        A USBTMC bulk read that times out leaves the session mid-transfer, and
        every subsequent query then reads the previous reply — silently shifting
        the data by one point. So a timeout is never passed through: first try a
        device CLEAR and resend, and if that does not take, reopen the device
        entirely (see reopen()).
        """
        for attempt in range(1 + retries):
            try:
                self.write(cmd)
                return self.read_raw(nbytes).decode("ascii", errors="replace").strip()
            except (TimeoutError, OSError):
                if attempt >= retries:
                    raise
                try:
                    if attempt == 0:
                        self.clear()
                    else:
                        self.reopen()
                except OSError:
                    pass
                time.sleep(0.3 * (attempt + 1))

    def close(self):
        if getattr(self, "fd", None) is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ----------------------------------------------------------- session ---

    def idn(self):
        return self.query("*IDN?")

    def reset(self):
        """*RST then *CLS, and wait for the reset to actually complete."""
        self.write("*RST")
        self.write("*CLS")
        self.query("*OPC?")

    def errors(self):
        """Drain the SCPI error queue. Returns a list of 'code,\"text\"' strings."""
        out = []
        for _ in range(20):
            err = self.query("SYST:ERR?")
            if not err or err.startswith("+0,") or err.startswith("0,"):
                break
            out.append(err)
        return out

    # ------------------------------------------------------------ config ---

    def config_vdc(self, nplc=10, rng=None, autozero=True, high_z=True):
        """DC volts. rng=None -> autorange; else a fixed range in volts.

        high_z puts the 100 mV / 1 V / 10 V ranges at >10 GOhm input impedance
        instead of the default 10 MOhm, so the meter stops loading the node
        it is measuring. Leave it on for anything high-impedance.
        """
        self._config = (self.config_vdc, dict(nplc=nplc, rng=rng,
                                              autozero=autozero, high_z=high_z))
        self.write("CONF:VOLT:DC")
        if rng is None:
            self.write("SENS:VOLT:DC:RANG:AUTO ON")
        else:
            self.write("SENS:VOLT:DC:RANG:AUTO OFF")
            self.write(f"SENS:VOLT:DC:RANG {rng:g}")
        self._set_nplc("VOLT:DC", nplc)
        self.write(f"SENS:ZERO:AUTO {'ON' if autozero else 'OFF'}")
        self.write(f"INP:IMP:AUTO {'ON' if high_z else 'OFF'}")
        self.write("TRIG:SOUR IMM")
        self.query("*OPC?")

    def config_idc(self, nplc=10, rng=None, autozero=True):
        """DC amps (use the meter's I terminals — burden voltage applies)."""
        self.write("CONF:CURR:DC")
        if rng is None:
            self.write("SENS:CURR:DC:RANG:AUTO ON")
        else:
            self.write("SENS:CURR:DC:RANG:AUTO OFF")
            self.write(f"SENS:CURR:DC:RANG {rng:g}")
        self._set_nplc("CURR:DC", nplc)
        self.write(f"SENS:ZERO:AUTO {'ON' if autozero else 'OFF'}")
        self.write("TRIG:SOUR IMM")
        self.query("*OPC?")

    def config_fres(self, nplc=10, rng=None):
        """4-wire resistance."""
        self.write("CONF:FRES")
        if rng is None:
            self.write("SENS:FRES:RANG:AUTO ON")
        else:
            self.write("SENS:FRES:RANG:AUTO OFF")
            self.write(f"SENS:FRES:RANG {rng:g}")
        self._set_nplc("FRES", nplc)
        self.write("TRIG:SOUR IMM")
        self.query("*OPC?")

    def _set_nplc(self, func, nplc):
        if nplc not in VALID_NPLC:
            raise ValueError(f"NPLC {nplc} not in {VALID_NPLC}")
        self.write(f"SENS:{func}:NPLC {nplc:g}")

    # ----------------------------------------------------------- reading ---

    def read(self):
        """One triggered reading, as a float."""
        return float(self.query("READ?"))

    def read_burst(self, n, nplc=None):
        """Take n readings into the instrument's buffer and fetch them in ONE
        transfer. Far faster than n round-trips of read() — at low NPLC the USB
        round-trip, not the integration time, is what limits read() throughput.

        The 2100's reading buffer holds 2000; larger n is chunked. The read
        timeout is widened to cover n integrations plus overhead, then restored.
        """
        if n > 2000:
            out = []
            while n > 0:
                take = min(n, 2000)
                out += self.read_burst(take, nplc)
                n -= take
            return out
        # NPLC is per power-line cycle: 50 Hz -> 20 ms each, plus autozero.
        secs = n * (nplc or 10) * 0.02 * 2.5 + 5.0
        old = self.get_timeout()
        self.set_timeout(int(secs * 1000))
        try:
            # Same stall recovery as query() — read_values() is called directly
            # here, so it does not otherwise get the retry/reopen path.
            txt = ""
            for attempt in range(3):
                try:
                    self.write("TRIG:COUN 1")
                    self.write(f"SAMP:COUN {n}")
                    self.write("READ?")
                    txt = self.read_values(n).decode("ascii", errors="replace")
                    break
                except (TimeoutError, OSError):
                    if attempt == 2:
                        raise
                    try:
                        self.clear() if attempt == 0 else self.reopen()
                    except OSError:
                        pass
                    self.set_timeout(int(secs * 1000))
                    time.sleep(0.3 * (attempt + 1))
        finally:
            self.set_timeout(old)
            self.write("SAMP:COUN 1")
        vals = [float(x) for x in txt.split(",") if x.strip()]
        if len(vals) < n:
            # A short read leaves the remainder in the instrument's output
            # buffer, and the next session parses those digits as commands —
            # which is exactly what produced the stray -113 "Undefined header".
            # Flush it so the residue cannot leak into later measurements.
            try:
                self.clear()
            except OSError:
                pass
        return vals

    def read_n(self, n, settle=0.0):
        """n readings; returns the list. `settle` sleeps between them."""
        vals = []
        for i in range(n):
            if i and settle:
                time.sleep(settle)
            vals.append(self.read())
        return vals

    def read_stats(self, n, settle=0.0):
        """(mean, stdev, list) over n readings — stdev is the sample stdev."""
        vals = self.read_n(n, settle)
        mean = sum(vals) / len(vals)
        if len(vals) < 2:
            return mean, 0.0, vals
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        return mean, var ** 0.5, vals


def autodetect_usbtmc(vid="05e6", pid="2100"):
    """Return the /dev/usbtmc* node for a matching instrument, else the first one.

    Matching walks sysfs so a second USBTMC instrument on the bus doesn't get
    picked by accident.
    """
    nodes = sorted(glob.glob("/dev/usbtmc*"))
    if not nodes:
        return None
    for node in nodes:
        name = os.path.basename(node)
        sysdir = f"/sys/class/usbmisc/{name}/device"
        try:
            # .../usbtmcN/device is the USB *interface*; VID/PID live on its parent.
            usbdev = os.path.dirname(os.path.realpath(sysdir))
            with open(os.path.join(usbdev, "idVendor")) as f:
                v = f.read().strip()
            with open(os.path.join(usbdev, "idProduct")) as f:
                p = f.read().strip()
        except OSError:
            continue
        if v.lower() == vid.lower() and p.lower() == pid.lower():
            return node
    return nodes[0]


if __name__ == "__main__":
    # Smoke test: identify, configure, and take a few readings.
    path = autodetect_usbtmc()
    print(f"device: {path}")
    with Keithley2100(path) as dmm:
        print(f"*IDN? : {dmm.idn()}")
        dmm.reset()
        dmm.config_vdc(nplc=10, rng=None)
        mean, sd, vals = dmm.read_stats(5)
        print(f"DCV   : {mean:+.7f} V   stdev {sd*1e6:.2f} uV   n=5")
        print(f"        {['%+.7f' % v for v in vals]}")
        errs = dmm.errors()
        print(f"errors: {errs or 'none'}")
