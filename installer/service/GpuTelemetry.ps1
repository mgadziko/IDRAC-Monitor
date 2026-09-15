param([string] $ConfigPath = 'C:\ProgramData\ThermalMonitor\token.txt')

$ErrorActionPreference = 'Stop'
$token = (Get-Content -LiteralPath $ConfigPath -Raw).Trim()
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add('http://+:11436/')
$listener.Start()
try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        try {
            if ($context.Request.HttpMethod -ne 'GET' -or $context.Request.Url.AbsolutePath -ne '/v1/gpu-temperatures' -or $context.Request.Headers['X-ThermalMonitor-Token'] -ne $token) {
                $context.Response.StatusCode = 401
            } else {
                $output = & nvidia-smi --query-gpu=index,name,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits 2>&1
                if ($LASTEXITCODE -ne 0) { throw "nvidia-smi failed: $output" }
                $bytes = [Text.Encoding]::UTF8.GetBytes(($output -join [Environment]::NewLine))
                $context.Response.StatusCode = 200
                $context.Response.ContentType = 'text/plain; charset=utf-8'
                $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            }
        } catch {
            $context.Response.StatusCode = 500
        } finally {
            $context.Response.Close()
        }
    }
} finally {
    $listener.Close()
}
