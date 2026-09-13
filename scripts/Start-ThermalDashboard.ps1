[CmdletBinding()]
param(
    [int] $RefreshSeconds = 5,
    [string] $IdracHost = '192.168.4.120',
    [string] $IdracUser = 'root',
    [string] $IpmiToolPath = 'C:\ipmitool\192.168.4.120\ipmitool.exe'
)

$dashboardLogDirectory = Join-Path $PSScriptRoot '..\logs'
$dashboardErrorLog = Join-Path $dashboardLogDirectory 'dashboard-startup-error.log'
trap {
    $details = "[$((Get-Date).ToUniversalTime().ToString('o'))]`r`n$($_ | Out-String)"
    try {
        New-Item -ItemType Directory -Force -Path $dashboardLogDirectory | Out-Null
        Set-Content -LiteralPath $dashboardErrorLog -Value $details -Encoding utf8
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "The Thermal Monitor could not start. Details were saved to:`r`n$dashboardErrorLog`r`n`r`n$($_.Exception.Message)",
            'BlackLotus Thermal Monitor',
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
    catch { }
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($RefreshSeconds -lt 2) {
    throw 'RefreshSeconds must be at least 2.'
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$collectorPath = Join-Path $PSScriptRoot 'Collect-Thermals.ps1'
if (-not (Test-Path -LiteralPath $collectorPath -PathType Leaf)) {
    throw "Collector not found at $collectorPath"
}

function New-ValueLabel {
    param([int] $X, [int] $Y, [int] $Width = 190)
    $label = New-Object System.Windows.Forms.Label
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size($Width, 30)
    $label.Font = New-Object System.Drawing.Font('Segoe UI', 15, [System.Drawing.FontStyle]::Bold)
    $label.Text = '--'
    return $label
}

function New-CaptionLabel {
    param([string] $Text, [int] $X, [int] $Y, [int] $Width = 190)
    $label = New-Object System.Windows.Forms.Label
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size($Width, 20)
    $label.Font = New-Object System.Drawing.Font('Segoe UI', 9)
    $label.ForeColor = [System.Drawing.Color]::DimGray
    $label.Text = $Text
    return $label
}

function Get-TemperatureColor {
    param([int] $TemperatureC)
    if ($TemperatureC -ge 90) { return [System.Drawing.Color]::Firebrick }
    if ($TemperatureC -ge 80) { return [System.Drawing.Color]::DarkOrange }
    if ($TemperatureC -ge 70) { return [System.Drawing.Color]::Goldenrod }
    return [System.Drawing.Color]::ForestGreen
}

function Request-IdracPassword {
    if (-not [string]::IsNullOrWhiteSpace($env:BLACKLOTUS_IDRAC_PASSWORD)) {
        return $env:BLACKLOTUS_IDRAC_PASSWORD
    }

    $dialog = New-Object System.Windows.Forms.Form
    $dialog.Text = 'iDRAC credentials required'
    $dialog.ClientSize = New-Object System.Drawing.Size(385, 145)
    $dialog.StartPosition = 'CenterScreen'
    $dialog.FormBorderStyle = 'FixedDialog'
    $dialog.MaximizeBox = $false
    $dialog.MinimizeBox = $false

    $prompt = New-CaptionLabel -Text "Enter the password for $IdracUser@$IdracHost. It is retained only by this running app." -X 18 -Y 16 -Width 348
    $dialog.Controls.Add($prompt)
    $input = New-Object System.Windows.Forms.TextBox
    $input.Location = New-Object System.Drawing.Point(20, 53)
    $input.Size = New-Object System.Drawing.Size(345, 24)
    $input.UseSystemPasswordChar = $true
    $dialog.Controls.Add($input)
    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = 'Connect'
    $ok.Location = New-Object System.Drawing.Point(205, 98)
    $ok.Size = New-Object System.Drawing.Size(75, 27)
    $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $dialog.AcceptButton = $ok
    $dialog.Controls.Add($ok)
    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = 'Cancel'
    $cancel.Location = New-Object System.Drawing.Point(290, 98)
    $cancel.Size = New-Object System.Drawing.Size(75, 27)
    $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $dialog.CancelButton = $cancel
    $dialog.Controls.Add($cancel)

    $result = $dialog.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK -or [string]::IsNullOrWhiteSpace($input.Text)) {
        throw 'No iDRAC password was supplied.'
    }
    return $input.Text
}

$env:BLACKLOTUS_IDRAC_PASSWORD = Request-IdracPassword

$form = New-Object System.Windows.Forms.Form
$form.Text = 'BlackLotus Thermal Monitor'
$form.ClientSize = New-Object System.Drawing.Size(720, 520)
$form.MinimumSize = New-Object System.Drawing.Size(736, 558)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)

$title = New-Object System.Windows.Forms.Label
$title.Location = New-Object System.Drawing.Point(18, 14)
$title.Size = New-Object System.Drawing.Size(680, 28)
$title.Font = New-Object System.Drawing.Font('Segoe UI', 16, [System.Drawing.FontStyle]::Bold)
$title.Text = 'BlackLotus Thermal Monitor'
$form.Controls.Add($title)

$subtitle = New-CaptionLabel -Text "Monitor only • iDRAC $IdracHost • refreshes every $RefreshSeconds seconds" -X 20 -Y 45 -Width 670
$form.Controls.Add($subtitle)

$gpu0Caption = New-CaptionLabel -Text 'GPU 0 temperature' -X 22 -Y 86
$gpu0Value = New-ValueLabel -X 22 -Y 106
$gpu0Detail = New-CaptionLabel -Text 'Waiting for NVIDIA telemetry' -X 22 -Y 140
$gpu1Caption = New-CaptionLabel -Text 'GPU 1 temperature' -X 242 -Y 86
$gpu1Value = New-ValueLabel -X 242 -Y 106
$gpu1Detail = New-CaptionLabel -Text 'Waiting for NVIDIA telemetry' -X 242 -Y 140
$inletCaption = New-CaptionLabel -Text 'iDRAC inlet' -X 462 -Y 86
$inletValue = New-ValueLabel -X 462 -Y 106
$exhaustCaption = New-CaptionLabel -Text 'iDRAC exhaust' -X 462 -Y 164
$exhaustValue = New-ValueLabel -X 462 -Y 184
foreach ($control in @($gpu0Caption, $gpu0Value, $gpu0Detail, $gpu1Caption, $gpu1Value, $gpu1Detail, $inletCaption, $inletValue, $exhaustCaption, $exhaustValue)) {
    $form.Controls.Add($control)
}

$fanTitle = New-Object System.Windows.Forms.Label
$fanTitle.Location = New-Object System.Drawing.Point(22, 230)
$fanTitle.Size = New-Object System.Drawing.Size(220, 22)
$fanTitle.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$fanTitle.Text = 'iDRAC fan speeds'
$form.Controls.Add($fanTitle)

$fanGrid = New-Object System.Windows.Forms.DataGridView
$fanGrid.Location = New-Object System.Drawing.Point(22, 258)
$fanGrid.Size = New-Object System.Drawing.Size(666, 155)
$fanGrid.ReadOnly = $true
$fanGrid.AllowUserToAddRows = $false
$fanGrid.AllowUserToDeleteRows = $false
$fanGrid.AllowUserToResizeRows = $false
$fanGrid.RowHeadersVisible = $false
$fanGrid.AutoSizeColumnsMode = [System.Windows.Forms.DataGridViewAutoSizeColumnsMode]::Fill
[void] $fanGrid.Columns.Add('Name', 'Fan')
[void] $fanGrid.Columns.Add('Reading', 'Speed')
[void] $fanGrid.Columns.Add('Status', 'Status')
$form.Controls.Add($fanGrid)

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(22, 430)
$status.Size = New-Object System.Drawing.Size(666, 42)
$status.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$status.ForeColor = [System.Drawing.Color]::DimGray
$status.Text = 'Waiting for first sample…'
$form.Controls.Add($status)

$refreshButton = New-Object System.Windows.Forms.Button
$refreshButton.Location = New-Object System.Drawing.Point(590, 475)
$refreshButton.Size = New-Object System.Drawing.Size(98, 28)
$refreshButton.Text = 'Refresh now'
$form.Controls.Add($refreshButton)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = $RefreshSeconds * 1000
 $collectionPollTimer = New-Object System.Windows.Forms.Timer
$collectionPollTimer.Interval = 250
$script:collectionProcess = $null

function Show-CompletedSample {
    param([string] $Json)
    $refreshButton.Enabled = $true
    try {
        $sample = $Json | ConvertFrom-Json
        $gpus = @($sample.gpus)
        $gpuLabels = @($gpu0Value, $gpu1Value)
        $gpuDetails = @($gpu0Detail, $gpu1Detail)
        for ($i = 0; $i -lt $gpuLabels.Count; $i++) {
            if ($i -lt $gpus.Count) {
                $gpu = $gpus[$i]
                $gpuLabels[$i].Text = "$($gpu.temperatureC)°C"
                $gpuLabels[$i].ForeColor = Get-TemperatureColor -TemperatureC ([int] $gpu.temperatureC)
                $gpuDetails[$i].Text = "$($gpu.name) • $($gpu.powerDrawW) W / $($gpu.powerLimitW) W"
            }
            else {
                $gpuLabels[$i].Text = 'Not detected'
                $gpuLabels[$i].ForeColor = [System.Drawing.Color]::DimGray
                $gpuDetails[$i].Text = ''
            }
        }

        $temperatures = @($sample.idrac.temperatures)
        $inlet = $temperatures | Where-Object { $_.name -eq 'Inlet Temp' } | Select-Object -First 1
        $exhaust = $temperatures | Where-Object { $_.name -eq 'Exhaust Temp' } | Select-Object -First 1
        $inletValue.Text = if ($inlet) { $inlet.reading } else { '--' }
        $exhaustValue.Text = if ($exhaust) { $exhaust.reading } else { '--' }

        $fanGrid.Rows.Clear()
        foreach ($fan in @($sample.idrac.fans)) {
            [void] $fanGrid.Rows.Add($fan.name, $fan.reading, $fan.status)
        }
        $status.ForeColor = [System.Drawing.Color]::ForestGreen
        $status.Text = "Last successful sample: $($sample.timestampUtc) UTC. Monitoring only; iDRAC fan mode was not changed."
    }
    catch {
        $status.ForeColor = [System.Drawing.Color]::Firebrick
        $status.Text = "Sample could not be displayed: $($_.Exception.Message)"
    }
}

$startCollection = {
    if ($script:collectionProcess -and -not $script:collectionProcess.HasExited) { return }

    try {
        $powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $powerShellPath
        $startInfo.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$collectorPath`" -IdracHost `"$IdracHost`" -IdracUser `"$IdracUser`" -IpmiToolPath `"$IpmiToolPath`""
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.EnvironmentVariables['BLACKLOTUS_IDRAC_PASSWORD'] = $env:BLACKLOTUS_IDRAC_PASSWORD

        $script:collectionProcess = New-Object System.Diagnostics.Process
        $script:collectionProcess.StartInfo = $startInfo
        if (-not $script:collectionProcess.Start()) {
            throw 'The telemetry collector process could not start.'
        }
        $refreshButton.Enabled = $false
        $status.ForeColor = [System.Drawing.Color]::DimGray
        $status.Text = 'Collecting NVIDIA and iDRAC telemetry…'
        $collectionPollTimer.Start()
    }
    catch {
        $refreshButton.Enabled = $true
        $status.ForeColor = [System.Drawing.Color]::Firebrick
        $status.Text = "Could not start collection: $($_.Exception.Message)"
    }
}

$checkCollection = {
    $process = $script:collectionProcess
    if ($null -eq $process -or -not $process.HasExited) { return }

    $collectionPollTimer.Stop()
    $standardOutput = $process.StandardOutput.ReadToEnd()
    $standardError = $process.StandardError.ReadToEnd()
    $exitCode = $process.ExitCode
    $process.Dispose()
    $script:collectionProcess = $null

    if ($exitCode -ne 0) {
        $refreshButton.Enabled = $true
        $status.ForeColor = [System.Drawing.Color]::Firebrick
        $status.Text = "Sample failed: $($standardError.Trim())"
        return
    }
    Show-CompletedSample -Json $standardOutput
}

$refresh = {
    & $startCollection
}

$timer.Add_Tick($refresh)
$collectionPollTimer.Add_Tick($checkCollection)
$refreshButton.Add_Click($refresh)
$form.Add_Shown({ & $refresh; $timer.Start() })
$form.Add_FormClosing({
    $timer.Stop()
    $collectionPollTimer.Stop()
    if ($script:collectionProcess -and -not $script:collectionProcess.HasExited) {
        $script:collectionProcess.Kill()
    }
})
[void] $form.ShowDialog()
