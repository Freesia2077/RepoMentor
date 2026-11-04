$ErrorActionPreference = 'Stop'
$RepoMentorPython = 'D:\anaconda\envs\repomentor\python.exe'
$DataRoot = & $RepoMentorPython -s -c 'from repomentor.paths import data_root; print(data_root())'
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Python data directory' }
$ToolsRoot = Join-Path ([IO.Path]::GetFullPath($DataRoot)) 'tools'
$Archive = Join-Path $ToolsRoot 'ollama-v0.34.3-windows-amd64.zip'
$Destination = Join-Path $ToolsRoot 'ollama-v0.34.3'
$ExpectedHash = '306ce9e81e3491d147f558e60d7a389499f244d10f71859c6e4e899241d1b4ae'
New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $Archive)) {
    $Partial = "$Archive.partial"
    & curl.exe --fail --location --retry 2 --output $Partial 'https://github.com/ollama/ollama/releases/download/v0.34.3/ollama-windows-amd64.zip'
    if ($LASTEXITCODE -ne 0) { throw 'Official archive download failed; partial file retained.' }
    if ((Get-FileHash -LiteralPath $Partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) { throw 'Archive SHA256 mismatch' }
    Move-Item -LiteralPath $Partial -Destination $Archive
}
if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) { throw 'Archive SHA256 mismatch' }
if (-not (Test-Path -LiteralPath (Join-Path $Destination 'ollama.exe'))) {
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination
}
Write-Output "Pinned Ollama v0.34.3 is available at $Destination"
