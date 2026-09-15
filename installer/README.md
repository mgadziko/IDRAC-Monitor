# Thermal Monitor installer

Run PowerShell as Administrator and execute:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
.\Install-ThermalMonitor.ps1 -IdracIp 192.168.4.120
```

This installs the GUI to `C:\ThermalMonitor`, installs the bundled `ipmitool` runtime to `C:\ipmitool\<iDRAC IP>`, and creates a Desktop shortcut. It never packages, saves, or transmits an iDRAC password.

To install the optional, token-protected GPU telemetry service on port 11436:

```powershell
.\Install-ThermalMonitor.ps1 -IdracIp 192.168.4.120 -InstallTelemetry
```

The host needs Windows 10/11 with .NET Framework 4.x and an NVIDIA driver that supplies `nvidia-smi.exe`.
