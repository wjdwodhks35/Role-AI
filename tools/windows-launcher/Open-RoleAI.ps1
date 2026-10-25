param([ValidateSet('desktop', 'browser')][string]$Mode = 'desktop')
$ErrorActionPreference = 'Stop'
$url = 'http://127.0.0.1:8000'
if ($Mode -eq 'browser') {
    Start-Process $url
    return
}
$edgePaths = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $edge) { throw 'Role-AI 전용 창을 열려면 설치된 Microsoft Edge가 필요합니다. 일반 브라우저를 사용하려면 settings.json의 WindowMode를 browser로 변경하세요.' }
$profile = Join-Path $env:LOCALAPPDATA 'RoleAI\DesktopProfile'
Start-Process -FilePath $edge -ArgumentList @(
    "--app=$url",
    ('--user-data-dir="' + $profile + '"'),
    '--no-first-run',
    '--no-default-browser-check'
)
Write-Host 'Role-AI 전용 창 열기를 요청했습니다.'
