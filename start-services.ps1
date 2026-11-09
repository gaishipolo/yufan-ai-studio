# Start the workbench services in windows that belong to YOUR desktop session.
# Services started inside an AI session get recycled when that session ends,
# which is what produced the "Request failed with status code 500" errors.
#
# Usage (run from your own terminal):
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1 -WithBrowser
param([switch]$WithBrowser)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$api = Join-Path $root 'api'
$studio = Join-Path $root 'studio'
$app = Join-Path $api '.venv\Scripts\app.exe'

function Test-Http([string]$url) {
    try {
        $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-Path $app)) { throw "Backend executable not found: $app" }

if (Test-Http 'http://127.0.0.1:8080/api/v1/health') {
    Write-Host 'Backend already running on 8080; reusing it.'
} else {
    Start-Process -FilePath $app -ArgumentList @('serve', '--host', '127.0.0.1', '--port', '8080') -WorkingDirectory $api
    Write-Host 'Backend starting: http://127.0.0.1:8080'
}

if (Test-Http 'http://127.0.0.1:5173/') {
    Write-Host 'Frontend already running on 5173; reusing it.'
} else {
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', 'npm', 'run', 'dev', '--', '--host', '127.0.0.1') -WorkingDirectory $studio
    Write-Host 'Frontend starting: http://127.0.0.1:5173'
}

if ($WithBrowser) {
    $chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
    $profile = Join-Path $api 'var\libtv-chrome-profile'
    if (Test-Http 'http://127.0.0.1:9226/json/version') {
        Write-Host 'LibTV browser already online on CDP 9226.'
    } elseif (Test-Path $chrome) {
        Start-Process -FilePath $chrome -ArgumentList @(
            '--remote-debugging-port=9226',
            '--remote-allow-origins=*',
            "--user-data-dir=$profile",
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-blink-features=AutomationControlled',
            'https://www.liblib.tv/'
        )
        Write-Host 'LibTV browser starting on CDP 9226 (profile keeps the login).'
    } else {
        Write-Warning 'Chrome not found; start the LibTV browser manually.'
    }
}

Write-Host 'Done. Keep these windows open - closing them stops the services.'
