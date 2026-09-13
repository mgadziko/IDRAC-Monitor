# BlackLotus Fan Control

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

The Windows Forms dashboard refreshes every five seconds.  It is an observer:
it displays GPU temperatures, GPU power, inlet/exhaust temperatures, and all
six fan speeds, but has no fan-control action. If
`BLACKLOTUS_IDRAC_PASSWORD` is not already set for the launch process, it
prompts for the password and retains it only while the dashboard runs.
NVIDIA and iDRAC polling run in a background worker, so the window remains
responsive while a slow IPMI request is in progress.

## What this does not do yet

It never changes iDRAC fan mode or speed.  Fan-control logic is a later phase,
after sample data confirms the sensor identities and expected thermal response.
