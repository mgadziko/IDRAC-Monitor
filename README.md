# Thermal Monitor

The first milestone is deliberately monitor-only.  It samples both NVIDIA Tesla
M40 GPU temperatures through `nvidia-smi`, and reads fan plus thermal sensors
from the Dell R730 iDRAC through the local `ipmitool.exe` distribution.  It
does not enable manual fan control or send any fan-speed command.

## Prerequisites

- Run on BlackLotus, where NVIDIA's driver and `nvidia-smi` are installed.
- Set `BLACKLOTUS_IDRAC_PASSWORD` for the current user or service account.
- Leave `ipmitool.exe` in its established location:
  `C:\ipmitool\192.168.4.120\ipmitool.exe`.

The password is intentionally not accepted as a command-line argument and
must never be committed to this repository.

## Run

```powershell
$env:BLACKLOTUS_IDRAC_PASSWORD = '<iDRAC password>'
.\scripts\Collect-Thermals.ps1
```

Each invocation writes one JSON object to `logs\thermal-samples.jsonl` and
also prints it to the console.  The result contains each GPU's temperature and
the iDRAC fan, inlet, exhaust, and generic temperature sensors.

## Live dashboard

```powershell
.\scripts\Start-ThermalDashboard.ps1
```

The Windows Forms dashboard refreshes every five seconds and can apply a
six-tier GPU-temperature fan curve. It displays GPU temperatures, GPU power,
inlet/exhaust temperatures, and all six fan speeds. NVIDIA and iDRAC polling
run in a background worker, so the window remains responsive while a slow IPMI
request is in progress.

Use **Apply Settings** after selecting either startup option. **Launch app on
system startup** registers the app for the current Windows user's next sign-in;
Windows cannot display a desktop GUI before a user session exists. **Start
Automatic Control on app launch** then applies the saved fan curve without a
confirmation dialog.

The app encrypts the saved iDRAC password with Windows DPAPI for that Windows
user. It is never stored in plaintext, passed on the command line, or committed
to this repository. If the app is moved to a different Windows user or machine,
enter the password once again and apply the settings there.

## What this does not do yet

It does not replace iDRAC's safety protections; use **Restore Default Behavior**
to return fan control to iDRAC.
