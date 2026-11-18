# Start the workbench services in windows that belong to YOUR desktop session.
# Services started inside an AI session get recycled when that session ends,
# which is what produced the "Request failed with status code 500" errors.
#
# Usage (run from your own terminal):
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1 -WithBrowser
param(
    [switch]$WithBrowser,
    # 浏览器可执行文件。Chrome 升级后路径变了就传这个参数，别改脚本。
    [string]$Chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe',
    # 两个 LibTV 账号各自的 Chrome 配置目录。**必须分开**：同一个目录只能被
    # 一个 Chrome 进程占用，而且登录态就是按目录存的 —— 用同一个目录等于两个
    # 账号其实是同一个账号。
    [string]$Account1Profile = '',
    # 账号二的目录沿用已经登录过的那个（video_analysis 项目建的），
    # 换新目录会让 9412 变成「未登录」，得重新扫码一次。
    [string]$Account2Profile = 'D:\python_file\video_analysis\.browser-profile-account2-9412'
)

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
    if (-not $Account1Profile) { $Account1Profile = Join-Path $api 'var\libtv-chrome-profile' }

    # 端口必须与 api\.env 里的 LIBTV_BROWSER_URLS / LIBTV_CREDITS_BROWSER_URL 一致：
    # 9411 = 免费队列账号（积分快用完，但 0 积分畅用队列照常），
    # 9412 = 积分队列账号（LIBTV_CREDITS_BROWSER_URL 指向它）。
    $accounts = @(
        @{ Port = 9411; Profile = $Account1Profile; Note = 'free queue' },
        @{ Port = 9412; Profile = $Account2Profile; Note = 'credits queue' }
    )

    foreach ($account in $accounts) {
        $port = $account.Port
        if (Test-Http "http://127.0.0.1:$port/json/version") {
            Write-Host "LibTV account on CDP $port already online; reusing it."
            continue
        }
        if (-not (Test-Path $Chrome)) {
            Write-Warning "Chrome not found at $Chrome; start the LibTV browser on CDP $port manually."
            continue
        }
        if (-not (Test-Path $account.Profile)) {
            # 不影响启动，但要说清楚：新目录里没有 Cookie，第一次必须人工登录。
            Write-Warning "Profile $($account.Profile) is new - sign in to LibTV once after it opens."
        }
        Start-Process -FilePath $Chrome -ArgumentList @(
            "--remote-debugging-port=$port",
            '--remote-allow-origins=*',
            "--user-data-dir=$($account.Profile)",
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-blink-features=AutomationControlled',
            'https://www.liblib.tv/'
        )
        Write-Host "LibTV account starting on CDP $port ($($account.Note)); profile: $($account.Profile)"
    }
}

Write-Host 'Done. Keep these windows open - closing them stops the services.'
