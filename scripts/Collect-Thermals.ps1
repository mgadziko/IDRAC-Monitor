[CmdletBinding()]
param(
    [string] $IdracHost = '192.168.4.120',
    [string] $IdracUser = 'root',
    [string] $IpmiToolPath = 'C:\ipmitool\192.168.4.120\ipmitool.exe',
    [string] $LogDirectory = (Join-Path $PSScriptRoot '..\logs')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NvidiaGpuSamples {
    $raw = & nvidia-smi --query-gpu=index,name,uuid,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "nvidia-smi failed: $raw"
    }

    $samples = foreach ($line in $raw) {
        $parts = $line -split ',\s*'
        if ($parts.Count -ne 6) {
            throw "Unexpected nvidia-smi output: $line"
        }
        [ordered]@{
            index = [int] $parts[0]
            name = $parts[1]
            uuid = $parts[2]
            temperatureC = [int] $parts[3]
            powerDrawW = [double] $parts[4]
            powerLimitW = [double] $parts[5]
        }
    }

    if (-not $samples) {
        throw 'nvidia-smi returned no GPUs.'
    }
    return @($samples)
}

function Get-IdracSensors {
    if (-not (Test-Path -LiteralPath $IpmiToolPath -PathType Leaf)) {
        throw "ipmitool was not found at $IpmiToolPath"
    }

    $raw = & $IpmiToolPath -I lanplus -H $IdracHost -U $IdracUser -E sdr elist full 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "ipmitool sensor query failed: $raw"
    }

    $sensors = foreach ($line in $raw) {
        if ($line -notmatch '^\s*(?<name>[^|]+?)\s*\|\s*(?<id>[^|]+?)\s*\|\s*(?<status>[^|]+?)\s*\|\s*(?<location>[^|]+?)\s*\|\s*(?<reading>.+?)\s*$') {
            continue
        }
        [ordered]@{
            name = $Matches.name.Trim()
            id = $Matches.id.Trim()
            status = $Matches.status.Trim()
            location = $Matches.location.Trim()
            reading = $Matches.reading.Trim()
        }
    }

    return @($sensors)
}

if ([string]::IsNullOrWhiteSpace($env:BLACKLOTUS_IDRAC_PASSWORD)) {
    throw 'Set BLACKLOTUS_IDRAC_PASSWORD before running this monitoring-only collector.'
}

# ipmitool -E reads the password from IPMI_PASSWORD, avoiding a password in the
# process command line.  Restore any prior value after the subprocess returns.
$priorIpmiPassword = $env:IPMI_PASSWORD
try {
    $env:IPMI_PASSWORD = $env:BLACKLOTUS_IDRAC_PASSWORD
    $gpus = Get-NvidiaGpuSamples
    $idracSensors = Get-IdracSensors
}
finally {
    $env:IPMI_PASSWORD = $priorIpmiPassword
}

$sample = [ordered]@{
    timestampUtc = (Get-Date).ToUniversalTime().ToString('o')
    source = 'BlackLotus monitor-only thermal collector'
    gpus = $gpus
    idrac = [ordered]@{
        host = $IdracHost
        fans = @($idracSensors | Where-Object { $_.name -match '^Fan\d+$' })
        temperatures = @($idracSensors | Where-Object { $_.reading -match 'degrees C$' })
    }
}

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$logPath = Join-Path $LogDirectory 'thermal-samples.jsonl'
$json = $sample | ConvertTo-Json -Depth 5 -Compress
Add-Content -LiteralPath $logPath -Value $json -Encoding utf8
$json
