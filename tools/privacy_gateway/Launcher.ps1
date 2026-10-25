$ErrorActionPreference = 'Stop'
$distro = 'Ubuntu'
$repo = '/home/serveradmin/Role-AI'
$state = "$repo/.codex-local/privacy-gateway"
try {
    & wsl.exe -d $distro -u serveradmin -- python3 "$repo/tools/privacy_gateway/gateway.py" --init
    if ($LASTEXITCODE -ne 0) { throw 'Local privacy initialization failed.' }
    $token = (& wsl.exe -d $distro -u serveradmin -- cat "$state/access-token" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $token -notmatch '^[a-f0-9]{64}$') { throw 'Cannot read the local access token.' }
    function Ready {
        try {
            $req = [Net.HttpWebRequest]::Create('http://127.0.0.1:8124/status')
            $req.Proxy = $null; $req.Timeout = 2000; $req.Method = 'POST'
            $req.ContentType = 'application/json'; $req.Headers.Add('Authorization', 'Bearer ' + $token)
            $bytes = [Text.Encoding]::UTF8.GetBytes('{}'); $req.ContentLength = $bytes.Length
            $stream = $req.GetRequestStream(); $stream.Write($bytes,0,$bytes.Length); $stream.Close()
            $response = $req.GetResponse(); $response.Close(); return $true
        } catch { return $false }
    }
    if (-not (Ready)) {
        Start-Process wsl.exe -WindowStyle Minimized -ArgumentList @('-d',$distro,'-u','serveradmin','--','python3',"$repo/tools/privacy_gateway/gateway.py")
        $ready = $false
        for ($i=0; $i -lt 15; $i++) { if (Ready) { $ready=$true; break }; Start-Sleep -Seconds 1 }
        if (-not $ready) { throw 'Privacy gateway is not reachable. Check the minimized WSL window; port 8124 may be occupied.' }
    }
    Start-Process ('http://127.0.0.1:8124/#token=' + $token)
    Write-Host 'Local privacy review opened. Keep the minimized WSL window open.'
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
