param([ValidateSet('start', 'status', 'stop')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$RepoMentorPython = 'D:\anaconda\envs\repomentor\python.exe'
$DataRoot = & $RepoMentorPython -s -c 'from repomentor.paths import data_root; print(data_root())'
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Python data directory' }
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$Binary = Join-Path $DataRoot 'tools/ollama-v0.34.3/ollama.exe'
$StateDir = Join-Path $DataRoot 'ollama-service'
$PidFile = Join-Path $StateDir 'server.pid'
$ModelHost = '127.0.0.1:11435'
$ManagedProcess = $null
if (Test-Path -LiteralPath $PidFile) {
    $SavedPid = [int](Get-Content -LiteralPath $PidFile -Raw)
    $ManagedProcess = Get-Process -Id $SavedPid -ErrorAction SilentlyContinue
    if ($ManagedProcess -and $ManagedProcess.Path -ne $Binary) { throw 'PID belongs to a different executable' }
}
if ($Action -eq 'stop') {
    if ($ManagedProcess) {
        $RunnerBinary = Join-Path $DataRoot 'tools/ollama-v0.34.3/lib/ollama/llama-server.exe'
        # The pinned Ollama runner survives terminating its parent on Windows.
        # Capture only our direct runner children before stopping the managed server.
        $ManagedRunners = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($ManagedProcess.Id)" |
            Where-Object { $_.ExecutablePath -eq $RunnerBinary })
        Stop-Process -Id $ManagedProcess.Id
        foreach ($ManagedRunner in $ManagedRunners) {
            $RunnerProcess = Get-Process -Id $ManagedRunner.ProcessId -ErrorAction SilentlyContinue
            if ($RunnerProcess -and $RunnerProcess.Path -eq $RunnerBinary) {
                Stop-Process -Id $RunnerProcess.Id
            }
        }
    }
    if (Test-Path -LiteralPath $PidFile) { Remove-Item -LiteralPath $PidFile }
    Write-Output 'RepoMentor local Ollama stopped.'
    exit 0
}
if ($Action -eq 'start' -and -not $ManagedProcess) {
    if (-not (Test-Path -LiteralPath $Binary)) { throw 'Extract the pinned official Ollama archive into artifacts/tools/ollama-v0.34.3 first.' }
    $Listener = Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue
    if ($Listener) { throw 'Port 11435 is already occupied; refusing to reuse an unrelated service.' }
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    $PrivateProfile = Join-Path $StateDir 'profile'
    New-Item -ItemType Directory -Path $PrivateProfile -Force | Out-Null
    $Settings = @{
        OLLAMA_HOST = $ModelHost; OLLAMA_MODELS = (Join-Path $DataRoot 'models/ollama')
        OLLAMA_NUM_PARALLEL = '1'; OLLAMA_MAX_LOADED_MODELS = '1'; OLLAMA_CONTEXT_LENGTH = '16384'
        OLLAMA_KEEP_ALIVE = '5m'; OLLAMA_NO_CLOUD = '1'; USERPROFILE = $PrivateProfile
        LLAMA_ARG_FIT_TARGET = '2048'
    }
    $Previous = @{}
    try {
        foreach ($Key in $Settings.Keys) {
            $Previous[$Key] = [Environment]::GetEnvironmentVariable($Key, 'Process')
            [Environment]::SetEnvironmentVariable($Key, $Settings[$Key], 'Process')
        }
        $ManagedProcess = Start-Process -FilePath $Binary -ArgumentList 'serve' -WorkingDirectory $StateDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $StateDir 'stdout.log') -RedirectStandardError (Join-Path $StateDir 'stderr.log')
        Set-Content -LiteralPath $PidFile -Value $ManagedProcess.Id
    } finally {
        foreach ($Key in $Previous.Keys) { [Environment]::SetEnvironmentVariable($Key, $Previous[$Key], 'Process') }
    }
}
if ($ManagedProcess) {
    if ($Action -eq 'start') {
        $Ready = $false
        for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
            try {
                # Loopback must bypass the owner's system/environment proxy.
                $VersionText = & curl.exe --noproxy '*' --silent --fail --connect-timeout 1 --max-time 2 "http://$ModelHost/api/version"
                if ($LASTEXITCODE -ne 0) { throw 'Local service is not responding yet' }
                $Version = $VersionText | ConvertFrom-Json
                if ($Version.version -ne '0.34.3') { throw 'Unexpected server version' }
                $Ready = $true
                break
            } catch { Start-Sleep -Milliseconds 500 }
        }
        if (-not $Ready) { throw "Local service did not become ready; inspect $StateDir/stderr.log" }
    }
    [PSCustomObject]@{ pid = $ManagedProcess.Id; host = $ModelHost; dataRoot = $DataRoot }
} else { Write-Output 'RepoMentor local Ollama is not running.' }
