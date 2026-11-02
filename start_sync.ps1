param(
    [string]$PythonPath = '',
    [ValidateSet('Preview','Check','Status','Reuse')][string]$Mode = 'Preview',
    [string]$RunId = '',
    [string]$StateDir = ''
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not $PythonPath) {
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    $PythonPath = @(
        "$PSScriptRoot\.venv\Scripts\python.exe"
        "$desktopPath\mongodb_api_server_bundle\mongodb_api\.venv\Scripts\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $PythonPath -or -not (Test-Path $PythonPath)) { throw 'Use -PythonPath with your existing Python executable.' }
$commandArgs = @('-m', 'product_sync.sync')
if ($StateDir) { $commandArgs += @('--state-dir', $StateDir) }
switch ($Mode) {
    'Check' { $commandArgs += '--check' }
    'Status' { $commandArgs += '--status' }
    'Reuse' {
        if (-not $RunId) { throw 'Reuse requires -RunId.' }
        $commandArgs += @('--reuse-snapshot', $RunId)
    }
}
# No apply mode: this launcher is for the current Windows read-only test phase.
& $PythonPath @commandArgs
exit $LASTEXITCODE
