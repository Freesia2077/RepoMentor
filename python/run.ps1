param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PythonArgs)
$ErrorActionPreference = 'Stop'
$RepoMentorPython = 'D:\anaconda\envs\repomentor\python.exe'
if (-not (Test-Path -LiteralPath $RepoMentorPython)) { throw 'The designated RepoMentor Conda interpreter is missing.' }
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
& $RepoMentorPython -s @PythonArgs
exit $LASTEXITCODE
