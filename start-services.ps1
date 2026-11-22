# Start the workbench services in windows that belong to YOUR desktop session.
# Services started inside an AI session get recycled when that session ends,
# which is what produced the "Request failed with status code 500" errors.
#
# Usage (run from your own terminal):
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1 -WithBrowser
#   powershell -ExecutionPolicy Bypass -File .\start-services.ps1 -BrowsersOnly `
#     -Account1Profile D:\yufan-libtv\profile-9411 -Account2Profile D:\yufan-libtv\profile-9412
#
# -BrowsersOnly 是给生产机用的：后端已经按 README §2.4 起好了（它自己发页面，
# 不需要 vite dev），只需要补上 LibTV 的两个登录浏览器。这时 8080 / 5173 一律不碰。
param(
    [switch]$WithBrowser,
    # 只起 CDP 浏览器，跳过后端与前端。隐含 -WithBrowser。
    [switch]$BrowsersOnly,
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
# Invoke-WebRequest 探活默认会画进度条；输出重定向到日志时那些「正在读取 Web 响应」
# 会夹在正常文案中间，读日志时反而看不清到底起没起。
$ProgressPreference = 'SilentlyContinue'
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

if (-not $BrowsersOnly) {
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
}

if ($WithBrowser -or $BrowsersOnly) {
    if (-not $Account1Profile) { $Account1Profile = Join-Path $api 'var\libtv-chrome-profile' }

    # 生产机上不要落进「另一个项目的目录」这个开发机默认值：没显式传就用本仓库 var\ 下的。
    # 两个目录必须不同 —— 登录态按目录存，复用同一个目录等于两个账号变同一个账号。
    if ($BrowsersOnly -and (-not $PSBoundParameters.ContainsKey('Account2Profile') -or -not $Account2Profile)) {
        $Account2Profile = Join-Path $api 'var\libtv-chrome-profile-2'
    }

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
            Write-Warning "Chrome not found at $Chrome; start the LibTV browser on CDP $port manually (profile: $($account.Profile))."
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

if ($BrowsersOnly) {
    Write-Host 'Done. Keep both Chrome windows open - closing one takes that account offline.'
    Write-Host 'Note: logging off Windows also kills them; an RDP disconnect (not logoff) is fine.'
} else {
    Write-Host 'Done. Keep these windows open - closing them stops the services.'
}
