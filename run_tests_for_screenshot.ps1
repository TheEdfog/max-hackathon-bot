$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Virtual environment was not found: $PythonPath"
}

$ReportDir = Join-Path $ProjectRoot "docs\test_reports"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ReportPath = Join-Path $ReportDir "pytest_report_$Timestamp.txt"

Write-Host "Running pytest in verbose mode..."
Write-Host "Report will be saved to: $ReportPath"
Write-Host ""

$env:PYTHONUTF8 = "1"
$Output = & $PythonPath -m pytest tests -vv --tb=short -p no:cacheprovider 2>&1
$ExitCode = $LASTEXITCODE

$Output | Tee-Object -FilePath $ReportPath

Write-Host ""
Write-Host "Saved report: $ReportPath"
exit $ExitCode
