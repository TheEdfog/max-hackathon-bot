param(
    [switch]$SkipLatex,
    [switch]$RunTests,
    [switch]$StartServer
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-FirstExistingPath {
    param([string[]]$Paths)
    foreach ($path in $Paths) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            return $path
        }
    }
    return $null
}

Write-Step "Checking Python"
$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "Python 3.10+ was not found in PATH. Install Python and try again."
}

& $PythonCommand.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.10+ is required."
}

Write-Step "Creating virtual environment"
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $PythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv."
    }
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Step "Installing Python dependencies"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}

& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Python dependencies from requirements.txt."
}

Write-Step "Preparing local configuration"
if ((-not (Test-Path -LiteralPath ".env")) -and (Test-Path -LiteralPath ".env.example")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "Created .env from .env.example. Fill DEEPSEEK_API_KEY before using live generation." -ForegroundColor Yellow
}
else {
    Write-Host ".env already exists or .env.example is missing."
}

Write-Step "Preparing runtime directories"
$RuntimeDirectories = @(
    "storage",
    "storage\attachments",
    "storage\generated",
    "storage\temp",
    "storage\vacancy_raw"
)
foreach ($directory in $RuntimeDirectories) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

if (-not $SkipLatex) {
    Write-Step "Installing/checking LaTeX packages"
    $MpmCandidates = @()
    if ($env:LOCALAPPDATA) {
        $MpmCandidates += Join-Path $env:LOCALAPPDATA "Programs\MiKTeX\miktex\bin\x64\mpm.exe"
    }
    if ($env:ProgramFiles) {
        $MpmCandidates += Join-Path $env:ProgramFiles "MiKTeX\miktex\bin\x64\mpm.exe"
    }
    if (${env:ProgramFiles(x86)}) {
        $MpmCandidates += Join-Path ${env:ProgramFiles(x86)} "MiKTeX\miktex\bin\x64\mpm.exe"
    }

    $MpmPath = Find-FirstExistingPath $MpmCandidates
    if (-not $MpmPath) {
        $MpmCommand = Get-Command mpm -ErrorAction SilentlyContinue
        if ($MpmCommand) {
            $MpmPath = $MpmCommand.Source
        }
    }

    if (-not $MpmPath) {
        Write-Warning "MiKTeX mpm.exe was not found. PDF export needs MiKTeX or another xelatex installation."
    }
    elseif (Test-Path -LiteralPath "requirements-latex.txt") {
        $Packages = Get-Content -LiteralPath "requirements-latex.txt" |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and (-not $_.StartsWith("#")) }

        foreach ($package in $Packages) {
            Write-Host "Installing/checking LaTeX package: $package"
            & $MpmPath "--install=$package"
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Package '$package' was not installed cleanly. It may already be installed or unavailable in the configured MiKTeX repository."
            }
        }

        $InitexmfPath = Join-Path (Split-Path -Parent $MpmPath) "initexmf.exe"
        if (Test-Path -LiteralPath $InitexmfPath) {
            & $InitexmfPath --update-fndb
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "MiKTeX file database update failed. Close MiKTeX windows/processes and run this script again if PDF build cannot find packages."
            }
        }
    }
}

Write-Step "Environment notes"
Write-Host "Check PostgreSQL and DATABASE_URL in .env before starting the server."
Write-Host "Check DEEPSEEK_API_KEY in .env if live DeepSeek generation is needed."

if ($RunTests) {
    Write-Step "Running tests"
    & $VenvPython -m pytest tests -q
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed."
    }
}

if ($StartServer) {
    Write-Step "Starting server"
    & $VenvPython -m uvicorn apps.web.main:app --host 127.0.0.1 --port 8000
}
else {
    Write-Step "Done"
    Write-Host "Start the server with:"
    Write-Host "  .\setup_windows.ps1 -StartServer"
    Write-Host "or:"
    Write-Host "  .\.venv\Scripts\python.exe -m uvicorn apps.web.main:app --host 127.0.0.1 --port 8000"
}
