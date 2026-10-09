# Thermal Monitor

Thermal Monitor is a desktop utility for Dell iDRAC-equipped GPU servers. It
combines GPU-local NVIDIA telemetry with Dell BMC sensor data and, when the
operator explicitly enables it, can apply a GPU-temperature fan curve through
iDRAC.

The repository contains two independent desktop implementations:

| Platform | Implementation | Primary use |
| --- | --- | --- |
| Windows | Windows Forms application and PowerShell tools | Comparable Windows hosts and legacy BlackLotus builds |
| Linux | GTK4 / Python application in [`linux/`](linux/) | WhiteLotus, BlackLotus, and comparable Zorin/Ubuntu hosts |

The implementations share the same safety model, but are not interchangeable:
use the platform-specific installation and operating instructions below.

## What it does

- Reads NVIDIA GPU telemetry locally through `nvidia-smi`. The Linux app shows
  temperature, power draw and limit, utilization, and VRAM use; the Windows
  application shows GPU temperature and power alongside chassis readings.
- Reads iDRAC fan and thermal sensors through `ipmitool` using RMCP+.
- Shows GPU, CPU/chassis, and fan data in a desktop dashboard without blocking
  the user interface during a slow BMC request.
- Persists connection settings and the chosen fan curve locally. Passwords
  are not written to configuration files or passed on command lines.
- Provides an opt-in fan curve based on the hottest GPU temperature. Fan
  control is not activated merely by launching the app or reading telemetry.

`nvidia-smi` is the authoritative source for GPU-local temperature and power.
iDRAC readings are chassis/BMC sensor values and must not be relabeled as GPU
temperatures.

## Safety and fan control

Reading telemetry is non-destructive. Starting fan control is different: it
sends Dell raw IPMI commands that place iDRAC in manual fan mode and set all
fan zones to the calculated duty. Use it only after reviewing the displayed
GPU temperatures, BMC connection, and curve.

The controller raises the target promptly as the hottest GPU crosses a tier.
It lowers a tier only after three cool samples at least 4 °C below the prior
tier threshold, avoiding rapid fan oscillation.

When either desktop application is closed normally, it explicitly asks whether
to **Restore iDRAC Automatic Control**, **Keep current manual fan speed**, or
**Don't quit**. Restore sends Dell's iDRAC automatic-control command and the
application stays open if that command fails; it never silently exits after a
failed restore. **Stop (leave speed)** still stops only further curve updates
and retains the manual duty. A crash or forced termination cannot show the
dialog and may likewise leave iDRAC in manual mode.

## Windows

The Windows application and support tools live at the repository root:

- `src/Program.cs` — Windows Forms dashboard and fan-controller application.
- `scripts/Collect-Thermals.ps1` — one JSON telemetry sample.
- `scripts/Start-ThermalDashboard.ps1` — PowerShell monitoring dashboard.
- `installer/` — self-contained installer, bundled `ipmitool`, and optional
  token-protected GPU telemetry service.

The dashboard refreshes GPU and iDRAC data in the background, displays GPU
temperature/power, inlet/exhaust, and fan speeds, and stores the entered iDRAC
password encrypted with Windows DPAPI for the current Windows user when that
option is selected.

For installation instructions, see [installer/README.md](installer/README.md).
For an ad-hoc telemetry sample on the established BlackLotus layout:

```powershell
$env:BLACKLOTUS_IDRAC_PASSWORD = '<iDRAC password>'
.\scripts\Collect-Thermals.ps1
```

Do not place the password in a script, command history, or this repository.

## Linux

The Linux port is a separate GTK4 application in [`linux/`](linux/), developed
without changing the Windows implementation. It is deployed on WhiteLotus
(Zorin OS 18 with Tesla P40 GPUs) and BlackLotus (Zorin OS with Tesla M40
GPUs), and is designed for Linux hosts with:

- an NVIDIA driver that provides `nvidia-smi`;
- `ipmitool` for BMC access;
- GTK 4 and PyGObject; and
- Secret Service / libsecret introspection when secure password storage is
  desired.

The Linux dashboard refreshes GPU telemetry and active fan-curve decisions
every 10 seconds. BMC reads use a separate 30-second cadence and progressively
back off after errors such as RMCP+ session exhaustion; **Refresh now** bypasses
that backoff. GPU polling remains independent while a BMC request is in
progress.

It shows per-GPU temperature, power, utilization, and VRAM bars; dedicated
CPU1/CPU2, inlet, and exhaust temperature bars; and a six-fan RPM panel. Its
eight default curve points are:

```text
0 °C → 3000 RPM    60 °C → 4000 RPM    65 °C → 5000 RPM    70 °C → 6000 RPM
75 °C → 7000 RPM   80 °C → 8000 RPM    85 °C → 8700 RPM    90 °C → 9360 RPM
```

The app stores its non-secret settings in a user-only file and can save the
BMC password in the signed-in desktop's Secret Service keyring. It offers a
per-user desktop launcher and optional desktop-login start. On X11 it also
remembers the window position; Wayland intentionally does not expose that
control to GTK applications.

### Install and run on Linux

From the `linux/` directory:

```sh
./install.sh
```

The installer verifies its GTK, Secret Service, `nvidia-smi`, and `ipmitool`
requirements, copies the app to `~/.local/share/thermal-monitor`, and creates
a launcher under `~/.local/share/applications`. It requires no root access
and does not install system packages.

### Deployed Linux targets

- **WhiteLotus:** `~/.local/share/thermal-monitor`
- **BlackLotus:** `/mnt/ssd894/ThermalMonitor`, with
  `~/.local/share/thermal-monitor` linked to that SSD-backed copy

Both deployments use the same explicit quit decision. Choose **Restore iDRAC
Automatic Control** to return fan policy to iDRAC, **Keep current manual fan
speed** to retain the last duty, or **Don't quit** to leave the application
open. The restore path is confirmed before exit and keeps the application open
if iDRAC rejects the command.

For development or a direct launch:

```sh
python3 thermal_monitor.py
```

See [linux/README.md](linux/README.md) for the full Linux UI, credential,
fan-control, and development-check documentation.

## Development checks

Run the Linux parser and fan-curve tests from `linux/`:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile monitor_core.py thermal_monitor.py credential_store.py
```

The test suite exercises GPU/IPMI parsing, fan-curve validation and mapping,
fan-control hysteresis helpers, and the Dell R730 CPU sensor identification
used by the Linux dashboard.
