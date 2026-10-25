param([switch]$CheckOnly, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$exitCode = 0
function Invoke-Wsl([string]$UserName, [string[]]$CommandArgs) {
    & wsl.exe -d $script:distro -u $UserName -- @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed (exit $LASTEXITCODE). See the message above." }
}
function Invoke-WslInput([string]$UserName, [string]$LinuxCommand, [string]$Content) {
    if ($script:distro -notmatch '^[A-Za-z0-9_.-]+$' -or $UserName -notmatch '^[a-z0-9_-]+$') { throw 'Invalid WSL name.' }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'wsl.exe'
    $info.Arguments = '-d {0} -u {1} -- {2}' -f $script:distro, $UserName, $LinuxCommand
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $process = [Diagnostics.Process]::Start($info)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content.Replace("`r`n", "`n"))
    $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $process.StandardInput.Close()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "WSL input command failed (exit $($process.ExitCode))." }
}
function Test-Http([string]$Url) {
    try {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Proxy = $null
        $request.Timeout = 3000
        $response = $request.GetResponse()
        $response.Close()
        return $true
    } catch { return $false }
}
function Start-InstalledApp([string]$Pattern, [string]$Label) {
    $apps = @(Get-StartApps | Where-Object { $_.Name -match $Pattern })
    if ($apps.Count -eq 1) {
        Start-Process explorer.exe -ArgumentList ('shell:AppsFolder\' + $apps[0].AppID)
        Write-Host "$Label start requested (connection is checked separately)."
    } else { Write-Warning "$Label could not be uniquely identified. Start the installed app manually if needed." }
}
try {
    $settings = Get-Content -Raw -LiteralPath (Join-Path $root 'settings.json') | ConvertFrom-Json
    $distros = @(& wsl.exe --list --quiet | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list WSL distributions.' }
    if ($settings.Distro) {
        if ($settings.Distro -notin $distros) { throw 'The configured WSL distribution is not registered.' }
        $script:distro = $settings.Distro
    } else {
        $foundDistros = @()
        foreach ($candidate in $distros) {
            if ($candidate -notmatch '^Ubuntu') { continue }
            & wsl.exe -d $candidate -u root -- test -f /root/Role-AI/compose.yaml
            if ($LASTEXITCODE -eq 0) { $foundDistros += $candidate }
        }
        if ($foundDistros.Count -ne 1) { throw 'Cannot uniquely locate Role-AI in Ubuntu. Set Distro in settings.json.' }
        $script:distro = $foundDistros[0]
    }
    Write-Host "WSL distribution: $script:distro"
    # Read the Windows files through stdin: no UNC path or shell quoting is needed.
    if ($CheckOnly) {
        foreach ($name in @('start-services.sh', 'gateway-session.sh')) {
            Invoke-WslInput serveradmin 'bash -n' (Get-Content -Raw -LiteralPath (Join-Path $root $name))
        }
        Invoke-Wsl root @('test', '-f', '/root/Role-AI/compose.override.yaml')
        Invoke-Wsl root @('test', '-f', '/etc/role-ai/openclaw/compose.yaml')
        Invoke-Wsl serveradmin @('test', '-x', '/home/serveradmin/.openclaw/bin/openclaw')
        Write-Host 'Preflight passed. No services or Windows apps were started.'
    } else {
        Start-InstalledApp '(?i)^Ollama$' 'Ollama'
        Invoke-WslInput root 'bash -s' (Get-Content -Raw -LiteralPath (Join-Path $root 'start-services.sh'))
        $gatewayHealthy = $false
        try { & wsl.exe -d $script:distro -u serveradmin -- env OPENCLAW_CONFIG_PATH=/home/serveradmin/.openclaw/windows-control.json OPENCLAW_STATE_DIR=/home/serveradmin/.openclaw/windows-control-state /home/serveradmin/.openclaw/bin/openclaw health 1>$null 2>$null
            $gatewayHealthy = ($LASTEXITCODE -eq 0)
        } catch { $gatewayHealthy = $false }
        if (-not $gatewayHealthy) {
            # Install only the launcher script, never the existing Gateway configuration.
            $gatewayScript = Get-Content -Raw -LiteralPath (Join-Path $root 'gateway-session.sh')
            $installScript = "umask 077`nmkdir -p /home/serveradmin/.openclaw/windows-control-state/launcher`ncat > /home/serveradmin/.openclaw/windows-control-state/launcher/gateway-session.sh <<'ROLEAI_GATEWAY_SCRIPT'`n" + $gatewayScript + "`nROLEAI_GATEWAY_SCRIPT`n"
            Invoke-WslInput serveradmin 'bash -s' $installScript
            Start-Process wsl.exe -WindowStyle Minimized -ArgumentList @('-d', $script:distro, '-u', 'serveradmin', '--', 'bash', '/home/serveradmin/.openclaw/windows-control-state/launcher/gateway-session.sh')
            Write-Host 'PC Gateway를 시작합니다. 최소화된 WSL 창은 닫지 마세요.'
        }
        $ready = $false
        for ($i = 0; $i -lt 30; $i++) {
            if (Test-Http 'http://127.0.0.1:18790/') { $ready = $true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $ready) { throw 'PC Gateway is not reachable. Check its WSL window and windows-control-state/launcher/gateway.log.' }
        Invoke-Wsl serveradmin @('env', 'OPENCLAW_CONFIG_PATH=/home/serveradmin/.openclaw/windows-control.json', 'OPENCLAW_STATE_DIR=/home/serveradmin/.openclaw/windows-control-state', '/home/serveradmin/.openclaw/bin/openclaw', 'health')
        if (-not (Test-Http 'http://127.0.0.1:8000/api/health')) { throw 'Windows cannot reach Role-AI on localhost:8000.' }
        Start-InstalledApp '(?i)^OpenClaw Companion$' 'OpenClaw Companion'
        try { Invoke-WslInput serveradmin 'python3 -' (Get-Content -Raw -LiteralPath (Join-Path $root 'check-ollama.py')) }
        catch { Write-Warning 'Ollama needs attention; no configuration was changed.' }
        Write-Host 'Role-AI 서버와 PC Gateway에 Windows에서 접속할 수 있습니다.'
        try { Invoke-WslInput serveradmin 'python3 -' (Get-Content -Raw -LiteralPath (Join-Path $root 'check-node.py')) }
        catch { Write-Warning 'Role-AI 서버는 사용할 수 있습니다. PC 제어를 사용하려면 Companion을 다시 연결하세요.' }
        Write-Host 'AI를 통한 Windows 명령 실행은 별도 검증이 필요합니다.'
        if ($settings.OpenBrowser -and -not $NoBrowser) {
            $windowMode = 'desktop'
            if ($settings.PSObject.Properties.Name -contains 'WindowMode') { $windowMode = $settings.WindowMode }
            & (Join-Path $root 'Open-RoleAI.ps1') -Mode $windowMode
        }
    }
} catch {
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
exit $exitCode
