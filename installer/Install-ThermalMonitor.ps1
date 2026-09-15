param(
    [string] $IdracIp = '192.168.4.120',
    [switch] $InstallTelemetry
)

$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSCommandPath
$appRoot = 'C:\ThermalMonitor'
$ipmiRoot = Join-Path 'C:\ipmitool' $IdracIp

New-Item -ItemType Directory -Force -Path $appRoot, $ipmiRoot | Out-Null
Copy-Item (Join-Path $packageRoot 'app\ThermalMonitor.exe') (Join-Path $appRoot 'ThermalMonitor.exe') -Force
Copy-Item (Join-Path $packageRoot 'ipmitool\*') $ipmiRoot -Force

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Thermal Monitor.lnk'))
$shortcut.TargetPath = Join-Path $appRoot 'ThermalMonitor.exe'
$shortcut.WorkingDirectory = $appRoot
$shortcut.Save()

if ($InstallTelemetry) {
    $telemetryRoot = 'C:\ProgramData\ThermalMonitor'
    New-Item -ItemType Directory -Force -Path $telemetryRoot | Out-Null
    Copy-Item (Join-Path $packageRoot 'service\GpuTelemetry.ps1') (Join-Path $telemetryRoot 'GpuTelemetry.ps1') -Force
    Set-Content -LiteralPath (Join-Path $telemetryRoot 'token.txt') -Value ([guid]::NewGuid().ToString('N')) -NoNewline
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\ThermalMonitor\GpuTelemetry.ps1'
    Register-ScheduledTask -TaskName 'ThermalMonitorGpuTelemetry' -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName 'ThermalMonitorGpuTelemetry'
    if (-not (Get-NetFirewallRule -Name 'ThermalMonitorGpuTelemetry' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'ThermalMonitorGpuTelemetry' -DisplayName 'Thermal Monitor GPU Telemetry' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 11436 | Out-Null
    }
}

Write-Host "Installed Thermal Monitor to $appRoot; ipmitool is configured at $ipmiRoot."
