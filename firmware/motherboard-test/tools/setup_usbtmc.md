# USBTMC instrument access (Keithley 2100)

The Keithley 2100 enumerates as `05e6:2100` and the kernel's `usbtmc` driver
gives it a `/dev/usbtmc*` node. That node is created `root:root 0600` by
default, so a plain user can't open it. One udev rule fixes it permanently.

## Install the rule (one time, needs sudo)

```sh
sudo tee /etc/udev/rules.d/99-usbtmc.rules >/dev/null <<'EOF'
# USBTMC test instruments — give plugdev users raw access to /dev/usbtmc*
SUBSYSTEM=="usbmisc", KERNEL=="usbtmc[0-9]*", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="05e6", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usbmisc
```

`foosa` is already in `plugdev`, so no re-login is needed. Verify:

```sh
ls -l /dev/usbtmc*        # want  crw-rw---- root plugdev
```

If the node still shows `root root`, unplug and replug the 2100 — `udevadm
trigger` doesn't always re-run permissions on an already-attached device.

## Smoke test

Run in the jax venv (that's where the project's python deps live):

```sh
source /home/foosa/jax-env/jax_env/bin/activate
python tools/keithley2100.py
```

Expect an `*IDN?` line like `KEITHLEY INSTRUMENTS INC.,MODEL 2100,<serial>,<fw>`
followed by five DC-volt readings and their spread.

## Notes

- The node number is not stable across replugs (it was `/dev/usbtmc1` on
  2026-07-29, not `usbtmc0`). `autodetect_usbtmc()` matches on VID/PID via
  sysfs rather than assuming a number.
- `Keithley2100.set_timeout()` raises the USBTMC bulk-read timeout; the default
  used here is 10 s, which covers 100 NPLC (2 s/reading at 50 Hz) with margin.
- Always `config_vdc(high_z=True)` when probing a high-impedance node — the
  2100 defaults to a 10 MOhm input on the <=10 V ranges, which loads the node.
- The interface **stalls roughly once per few hundred readings**. A device CLEAR
  is not always enough; `reopen()` closes and reopens the node and replays the
  measurement config. This is wired into both `query()` and `read_burst()`.
- For speed, NPLC coercion, and the burst-read protocol traps, see
  `docs/characterization.md` §5B — the short version is **NPLC 1 with autozero
  off**, and **never trust a requested NPLC without measuring the achieved rate**.
